from __future__ import annotations

import asyncio
import json
import random
from asyncio import Queue, Task
from enum import Enum, auto
from typing import List, Mapping

import httpx
import typer
import websockets
from typing_extensions import Self

from aionettools.ping import ping_pretty
from aionettools.tcpinfo import get_tcpinfo
from aionettools.util import (
    async_command,
    format_ip_port,
    get_sock_from_websocket,
    timer,
)


class Direction(Enum):
    download = auto()
    upload = auto()

    @property
    def reversed(self):
        if self == NDT7.Direction.download:
            return NDT7.Direction.upload
        else:
            return NDT7.Direction.download


class Role(Enum):
    client = auto()
    server = auto()


class NDT7:
    @staticmethod
    async def get_nearest_servers():
        async with httpx.AsyncClient() as client:
            response = await client.get("https://locate.measurementlab.net/v2/nearest/ndt/ndt7", follow_redirects=True)
            data = response.json()
            return [NDT7.Client(machine=result["machine"], urls=result["urls"]) for result in data["results"]]

    @staticmethod
    async def get_nearest_server():
        return (await NDT7.get_nearest_servers())[0]

    async def handle_websocket(
        websocket: websockets.WebSocketCommonProtocol,
        direction: Direction,
        role: Role = Role.client,
        max_duration: float = 13,
    ):
        sock = get_sock_from_websocket(websocket)
        local_ip_port = format_ip_port(sock.getsockname()) if sock else None
        remote_ip_port = format_ip_port(sock.getpeername()) if sock else None
        transferred_bytes = 0

        measurements_queue = Queue()

        measurement_period = 0.1
        start = timer()

        async def producer_handler():
            nonlocal transferred_bytes
            measurement_direction = Direction.upload if role == Role.client else Direction.download
            next_measurement = timer() - measurement_period

            async def measure():
                elapsed_us = int(1e6 * (timer() - start))
                tcpinfo = get_tcpinfo(sock)
                # print(tcpinfo)
                measurement = {
                    "AppInfo": {
                        "ElapsedTime": elapsed_us,
                        "NumBytes": transferred_bytes,
                    },
                    "Origin": role.name,
                    "Test": direction.name,
                }
                if local_ip_port and remote_ip_port and role == Role.server:
                    measurement["ConnectionInfo"] = {
                        "Client": remote_ip_port,
                        "Server": local_ip_port,
                    }
                if tcpinfo is not None:
                    measurement["TCPInfo"] = {
                        "BusyTime": tcpinfo.tcpi_busy_time,
                        "BytesAcked": tcpinfo.tcpi_bytes_acked,
                        "BytesReceived": tcpinfo.tcpi_bytes_received,
                        "BytesSent": tcpinfo.tcpi_bytes_sent,
                        "BytesRetrans": tcpinfo.tcpi_bytes_retrans,
                        "ElapsedTime": elapsed_us,
                        "MinRTT": tcpinfo.tcpi_min_rtt,
                        "RTT": tcpinfo.tcpi_rtt,
                        "RTTVar": tcpinfo.tcpi_rttvar,
                        "RWndLimited": tcpinfo.tcpi_rwnd_limited,
                        "SndBufLimited": tcpinfo.tcpi_sndbuf_limited,
                    }

                measurements_queue.put_nowait((measurement_direction, measurement))
                return measurement

            msg_size = 1 << 13
            while True:
                now = timer()
                time_until_measurement = next_measurement - now
                if time_until_measurement <= 0:
                    measurement = await measure()
                    await websocket.send(json.dumps(measurement))
                    next_measurement = now + measurement_period

                elif (role, direction) in [(Role.client, Direction.upload), (Role.server, Direction.download)]:
                    # Adjust message size
                    if msg_size < (1 << 24) and msg_size < transferred_bytes / 16:
                        msg_size *= 2
                    # Send random data
                    await websocket.send(random.randbytes(msg_size))
                    transferred_bytes += msg_size

                else:
                    await asyncio.sleep(time_until_measurement)

        async def consumer_handler():
            nonlocal transferred_bytes
            measurement_direction = Direction.download if role == Role.client else Direction.upload

            async for message in websocket:
                if isinstance(message, bytes):
                    transferred_bytes += len(message)
                else:
                    measurement = json.loads(message)
                    measurements_queue.put_nowait((measurement_direction, measurement))

        tasks: List[Task] = list(
            map(asyncio.create_task, [consumer_handler(), producer_handler(), asyncio.sleep(max_duration)])
        )

        def cancel_all():
            measurements_queue.put_nowait(None)
            for task in tasks:
                task.cancel()

        for task in tasks:
            task.add_done_callback(lambda task: cancel_all())

        try:
            while True:
                measurement = await measurements_queue.get()
                if measurement is None:
                    break
                yield measurement
        finally:
            cancel_all()

    class Client:
        def __init__(self, machine: str, urls: Mapping[str, str] = None) -> None:
            self.machine = machine
            if urls is not None:
                self.urls = urls
            else:
                self.urls = urls

        @staticmethod
        def from_host(hostname: str, port: int = 80, secure: bool = False) -> Self:
            scheme = "wss" if secure else "ws"
            urls = {f"{scheme}": None for direction in Direction}
            return NDT7.Client(hostname, urls)

        async def test(self, direction: NDT7.Direction):
            custom_headers = {"User-Agent": "aionettools"}
            url = self.urls.get(f"wss:///ndt/v7/{direction.name}") or self.urls.get(f"ws:///ndt/v7/{direction.name}")
            print(url)
            websocket_subprotocol = "net.measurementlab.ndt.v7"
            async with websockets.connect(
                url, subprotocols=[websocket_subprotocol], extra_headers=custom_headers
            ) as websocket:
                async for direction, measurement in NDT7.handle_websocket(websocket, direction):
                    yield direction, measurement

        async def test_pretty(self, direction: NDT7.Direction):
            from aionettools.ndt7_progress import TransferSpeedBar

            with TransferSpeedBar(direction) as bar:
                async for measurement_direction, measurement_data in self.test(direction):
                    if measurement_direction == direction:
                        bar.update(measurement=measurement_data)

@async_command
async def ndt7_main():
    server = await NDT7.get_nearest_server()
    print(f"Using {server.machine}")
    await ping_pretty([server.machine], count=100, interval=0.05, verbose=False)
    await server.test_pretty(Direction.download)
    await server.test_pretty(Direction.upload)
