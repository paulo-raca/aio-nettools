from .ping import IcmpProtocol
import argparse
import asyncio
import socket
import random

async def getaddr(server):
    hostname = server["host"].split(":")[0]
    loop = asyncio.get_event_loop()
    addrs = [
        addr
        for addr in await loop.getaddrinfo(hostname, 80, type=socket.SocketKind.SOCK_STREAM)
        if addr[0] in [socket.AddressFamily.AF_INET, socket.AddressFamily.AF_INET6]
    ]
    assert addrs, f"Couldn't resolve {hostname}"
    server["addrs"] = addrs
    #print(f"{hostname} -> {addrs}")
    return server

async def ping_server(server, icmp, icmp6):
    addr = random.choice(server["addrs"])
    family, addr = addr[0], addr[4]

    if family == socket.AddressFamily.AF_INET:
        ping = icmp.ping(addr, timeout=1)
    elif family == socket.AddressFamily.AF_INET6:
        ping = icmp6.ping(addr, timeout=1)

    try:
        print(f"Ping {server['host']}: {await ping:.1f}ms")
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        print(f"Ping {server['host']} failed: {ex}")

async def main():
    parser = argparse.ArgumentParser(description='speedtest with ndt7')
    parser.add_argument('--endpoint', metavar='INTERVAL', default=0.1, help="Interval between sending ping")
    args = parser.parse_args()
    
    async with await IcmpProtocol.create(socket.AddressFamily.AF_INET) as icmp, \
               await IcmpProtocol.create(socket.AddressFamily.AF_INET6) as icmp6:
        while True:
            for server in resolved_servers:
                asyncio.create_task(ping_server(server, icmp, icmp6))
                await asyncio.sleep(args.interval)
            print("=======================")

asyncio.run(main())
