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

from aionettools.elastic import ElasticDump

from aionettools.util import IPVersion, async_command, autocomplete, resolve_addresses, test_hostnames


@dataclass
class PingResult:
    class Status(Enum):
        SCHEDULED = auto()    # Not sent yet
        PENDING = auto()      # Ping sent
        SUCCESS = auto()       # Pong received
        UNREACHABLE = auto()  # Received a Network Unreachable response
        TIMEOUT = auto()      # No response received before reaching the timeout
        CANCELED = auto()     # Aborted before receiving the response

    addr: bytes
    seq: int
    payload: bytes
    start: float = field(default_factory=timer)
    end: Optional[float] = None
    status: Status = Status.PENDING
    timestamp: datetime = field(default_factory=datetime.utcnow)
    _future: Future[PingResult] = field(default_factory=lambda: asyncio.get_event_loop().create_future())

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
                pending_ping.complete(PingResult.Status.SUCCESS)
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
