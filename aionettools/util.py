import asyncio
import socket
from functools import wraps
from ipaddress import IPv4Address, _BaseAddress, ip_address
from time import perf_counter
from typing import Any, Iterable, List, Tuple

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


def async_command(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            asyncio.run(f(*args, **kwargs))
        except KeyboardInterrupt:
            pass

    return wrapper


def autocomplete(values: Iterable[str], name: str = "", fast: bool = False):
    def wrapped(ctx: typer.Context, args: List[str], incomplete: str):
        used = ctx.params.get(name) or []
        for value in values:
            if value.startswith(incomplete) and value not in used:
                yield value
                if fast and incomplete == "":
                    break

    return wrapped
