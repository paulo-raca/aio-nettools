from __future__ import annotations

import asyncio
import socket
from enum import Enum
from functools import wraps
from ipaddress import IPv4Address, IPv6Address, _BaseAddress, ip_address
from math import floor
from time import perf_counter
from typing import Any, Iterable, List, Tuple
import pytimeparse
import typer
from websockets import WebSocketCommonProtocol


test_hostnames = [
    "localhost",
    "example.com",
    "facebook.com",
    "amazon.com",
    "apple.com",
    "netflix.com",
    "google.com",
]


def timer():
    return perf_counter()


async def resolve_addresses(hostname: str) -> List[_BaseAddress]:
    loop = asyncio.get_event_loop()
    addresses = await loop.getaddrinfo(host=hostname, port=0)
    return list(set([ip_address(address[4][0]) for address in addresses]))


async def resolve_address(hostname: str) -> _BaseAddress:
    return (await resolve_addresses(hostname))[0]


def format_ip_port(addr: Tuple[Any]) -> str:
    ip = ip_address(addr[0])
    if isinstance(addr, IPv4Address):
        return f"{ip}:{addr[1]}"
    else:
        return f"[{ip}]:{addr[1]}"


def quantiles(data: List[float], quantiles: List[float]):
    if not data:
        return None

    ret: List[float] = []
    for quantile in quantiles:
        i = quantile * (len(data) - 1)
        i_int = floor(i)
        i_frac = i - i_int
        value = (1 - i_frac) * data[i_int]
        if i_frac > 0:
            value += (i_frac) * data[i_int + 1]
        ret.append(value)


def get_sock_from_websocket(websocket: WebSocketCommonProtocol) -> socket.socket:
    transport = websocket.transport
    try:
        from asyncio.sslproto import _SSLProtocolTransport

        if isinstance(transport, _SSLProtocolTransport):
            transport = transport._ssl_protocol._transport
    except BaseException:
        pass

    try:
        return transport._sock
    except BaseException:
        pass

    return None


def async_command(app, *args, **kwargs):
    @wraps(app.command)
    def decorator(f):
        @app.command(*args, **kwargs)
        @wraps(f)
        def wrapper(*args, **kwargs):
            async def f_with_signal(*args, **kwargs):
                task = asyncio.create_task(f(*args, **kwargs))
                asyncio.get_event_loop()
                for signame in ("SIGINT", "SIGTERM"):
                    # loop.add_signal_handler(getattr(signal, signame), task.cancel, signame)
                    ...
                try:
                    return await task
                except asyncio.CancelledError:
                    ...

            return asyncio.run(f_with_signal(*args, **kwargs))

        return f

    return decorator


def autocomplete(values: Iterable[str], name: str = "", fast: bool = False):
    def wrapped(ctx: typer.Context, args: List[str], incomplete: str):
        used = ctx.params.get(name) or []
        for value in values:
            if value.startswith(incomplete) and value not in used:
                yield value
                if fast and incomplete == "":
                    break

    return wrapped


class IPVersion(Enum):
    IPv4 = 4
    IPv6 = 6

    @staticmethod
    def from_address(addr: _BaseAddress) -> IPVersion:
        if isinstance(addr, IPv4Address):
            return IPVersion.IPv4
        elif isinstance(addr, IPv6Address):
            return IPVersion.IPv6
        else:
            raise ValueError("Unsupported address type")

def parse_interval(value) -> float:
    if isinstance(value, int | float):
        return value
    try:
        return pytimeparse.parse(value)
    except ValueError:
        return float(value)