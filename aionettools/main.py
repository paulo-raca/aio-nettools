from typing import Mapping
import typer

from aionettools.ndt7 import app as ndt7_app
from aionettools.ping import app as ping_app

app = typer.Typer()

nested_apps: Mapping[str, typer.Typer] = {
  "ping": ping_app,
  "ndt7": ndt7_app,
}

for nested_name, nested_app in nested_apps.items():
    if len(nested_app.registered_commands) == 1 and len(nested_app.registered_groups) == 0:
        app.command(nested_name)(nested_app.registered_commands[0].callback)
    else:
        app.add_typer(nested_app, name=nested_name)
