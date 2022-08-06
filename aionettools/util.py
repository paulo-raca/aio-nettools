from ipaddress import _BaseAddress, IPv4Address, ip_address
import asyncio
import socket
from typing import Any, List, Tuple
from websockets import WebSocketCommonProtocol
from timeit import default_timer as timer

async def resolve_addresses(hostname: str) -> List[_BaseAddress]:
    loop = asyncio.get_event_loop()
    addresses = await loop.getaddrinfo(host=hostname, port=0)
    return list(set([
        ip_address(address[4][0])
        for address in addresses
    ]))

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
    except:
        pass

    try:
        return transport._sock
    except:
        pass

    return None