import asyncio
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
from typing import Iterable, List, Mapping, Optional, Tuple

import more_itertools
import typer
from typing_extensions import Self

from .util import async_command, autocomplete, resolve_addresses, test_hostnames


@dataclass
class PingResult:
    class Status(Enum):
        SCHEDULED = auto()    # Not sent yet
        PENDING = auto()      # Ping sent
        SUCESS = auto()       # Pong received
        UNREACHABLE = auto()  # Received a Network Unreachable response
        TIMEOUT = auto()      # No response received before reaching the timeout
        CANCELED = auto()     # Aborted before receiving the response

    addr: bytes
    seq: int
    payload: bytes
    start: float = field(default_factory=timer)
    end: Optional[float] = None
    status: Status = Status.PENDING
    _future: Future[Self] = field(default_factory=lambda: asyncio.get_event_loop().create_future())

    @property
    def key(self):
        return (self.seq, self.payload)

    @property
    def elapsed(self) -> float:
        if self.end is None:
            return None
        return self.end - self.start

    def complete(self, status: Status):
        if self.status == PingResult.Status.PENDING:
            self.status = status
            self.end = timer()
            self._future.set_result(self)


class Ping:
    ICMP_ECHO_REQUEST = 8
    ICMP_ECHO_REPLY = 0
    ICMP6_ECHO_REQUEST = 128
    ICMP6_ECHO_REPLY = 129

    def __init__(self) -> None:
        self.echo_seq = 0
        self.loop = asyncio.get_event_loop()
        self.pending_pings: Mapping[int, PingResult] = {}
        self.sockets: Mapping[AddressFamily, socket.socket] = {}
        self.pending_sends: Mapping[AddressFamily, List[Tuple[bytes, _BaseAddress, PingResult]]] = {}

        for family, protocol in [
            (AddressFamily.AF_INET, socket.getprotobyname("icmp")),
            (AddressFamily.AF_INET6, socket.getprotobyname("ipv6-icmp")),
        ]:
            sock = socket.socket(family, socket.SOCK_DGRAM, protocol)
            self.sockets[family] = sock
            self.pending_sends[family] = []
            sock.setblocking(False)
            self.loop.add_reader(sock.fileno(), self.recv_ready, sock)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for sock in self.sockets.values():
            self.loop.remove_reader(sock)
            self.loop.remove_writer(sock)
            sock.close()
        for pending_ping in self.pending_pings.values():
            pending_ping.complete(PingResult.Status.CANCELED)

    def recv_ready(self, sock: socket.socket):
        data, address = sock.recvfrom(4096)
        self.datagram_received(data, address)

    def datagram_received(self, icmp_data, addr):
        type, code, checksum = struct.unpack("!BBH", icmp_data[:4])
        remaining = icmp_data[4:]

        if type in [Ping.ICMP_ECHO_REPLY, Ping.ICMP6_ECHO_REPLY] and code == 0:
            id, seq = struct.unpack("!HH", remaining[:4])
            remaining = remaining[4:]
            payload = remaining
            # print(f"id={id}, seq={seq}, payload={payload}")
            key = (seq, payload)
            pending_ping = self.pending_pings.get(key)
            if pending_ping and pending_ping.payload == payload:
                pending_ping.complete(PingResult.Status.SUCESS)
        else:
            # Discarding unexpected ICMP package
            pass

    def send_ready(self, family: AddressFamily):
        queue = self.pending_sends[family]
        sock = self.sockets[family]
        if len(queue) > 0:
            data, address, result = queue.pop(0)
            try:
                sock.sendto(data, (address.compressed, 0))
            except Exception:
                result.complete(PingResult.Status.UNREACHABLE)
        if len(queue) == 0:
            self.loop.remove_writer(sock.fileno())

    def ping(self, addr: _BaseAddress, timeout: Optional[float] = 1, **kwargs) -> PingResult:
        seq = self.echo_seq
        self.echo_seq = (self.echo_seq + 1) & 0xFFFF

        if isinstance(addr, IPv4Address):
            family = AddressFamily.AF_INET
            icmp_type = Ping.ICMP_ECHO_REQUEST
        elif isinstance(addr, IPv6Address):
            family = AddressFamily.AF_INET6
            icmp_type = Ping.ICMP6_ECHO_REQUEST
        else:
            raise ValueError("Unexpected address type: {addr} -- Should be IPv4Address or IPv6Address")

        header = struct.pack(
            "!BBHHH",
            icmp_type,  # ICMP Type: ECHO_REQUEST
            0,  # ICMP Code: always zero for ping
            0,  # Checksum -- Populated later by the linux kernel
            0,  # Identifier -- Populated later by the linux kernel
            seq,  # Sequence number
        )
        payload = bytes([random.getrandbits(8) for i in range(10)])
        packet = header + payload

        pending_ping = PingResult(addr=addr, seq=seq, payload=payload)
        for key, value in kwargs.items():
            if hasattr(pending_ping, key):
                raise ValueError(f"Cannot specify reserver field '{key}'")
            setattr(pending_ping, key, value)

        self.pending_pings[pending_ping.key] = pending_ping
        sock = self.sockets[family]
        self.pending_sends[family].append((packet, addr, pending_ping))
        if len(self.pending_sends[family]) == 1:
            self.loop.add_writer(sock.fileno(), self.send_ready, family)

        pending_ping._future.add_done_callback(lambda _: self.pending_pings.pop(pending_ping.key))
        if timeout is not None:
            loop = asyncio.get_event_loop()

            def on_timeout():
                pending_ping.complete(PingResult.Status.TIMEOUT)

            timeout_handle = loop.call_later(timeout, on_timeout)
            pending_ping._future.add_done_callback(lambda _: timeout_handle.cancel())
        return pending_ping

    async def wait_pending(self):
        await asyncio.gather(*[pending._future for pending in self.pending_pings.values()], return_exceptions=True)



async def ping_pretty(
    hostnames: Iterable[str],
    count: Optional[int] = None,
    duration: Optional[float] = None,
    interval: Optional[float] = None,
    timeout: float = 1,
    audible: bool = False,
    flood: bool = False,
    verbose: bool = True,
    show_ips: bool = False,
):
    from aionettools.ping_progress import PingProgressBar

    hostnames = set(hostnames)
    host_addr = dict(zip(hostnames, await asyncio.gather(*map(resolve_addresses, hostnames))))
    if show_ips:
        for host, addresses in host_addr.items():
            print(f"Resolved {host}: {', '.join(map(str, addresses))}")
    host_addr_iterator = more_itertools.interleave(
        *[zip(itertools.repeat(hostname), itertools.cycle(addresses)) for hostname, addresses in host_addr.items()]
    )

    retention_time = 5*timeout if count is None and duration is None else None
    print(f"count={count}, timeout={timeout}, retention_time={retention_time}")

    async with Ping() as ping:
        with PingProgressBar(hostnames, retention_time=retention_time, count=count or 0) as bar:

            def callback(future):
                result = future.result()
                if audible:
                    print("\a", end="", flush=True)
                if flood:
                    print("\b \b", end="", flush=True)
                if verbose and not flood:
                    print(f"{result.hostname} ({result.addr}): icmp_seq={result.seq}, time={result.elapsed * 1000:.1f} ms, {result.status.name}")

            if interval is None:
                interval = 0.005 if flood else 0.25
            total_count = count * len(hostnames) if count is not None else None
            interval /= len(hostnames)
            total_sent = 0
            
            end = timer() + duration if duration is not None else None
            print(f"end={end}, now={timer()}")
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

            await ping.wait_pending()




@async_command
async def ping_main(
    hostnames: List[str] = typer.Argument(
        ..., metavar="HOST", help="Host to ping", autocompletion=autocomplete(test_hostnames + ["speedtest"], name="hostnames", fast=True)
    ),
    count: Optional[int] = typer.Option(
        None, "--count", "-c", show_default=False, help="Stop after sending COUNT ECHO_REQUEST packets"
    ),
    time: Optional[float] = typer.Option(1, "--time", "-T", help="Stop the test after TIME seconds"),
    interval: Optional[float] = typer.Option(
        None, "--interval", "-i", help="Wait INTERVAL seconds between sending each packet"
    ),
    timeout: float = typer.Option(1, "--timeout", "-W", help="Time to wait for a response, in seconds"),
    flood: bool = typer.Option(
        False,
        "--flood",
        "-f",
        help="Flood *ping*. For _every_ ECHO_REQUEST sent a period “.” is printed, while for every ECHO_REPLY received a backspace is printed",
    ),
    audible: bool = typer.Option(False, "--audible", "-a", help="Audible ping"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Do not show results of each ping"),
    show_ips: bool = typer.Option(False, "--show-ips", help="Shows the resolved IP addresses"),
):
    if "faang" in hostnames:
        hostnames.remove("faang")
        hostnames += ["facebook.com", "apple.com", "amazon.com", "netflix.com", "google.com"]

    if "speedtest" in hostnames:
        hostnames.remove("speedtest")
        from aiotestspeed.aio import Speedtest

        speedtest = await Speedtest(secure=True)
        hostnames += [host["host"].split(":")[0] for host in await speedtest.get_closest_servers(10)]
    await ping_pretty(
        hostnames,
        count=count,
        duration=time,
        interval=interval,
        timeout=timeout,
        audible=audible,
        flood=flood,
        verbose=not quiet,
        show_ips=show_ips,
    )
