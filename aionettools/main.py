import typer

import aionettools.ndt7 as ndt7
import aionettools.ping as ping

cmds = {
  "ping": ping.ping_main,
  "ndt7": ndt7.ndt7_main,
}

__all__ = ["app"]
main = typer.Typer()
for cmd_name, cmd_func in cmds.items():
    main.command(cmd_name)(cmd_func)

    cmd_app = typer.Typer()
    cmd_app.command()(cmd_func)

    globals()[cmd_name] = cmd_app
    __all__.append(cmd_name)

from rich import print
from rich.console import group
from rich.panel import Panel
