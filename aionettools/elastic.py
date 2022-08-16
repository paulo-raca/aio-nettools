from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import itertools
import json
import random
import socket
import struct
import traceback
import httpx
from asyncio import Future
from dataclasses import dataclass, field
from enum import Enum, auto
from ipaddress import IPv4Address, IPv6Address, _BaseAddress
from socket import AddressFamily
from timeit import default_timer as timer
from typing import Any, AsyncIterator, ClassVar, Iterable, List, Mapping, Optional, Tuple

import more_itertools
import typer

from .util import async_command, autocomplete, resolve_addresses, test_hostnames
from yarl import URL

class ElasticDump_:
    NOOP: ClassVar[ElasticDump_]

    def __init__(self, base_url: URL, client: httpx.AsyncClient) -> None:
        self.base_url = base_url
        self.client = client
        self.pending_tasks = []
    
    def log(self, dataset: str, document: Any):
        async def task():   
            try:
                url = self.base_url.join(URL(f"{dataset}/_doc"))
                #print(f"Elastic - Adding to {dataset}: {document}")
                #print(f"URL: {url}")
                response = await self.client.post(str(url), json=document)
                #print(response.text)
            except:
                traceback.print_exc()

        if self.base_url is not None:
            self.pending_tasks.append(asyncio.create_task(task()))

ElasticDump_.NOOP = ElasticDump_(None, None)

@asynccontextmanager
async def ElasticDump(base_url: URL) -> AsyncIterator[ElasticDump_]:
    async with httpx.AsyncClient() as http_client:
        ret = ElasticDump_(base_url, http_client)
        yield ret
        await asyncio.gather(*ret.pending_tasks, return_exceptions=False)