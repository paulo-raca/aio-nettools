import argparse
import asyncio

import aionettools.ping as ping
import aionettools.ndt7 as ndt7


async def main():
    parser = argparse.ArgumentParser(description='Nettools implemented with Python AsyncIO')
    subparsers = parser.add_subparsers()
    ping.create_subcommand(parser, subparsers)
    ndt7.create_subcommand(parser, subparsers)
    args = parser.parse_args()
    await args.cmd(args)

try:
    asyncio.run(main())
except KeyboardInterrupt:
    ...
