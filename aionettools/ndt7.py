from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import json
import random
from yarl import URL
from asyncio import Queue, Task
from enum import Enum, auto
from typing import List, Mapping, Optional

import httpx
import typer
import websockets
import websockets.server
from websockets.http import Headers
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
    USER_AGENT = "aionettools-ndt7"
    WEBSOCKET_SUBPROTOCOL = "net.measurementlab.ndt.v7"
    MAX_SIZE = 1 << 24


    @staticmethod
    async def get_nearest_servers():
        async with httpx.AsyncClient() as client:
            response = await client.get("https://locate.measurementlab.net/v2/nearest/ndt/ndt7", follow_redirects=True)
            data = response.json()
            return [
                NDT7.Client(
                    host=result["machine"], 
                    urls=result["urls"]
                )
                for result in data["results"]
            ]

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
        local_ip_port = format_ip_port(websocket.local_address)
        remote_ip_port = format_ip_port(websocket.remote_address)
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
                if role == Role.server:
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

            msg = random.randbytes(1 << 13)
            while True:
                now = timer()
                time_until_measurement = next_measurement - now
                if time_until_measurement <= 0:
                    measurement = await measure()
                    await websocket.send(json.dumps(measurement))
                    next_measurement = now + measurement_period

                elif (role, direction) in [(Role.client, Direction.upload), (Role.server, Direction.download)]:
                    # Adjust message size
                    if len(msg) < NDT7.MAX_SIZE and len(msg) < transferred_bytes / 16:
                        msg *= 2
                    # Send random data
                    await websocket.send(msg)
                    transferred_bytes += len(msg)

                else:
                    await asyncio.sleep(time_until_measurement)

        async def consumer_handler():
            nonlocal transferred_bytes
            measurement_direction = Direction.download if role == Role.client else Direction.upload

            async for message in websocket:
                if isinstance(message, bytes):
                    transferred_bytes += len(message)
                elif isinstance(message, str):
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

    @dataclass
    class Client:
        host: str
        urls: Mapping[str, str]

        @staticmethod
        def from_url(base_url: str) -> Self:
            url = URL(base_url)
            scheme = "wss" if url.scheme in ("https", "wss") else "ws"
            return NDT7.Client(
                host=url.host,
                urls={
                    f"{scheme}:///ndt/v7/{direction.name}": str(url.with_scheme(scheme).join(URL(f"ndt/v7/{direction.name}")).with_query(url.query))
                    for direction in Direction
                }
            )

        async def test(self, direction: NDT7.Direction):
            url = self.urls.get(f"wss:///ndt/v7/{direction.name}") or self.urls.get(f"ws:///ndt/v7/{direction.name}")
            async with websockets.connect(
                url, subprotocols=[NDT7.WEBSOCKET_SUBPROTOCOL], max_size = NDT7.MAX_SIZE, extra_headers={"User-Agent": NDT7.USER_AGENT}, compression=None
            ) as websocket:
                async for direction, measurement in NDT7.handle_websocket(websocket, direction):
                    yield direction, measurement

        async def test_pretty(self, direction: NDT7.Direction):
            from aionettools.ndt7_progress import TransferSpeedBar

            with TransferSpeedBar(direction) as bar:
                async for measurement_direction, measurement_data in self.test(direction):
                    if measurement_direction == direction:
                        bar.update(measurement=measurement_data)

    @staticmethod
    class Server (websockets.server.serve):
        def __init__(self, base_url: str, **kwargs):
            parsed_base_url = URL(base_url or "")
            ssl = kwargs.get("ssl") is not None
            url = URL.build(
                scheme="wss" if ssl else "ws",
                host=parsed_base_url.host or "localhost",
                port=parsed_base_url.port or "8080",
            )

            super().__init__(
                self.handler,
                process_request=self.process_request,
                host=url.host, 
                port=url.port,
                subprotocols=[NDT7.WEBSOCKET_SUBPROTOCOL], 
                max_size = NDT7.MAX_SIZE,
                extra_headers={"Server": NDT7.USER_AGENT},
                compression = None,
                 **kwargs)

            self.ws_server.url = url



            
        async def handler(self, websocket: WebSocketServerProtocol):
            from aionettools.ndt7_progress import TransferSpeedBar

            direction = Direction.download if websocket.path.endswith("/download") else Direction.upload
            with TransferSpeedBar(direction) as bar:
                async for measurement_direction, measurement_data in NDT7.handle_websocket(websocket, direction, Role.server):
                    if measurement_direction == direction:
                        bar.update(measurement=measurement_data)

        async def process_request(self, path: str, request_headers: Headers):
            for direction in Direction:
                if path == f"/ndt/v7/{direction.name}":
                    print(path)
                    return None

            return HTTPStatus.NOT_FOUND, {}, b"Not found"

@async_command
async def ndt7_main(
    base_url: Optional[str] = typer.Argument(
        None, metavar="URL", help="Base URL of the host to test"
    ),
    server: bool = typer.Option(
        False, "--server", help="Acts as a NDT7 server instead of a client"
    ),
):

    if server:
        async with NDT7.Server(base_url) as server:
            print(f"Server: {type(server)}")
            print(f"Waiting for clients on {server.url}")
            await asyncio.get_event_loop().create_future()
    else:
        if base_url is None:
            client = await NDT7.get_nearest_server()
        else:
            client = NDT7.Client.from_url(base_url)

        #await ping_pretty([client.host], duration=1, interval=0.05, verbose=False)
        await client.test_pretty(Direction.download)
        await client.test_pretty(Direction.upload)
