from __future__ import annotations
from ast import Tuple

import asyncio
from datetime import datetime
import random
from yarl import URL
from typing import AsyncIterable, Optional
import pytimeparse
import typer
from aionettools.ndt7.ndt7_progress import NDT7ProgressBar

from aionettools.ping import ping_pretty
from aionettools.util import (
    async_command,
    parse_interval
)
from aionettools.elastic import ElasticDump

from aionettools.ndt7.ndt7 import NDT7, Direction, Measurement, Role

app = typer.Typer()

@async_command(app, "server")
async def ndt7_server(
    server_url: Optional[str] = typer.Argument(
        URL("ws://localhost:8080"), metavar="URL", help="Base URL to bind to"
    ),
    window: Optional[float] = typer.Option(None, "--window", "-W", help="Computes the speed of the last WINDOW seconds"),
):
    """Runs an NDT7 server"""

    with NDT7ProgressBar() as bar:
        class ServerPretty(NDT7.Server):
            async def handle_measurements(self, direction: Direction, test_data: AsyncIterable[Tuple[Direction, Measurement]]):
                await bar.run_test(test_data, direction, Role.server, window=window)

        async with ServerPretty(server_url) as server:
            print(f"Waiting for clients on {server.url}")
            await asyncio.get_event_loop().create_future()


@async_command(app, "run")
async def ndt7_main(
    base_url: Optional[str] = typer.Argument(
        None, metavar="URL", help="Base URL of the host to test"
    ),
    window: Optional[float] = typer.Option("3s", "--window", "-W", parser=parse_interval, help="Computes the speed of the last WINDOW seconds"),
    elastic_url: URL = typer.Option(
        None, "--elastic", help="Stores summary of the tests in the specified ElasticSearch URL"
    ),
):
    async with ElasticDump(elastic_url) as elastic:
        if base_url is None:
            client = await NDT7.get_nearest_server()
        else:
            client = NDT7.Client.from_url(base_url)

        timestamp = datetime.utcnow()

        ping_summary = await ping_pretty([client.host], count=20, interval=0.05, verbose=False)
        
        with NDT7ProgressBar() as bar:
            ndt_result = {
                direction: await bar.run_test(client.test(direction), direction, Role.client, window=window)
                for direction in Direction
            }

        elastic.log("ndt7", {
            "@timestamp": timestamp.isoformat(),
            "hostname": client.host,
            "latency": ping_summary.elapsed_mean,
            "latency_stddev": ping_summary.elapsed_std,
            "latency_quantiles": ping_summary.elapsed_quantiles,
            "download": ndt_result[Direction.download],
            "upload": ndt_result[Direction.upload],
        })

@async_command(app, "monitor")
async def ndt7_monitor(
    base_url: Optional[str] = typer.Argument(
        None, metavar="URL", help="Base URL of the host to test"
    ),
    window: Optional[float] = typer.Option("3s", "--window", "-W", parser=parse_interval, help="Computes the speed of the last WINDOW seconds"),
    elastic_url: URL = typer.Option(
        None, "--elastic", help="Stores summary of the tests in the specified ElasticSearch URL"
    ),
    period: float = typer.Option(
        "6h", "--period", metavar="PERIOD", parser=parse_interval, help="Mean interval between tests"
    ),
):
    while True:
        await ndt7_main(base_url=base_url, window=window, elastic_url=elastic_url)

        sleep_time = random.expovariate(1/period)
        sleep_time = max(sleep_time, 0.1 * period)
        sleep_time = min(sleep_time, 2.5 * period)
        await asyncio.sleep(sleep_time)
