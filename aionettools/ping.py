import os
import argparse
import socket
import asyncio
import struct 
import random
from timeit import default_timer as timer

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0
ICMP6_ECHO_REQUEST = 128
ICMP6_ECHO_REPLY = 129

def checksum(data):
    sum = 0
    for i in range(0, len(data), 2):
        sum += data[i] * 256 + data[i + 1]       
    if len(data) & 1:
        sum += buffer[-1]
    while sum >> 16:
        sum = (sum & 0xffff) + (sum >> 16)
    return ~sum & 0xffff
         

class IcmpProtocol:
    pending_pings = {}
    echo_id = os.getpid()
    echo_seq = 0
    
    def connection_made(self, transport):
        self.transport = transport
        self.family = transport._sock.family

    def connection_lost(self, exc):
        print("Connection closed")
        self.transport = None
        self.family = None
        
    def error_received(self, exc):
        print('Error received:', exc)
        
    def datagram_received(self, data, addr):
        print('Received:', data, addr)
        
        if self.family == socket.AddressFamily.AF_INET:
            ip_data = data[0:20]
            icmp_data = data[20:]
        elif self.family == socket.AddressFamily.AF_INET6:
            ip_data = None
            icmp_data = data
        
        #assert 0 == checksum(icmp_data), f"Invalid checksum: {checksum(icmp_data)}"
        
        type = icmp_data[0]
        code = icmp_data[1]
        #rest_of_header = icmp_data[4:8]
        #data = icmp_data[8:]
        
        #print(f"Received ICMP package: addr={addr}, type={type}, code={code}")
        
        if (self.family == socket.AddressFamily.AF_INET and type == ICMP_ECHO_REPLY) or (self.family == socket.AddressFamily.AF_INET6 and type == ICMP6_ECHO_REPLY):
            id_seq = icmp_data[4:8]
            data = icmp_data[8:]
            request = IcmpProtocol.pending_pings.pop(id_seq)
            if request:
                request.set_result(data)
        
    def send_icmp(self, addr, type, code, data):
        if self.family == socket.AddressFamily.AF_INET:
            assert len(data) >= 4, f"ICMP data should have at least 4 bytes"
            
        #print(f"type={type}, code={code}, rest_of-header={rest_of_header}, data={data}")
        header = struct.pack("!BBH", type, code,  0)
        header = struct.pack("!BBH", type, code, checksum(header + data))
        
        packet = header + data
        assert 0 == checksum(packet), f"Invalid checksum: {checksum(packet)}"
        
        self.transport.sendto(packet, addr)
        
        
    async def ping(self, addr, data=None, timeout=None):
        IcmpProtocol.echo_seq += 1
        #print(IcmpProtocol.echo_id, IcmpProtocol.echo_seq)
        id_seq = struct.pack("!HH", IcmpProtocol.echo_id & 0xFFFF, IcmpProtocol.echo_seq & 0xFFFF)
        if self.family == socket.AddressFamily.AF_INET:
            icmp_type = ICMP_ECHO_REQUEST
        elif self.family == socket.AddressFamily.AF_INET6:
            icmp_type = ICMP6_ECHO_REQUEST
        
        if data is None:
            data = b"abcdef"#bytes([random.getrandbits(8) for i in range(10)])
            
        loop = asyncio.get_event_loop()
        waiter = loop.create_future()
        
        
        IcmpProtocol.pending_pings[id_seq] = waiter
        try:
            start = timer()
            self.send_icmp(
                addr=addr,
                type=icmp_type,
                code=0,
                data=id_seq + data)
            
            await asyncio.wait_for(waiter, timeout)
            end = timer()
            return 1000 * (end - start)
        finally:
            if IcmpProtocol.pending_pings.get(id_seq, None) == waiter:
                del IcmpProtocol.pending_pings[id_seq]
        
        
        
async def createIcmpProtocol(family):
    if family == socket.AddressFamily.AF_INET:
        sock = socket.socket(socket.AddressFamily.AF_INET, socket.SOCK_RAW, socket.getprotobyname("icmp"))
    elif family == socket.AddressFamily.AF_INET6:
        sock = socket.socket(socket.AddressFamily.AF_INET6, socket.SOCK_RAW, socket.getprotobyname("ipv6-icmp"))
    sock.setblocking(False)
    
    protocol = IcmpProtocol()
    loop = asyncio.get_event_loop()
    waiter = loop.create_future()
    
    transport = loop._make_datagram_transport(
        sock=sock, protocol=protocol, address=None, waiter=waiter)
    
    await waiter
    return protocol

async def main():
    parser = argparse.ArgumentParser(description='Ping')
    parser.add_argument('host', metavar='HOST', nargs='?', default="example.com", help="Host to ping")
    args = parser.parse_args()
    
    loop = asyncio.get_event_loop()
    addr = (await loop.getaddrinfo(args.host, 0))[0]
    family = addr[0]
    addr = addr[4]
    print(f"addr: {addr}, family={repr(family)}")
    icmp = await createIcmpProtocol(family)
    try:
        while True:
            try:
                print(f"Pong: {await icmp.ping(addr, timeout=1):.1f}ms")
            except asyncio.CancelledError:
                raise
            except:
                print(f"Pong failed")
            break
    finally:
        icmp.transport.close()

asyncio.run(main())
