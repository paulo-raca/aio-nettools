from dataclasses import dataclass, field
import argparse
from enum import Enum, auto
from ipaddress import _BaseAddress, IPv4Address, IPv6Address
import random
import socket
import asyncio
import statistics
import struct
from typing import Any, List, Mapping, Optional, Tuple
from asyncio import Future
from timeit import default_timer as timer
from socket import AddressFamily
from typing_extensions import Self

from .util import resolve_addresses

@dataclass
class PingResponse:
    class Status(Enum):
        PENDING = auto()
        SUCESS = auto()
        TIMEOUT = auto()
        CANCELED = auto()
        UNREACHABLE = auto()

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
        if self.status == PingResponse.Status.PENDING:
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
        self.pending_pings: Mapping[int, PingResponse] = {}
        self.sockets: Mapping[AddressFamily, socket.socket] = {}
        self.pending_sends: Mapping[AddressFamily, List[Tuple[bytes, _BaseAddress]]] = {}

        for family, protocol in [(AddressFamily.AF_INET, socket.getprotobyname("icmp")), (AddressFamily.AF_INET6, socket.getprotobyname("ipv6-icmp"))]:
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
            pending_ping.complete(PingResponse.Status.CANCELED)

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
            #print(f"id={id}, seq={seq}, payload={payload}")
            key = (seq, payload)
            pending_ping = self.pending_pings.get(key)
            if pending_ping and pending_ping.payload == payload:
                pending_ping.complete(PingResponse.Status.SUCESS)
        else:
            #print(f"Discarding unexpected ICMP package: addr={addr}, type={type}, code={code}, remaining={remaining}")
            pass
        
    def send_ready(self, family: AddressFamily):
        queue = self.pending_sends[family]
        sock = self.sockets[family]
        if len(queue) > 0:
            data, address = queue.pop(0)
            sock.sendto(data, (address.compressed, 0))
        if len(queue) == 0:
            self.loop.remove_writer(sock.fileno())
        
    def ping(self, addr: _BaseAddress, timeout: Optional[float] = 1, **kwargs):
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
            icmp_type, # ICMP Type: ECHO_REQUEST
            0, # ICMP Code: always zero for ping
            0, # Checksum -- Populated later by the linux kernel
            0, # Identifier -- Populated later by the linux kernel
            seq, # Sequence number
        )
        payload = bytes([random.getrandbits(8) for i in range(10)])
        packet = header + payload
        
        pending_ping = PingResponse(addr=addr, seq=seq, payload=payload)
        for key, value in kwargs.items():
            if hasattr(pending_ping, key):
                raise ValueError(f"Cannot specify reserver field '{key}'")
            setattr(pending_ping, key, value)

        self.pending_pings[pending_ping.key] = pending_ping
        sock = self.sockets[family]
        self.pending_sends[family].append((packet, addr))
        if len(self.pending_sends[family]) == 1:
            self.loop.add_writer(sock.fileno(), self.send_ready, family)

        ret = pending_ping._future
        ret.add_done_callback(lambda _: self.pending_pings.pop(pending_ping.key))
        if timeout is not None:
            loop = asyncio.get_event_loop()
            def on_timeout():
                pending_ping.complete(PingResponse.Status.TIMEOUT)
            timeout_handle = loop.call_later(timeout, on_timeout)
            ret.add_done_callback(lambda _: timeout_handle.cancel())
        return ret

    async def wait_pending(self):
        await asyncio.gather(*[pending.future for pending in self.pending_pings.values()], return_exceptions=True)

async def main():
    parser = argparse.ArgumentParser(description='Ping')
    parser.add_argument('hostname', metavar='HOST', nargs='?', default="example.com", help="Host to ping")
    parser.add_argument('--count', '-c', metavar='COUNT', type=int, default=None, help="Stop after sending COUNT ECHO_REQUEST packets")
    parser.add_argument('--interval', '-i', metavar='INTERVAL', type=float, default=None, help="Wait INTERVAL seconds between sending each packet")
    parser.add_argument('--timeout', '-W', metavar='TIMEOUT', type=float, default=1, help="Time to wait for a response, in seconds")
    parser.add_argument('--flood', '-f', action='store_true', help="Wait INTERVAL seconds between sending each packet")
    parser.add_argument('--audible', '-a', action='store_true', help="Audible ping")
    args = parser.parse_args()
    
    interval = args.interval if args.interval is not None else 0.005 if args.flood else 1
    addresses = await resolve_addresses(args.hostname)

    ping_count = 0
    pongs = []
    try:
        async with Ping() as ping:
            def pong_callback(future: Future[PingResponse]):
                result = future.result()

                if result.status == PingResponse.Status.SUCESS:
                    pongs.append(result.elapsed)
                    if args.audible:
                        print("\a", end="", flush=True)
                    if args.flood:
                        print("\b \b", end="", flush=True)

                if not args.flood:
                    print(f"{result.hostname} ({result.addr}): icmp_seq={result.seq}, time={result.elapsed * 1000:.1f} ms, {result.status.name}")

            while args.count is None or ping_count < args.count:
                if ping_count > 0:
                    await asyncio.sleep(interval)
                ping_count += 1
                if args.flood:
                    print(".", end="", flush=True)

                addresses = addresses[1:] + addresses[:1]
                addr = addresses[0]
                pong = ping.ping(addr, timeout=args.timeout, hostname=args.hostname)
                pong.add_done_callback(pong_callback)

            await ping.wait_pending()
    finally:
        if ping_count > 0:
            success_rate = len(pongs) / ping_count
            if pongs:
                time_mean = statistics.mean(pongs)
                time_stderr = statistics.stdev(pongs)
                latency = f"{1000 * time_mean :.1f} ± {1000 * time_stderr :.1f} ms"
            else:
                latency = "N/A"
            if args.flood:
                print()
            print(f"Success Rate: {100 * success_rate : .2f} %, latency={latency}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        ...
