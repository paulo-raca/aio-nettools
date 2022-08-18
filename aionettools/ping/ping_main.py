from __future__ import annotations
import asyncio
from collections import defaultdict
from datetime import datetime
import itertools
import random
import socket
import struct
from asyncio import Future
from dataclasses import dataclass, field
from enum import Enum, auto
from ipaddress import IPv4Address, IPv6Address, _BaseAddress
from socket import AddressFamily
from timeit import default_timer as timer
from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Tuple
from yarl import URL

import more_itertools
import typer

from aionettools.elastic import ElasticDump, ElasticDump_

from aionettools.util import IPVersion, async_command, autocomplete, parse_interval, resolve_addresses, test_hostnames
from aionettools.ping.ping_stats import PingStatistics
from aionettools.ping.ping_progress import PingProgressBar
from aionettools.ping.ping import Ping, PingResult


async def ping_pretty(
    hostnames: Iterable[str],
    count: Optional[int] = None,
    duration: Optional[float] = None,
    interval: Optional[float] = None,
    window: Optional[float] = None,
    timeout: float = 1,
    audible: bool = False,
    flood: bool = False,
    verbose: bool = True,
    show_ips: bool = False,
    elastic: ElasticDump_ = ElasticDump_.NOOP,
) -> PingStatistics.Summary:
    hostnames = set(hostnames)
    host_addr = dict(zip(hostnames, await asyncio.gather(*map(resolve_addresses, hostnames))))
    if show_ips:
        for host, addresses in host_addr.items():
            print(f"Resolved {host}: {', '.join(map(str, addresses))}")
    host_addr_iterator = more_itertools.interleave(
        *[zip(itertools.repeat(hostname), itertools.cycle(addresses)) for hostname, addresses in host_addr.items()]
    )

    if window is None and count is None and duration is None:
        window = 5

    with PingProgressBar(hostnames, window=window, count=count or 0) as bar:
        result_statistics = PingStatistics(window=window)
        statistics: MutableMapping[Any, PingStatistics] = defaultdict(lambda: PingStatistics(window=window))
        async with Ping() as ping:
            def callback(future: Future[PingResult]):
                result = future.result()
                if audible:
                    print("\a", end="", flush=True)
                if flood:
                    print("\b \b", end="", flush=True)
                if verbose and not flood:
                    print(f"{result.hostname} ({result.addr}): icmp_seq={result.seq}, time={result.elapsed * 1000:.1f} ms, {result.status.name}")

                def ping_summary(by_host: bool = False, by_ip_version: bool = False, by_ip_address: bool = False):
                    key = []
                    if by_host:
                        key.append(result.hostname)
                    if by_ip_address:
                        key.append(str(result.addr))
                        by_ip_version = True
                    if by_ip_version:
                        key.append(IPVersion.from_address(result.addr))
                    summary = statistics[tuple(key)].summary
                    count = sum(summary.status_count.values())
                    ret = {
                        "@timestamp": result.timestamp.isoformat(),
                        "summary": True, 
                        "hostname": result.hostname if by_host else None,
                        "ip_version": IPVersion.from_address(result.addr).name if by_ip_version else None,
                        "ip_address": str(result.addr) if by_ip_address else None,
                        "loss": summary.loss,
                        "latency": summary.elapsed_mean,
                        "latency_stddev": summary.elapsed_std,
                        "latency_quantiles": summary.elapsed_quantiles,
                        "count": sum(summary.status_count.values()),
                        "status": {
                            status.name: n / count if n > 0 else 0
                            for status, n in summary.status_count.items()
                        },
                    }
                    return ret

                #elastic.log("ping", ping_summary())
                #elastic.log("ping", ping_summary(by_host=True))
                #elastic.log("ping", ping_summary(by_host=True, by_ip_version=True))
                #elastic.log("ping", ping_summary(by_host=True, by_ip_address=True))
                elastic.log("ping", {
                        "@timestamp": result.timestamp.isoformat(),
                        "summary": False,
                        "hostname": result.hostname,
                        "ip_version": IPVersion.from_address(result.addr).name,
                        "ip_address": str(result.addr),
                        "loss": 0.0 if result.status == PingResult.Status.SUCCESS else 1.0 if result.status in (PingResult.Status.TIMEOUT, PingResult.Status.UNREACHABLE) else None,
                        "latency": result.elapsed if result.status == PingResult.Status.SUCCESS else None,
                        "count": 1,
                        "status": {result.status.name: 1},
                    })

            if interval is None:
                interval = 0.005 if flood else 0.25
            total_count = count * len(hostnames) if count is not None else None
            total_sent = 0
            
            end = timer() + duration if duration is not None else None
            while (total_count is None or total_sent < total_count) and (end is None or timer() < end):
                hostname, addr = next(host_addr_iterator)
                if total_sent > 0:
                    await asyncio.sleep(interval)
                total_sent += 1

                if flood:
                    print(".", end="", flush=True)
                ping_result = ping.ping(addr, timeout, hostname=hostname)
                ping_result._future.add_done_callback(callback)
                bar.add_ping(hostname, ping_result)
                result_statistics.add_ping(ping_result)

            await ping.wait_pending()
            return result_statistics.summary


app = typer.Typer()

@async_command(app, "ping")
async def ping_main(
    hostnames: List[str] = typer.Argument(
        ..., metavar="HOST", help="Host to ping", autocompletion=autocomplete(test_hostnames + ["speedtest"], name="hostnames", fast=True)
    ),
    count: Optional[int] = typer.Option(
        None, "--count", "-c", show_default=False, help="Stop after sending COUNT ECHO_REQUEST packets"
    ),
    time: Optional[float] = typer.Option(None, "--time", "-T", help="Stop the test after TIME seconds"),
    window: Optional[float] = typer.Option(None, "--window", "-W", parser=parse_interval, help="Computes the speed of the last WINDOW seconds"),
    interval: Optional[float] = typer.Option(
        None, "--interval", "-i", parser=parse_interval, help="Wait INTERVAL seconds between sending each packet"
    ),
    timeout: float = typer.Option("1s", "--timeout", "-W", parser=parse_interval, help="Time to wait for a response, in seconds"),
    flood: bool = typer.Option(
        False,
        "--flood",
        "-f",
        help="Flood *ping*. For _every_ ECHO_REQUEST sent a period “.” is printed, while for every ECHO_REPLY received a backspace is printed",
    ),
    audible: bool = typer.Option(False, "--audible", "-a", help="Audible ping"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Do not show results of each ping"),
    show_ips: bool = typer.Option(False, "--show-ips", help="Shows the resolved IP addresses"),
    elastic_url: URL = typer.Option(
        None, "--elastic", help="Stores summary of the tests in the specified ElasticSearch URL"
    ),
):
    if "faang" in hostnames:
        hostnames.remove("faang")
        hostnames += ["facebook.com", "apple.com", "amazon.com", "netflix.com", "google.com"]

    if "speedtest" in hostnames:
        hostnames.remove("speedtest")
        from aiotestspeed.aio import Speedtest

        speedtest = await Speedtest(secure=True)
        hostnames += [host["host"].split(":")[0] for host in await speedtest.get_closest_servers(10)]

    async with ElasticDump(elastic_url) as elastic:
        await ping_pretty(
            hostnames,
            count=count,
            duration=time,
            interval=interval,
            window=window,
            timeout=timeout,
            audible=audible,
            flood=flood,
            verbose=not quiet,
            show_ips=show_ips,
            elastic=elastic,
        )
