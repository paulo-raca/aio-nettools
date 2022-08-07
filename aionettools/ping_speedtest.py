import itertools
from aionettools.util import resolve_addresses
from .ping import Ping
import argparse
import asyncio
import urllib
import random
from aiotestspeed.aio import Speedtest

async def main():
    s: Speedtest = await Speedtest(secure=True)
    servers = []

    for more_servers in (await s.get_closest_servers()).values():
        servers += more_servers

    for server in servers:
        print(">>", server)

    server_addresses = await asyncio.gather(*[
        resolve_addresses(server["host"].split(":")[0])
        for server in servers
    ])
    print(f"address_sets={server_addresses}")
    
    address_iter = itertools.cycle([
        zip(itertools.repeat(server), itertools.cycle(addresses))
        for server, addresses in zip(servers, server_addresses)
    ])

    for value in address_iter:
        print(value)
        #print(f"{server['host']}: {addr}")

    #server_ips = [
    #    urllib.parse(server)
    #    for server in servers.values()
    #]

asyncio.run(main())
