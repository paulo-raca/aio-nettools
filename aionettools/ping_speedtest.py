from .ping import IcmpProtocol
import argparse
import asyncio
import speedtest
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
    s = speedtest.Speedtest()
    servers = []
    for chunk in s.get_servers().values():
        servers += chunk
        
    resolved_servers = await asyncio.gather(*[
        asyncio.wait_for(getaddr(server), timeout=2)
        for server in servers
    ], return_exceptions=True)
    resolved_servers = [
        resolved_server
        for resolved_server in resolved_servers 
        if not isinstance(resolved_server, asyncio.TimeoutError)
    ]
    #print(resolved_servers)
    #return
    
    
    #print(s.get_best_server())
    #print(servers)
    parser = argparse.ArgumentParser(description='Speedtest Ping')
    parser.add_argument('--interval', metavar='INTERVAL', default=0.1, help="Interval between sending ping")
    args = parser.parse_args()
    
    async with await IcmpProtocol.create(socket.AddressFamily.AF_INET) as icmp, \
               await IcmpProtocol.create(socket.AddressFamily.AF_INET6) as icmp6:
        while True:
            for server in resolved_servers:
                asyncio.create_task(ping_server(server, icmp, icmp6))
                await asyncio.sleep(args.interval)
            print("=======================")

asyncio.run(main())
