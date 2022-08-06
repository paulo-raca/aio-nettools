from dataclasses import dataclass, field
import argparse
from enum import Enum, auto
from ipaddress import _BaseAddress, IPv4Address, IPv6Address
import itertools
import more_itertools
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
class PingResult:
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
            #print(f"id={id}, seq={seq}, payload={payload}")
            key = (seq, payload)
            pending_ping = self.pending_pings.get(key)
            if pending_ping and pending_ping.payload == payload:
                pending_ping.complete(PingResult.Status.SUCESS)
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
        
        pending_ping = PingResult(addr=addr, seq=seq, payload=payload)
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
                pending_ping.complete(PingResult.Status.TIMEOUT)
            timeout_handle = loop.call_later(timeout, on_timeout)
            ret.add_done_callback(lambda _: timeout_handle.cancel())
        return ret

    async def wait_pending(self):
        await asyncio.gather(*[pending._future for pending in self.pending_pings.values()], return_exceptions=True)

async def ping_pretty(hostnames: List[str], count: Optional[int] = None, interval: Optional[float] = None, timeout: float = 1, audible: bool = False, flood: bool = False, verbose: bool = True, show_ips: bool = False):
    from aionettools.ping_progress import PingProgressBar

    host_addr = dict(zip(hostnames, await asyncio.gather(*map(resolve_addresses, hostnames))))
    if show_ips:
        for host, addresses in host_addr.items():
            print(f"{host}: {', '.join(map(str, addresses))}")
    host_addr_iterator = more_itertools.interleave(
        *[
            zip(itertools.repeat(hostname), itertools.cycle(addresses))
            for hostname, addresses in host_addr.items()
        ]
    )

    if count is not None:
        count = max(count, len(hostnames))
    if interval is None:
        interval = 0.005 if flood else .25

    with PingProgressBar(redirect_stdout=True) as bar:
        sent = 0
        received = []
        update_rate = .1
        last_update = timer() - update_rate

        def update_progress(force=False):
            nonlocal last_update
            pending = count - sent if count is not None else None
            now = timer()
            if force or (not flood and timer() - last_update > update_rate):
                bar.update_pings(sent, received, pending)
                last_update = now

        try:
            async with Ping() as ping:
                def callback(future):
                    result = future.result()

                    if audible:
                        print("\a", end="", flush=True)
                    if flood:
                        print("\b \b", end="", flush=True)
                    elif verbose:
                        print(f"{result.hostname} ({result.addr}): icmp_seq={result.seq}, time={result.elapsed * 1000:.1f} ms, {result.status.name}")

                    received.append(result)
                    update_progress()

                while count is None or sent < count:
                    if sent > 0:
                        await asyncio.sleep(interval)
                    sent += 1

                    update_progress()
                    hostname, addr = next(host_addr_iterator)
                    ping.ping(addr, timeout, hostname=hostname).add_done_callback(callback)
                    if flood:
                        print(".", end="", flush=True)

                await ping.wait_pending()

        finally:
            update_progress(force=True)

def create_subcommand(parser: argparse.ArgumentParser, subparsers: argparse._SubParsersAction):
    async def cmd(args):
        if args.speedtest:
            from aiotestspeed.aio import Speedtest
            speedtest: Speedtest = await Speedtest(secure=True)
            args.hostnames = [
                host["host"].split(":")[0]
                for host in await speedtest.get_closest_servers(10)
            ]

        await ping_pretty(args.hostnames, count=args.count, interval=args.interval, timeout=args.timeout, audible=args.audible, flood=args.flood, verbose=not args.quiet, show_ips=args.show_ip)

    cmd_parser = subparsers.add_parser(
        'ping',
        description='Uses ICMP echo to test latency and loss to a given host',
    )
    cmd_parser.set_defaults(cmd=cmd)

    cmd_parser.add_argument('hostnames', metavar='HOST', nargs='*', default=["example.com"], help="Host to ping")
    cmd_parser.add_argument('--count', '-c', metavar='COUNT', type=int, default=None, help="Stop after sending COUNT ECHO_REQUEST packets")
    cmd_parser.add_argument('--interval', '-i', metavar='INTERVAL', type=float, default=None, help="Wait INTERVAL seconds between sending each packet")
    cmd_parser.add_argument('--timeout', '-W', metavar='TIMEOUT', type=float, default=1, help="Time to wait for a response, in seconds")
    cmd_parser.add_argument('--flood', '-f', action='store_true', help="Wait INTERVAL seconds between sending each packet")
    cmd_parser.add_argument('--audible', '-a', action='store_true', help="Audible ping")
    cmd_parser.add_argument('--quiet', '-q', action='store_true', help="Do not show results of each ping")
    cmd_parser.add_argument('--speedtest', action='store_true', help="Ping servers from SpeedTest by Ookla")
    cmd_parser.add_argument('--show-ip', action='store_true', help="Shows the resolved IP addresses")
