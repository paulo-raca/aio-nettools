from contextlib import contextmanager
from typing import AsyncGenerator, AsyncIterable, Iterable, Optional, Set, Tuple

from rich.console import RenderableType
from rich.progress import Progress, TextColumn, Column, Task, TimeElapsedColumn, SpinnerColumn, ProgressColumn, BarColumn
from aionettools.ndt7.ndt7 import Direction, Measurement, Role
from aionettools.ndt7.ndt7_stats import NDT7Statistics

class TaskFieldColumn(ProgressColumn):
    def __init__(self, fieldname: str, table_column: Optional[Column] = None) -> None:
        super().__init__(table_column)
        self.fieldname = fieldname

    def render(self, task: Task) -> RenderableType:
        return task.fields[self.fieldname]

class NDT7ProgressBar(Progress):
    def __init__(self) -> None:
        super().__init__(
            TimeElapsedColumn(),
            TextColumn(" "),
            SpinnerColumn(spinner_name="dots"),
            TextColumn(" "),
            TextColumn("[progress.description]{task.description},"),

            TextColumn(" speed: "),
            TextColumn("[bold]{task.fields[bitrate_Mbps]:.1f}[/] Mbps", justify="right"),
            TextColumn(" "),
            BarColumn(),
        )

    def make_tasks_table(self, tasks: Iterable[Task]):
        ret = super().make_tasks_table(tasks)
        ret.padding = (0, 0)
        return ret

    async def run_test(self, test_data: AsyncIterable[Tuple[Direction, Measurement]], direction: Direction, role: Role, window: Optional[float] = None):
        taskid = self.add_task(f"NDT7 [bold]{direction.name.capitalize()} {role.name}[/]", total=1, bitrate_Mbps=0)
        task = self._tasks[taskid]

        statistics = NDT7Statistics(window)
        last_summary: Measurement = None
        last_direction: Direction = None

        async for measurement_direction, measurement in test_data   :
            summary = statistics.update(measurement, measurement_direction)
            if "TCPInfo" in summary and "Rate" in summary["TCPInfo"]:
                if measurement_direction == direction:
                    transfer_rate = summary["TCPInfo"]["Rate"]["BytesSent"]
                else:
                    transfer_rate = summary["TCPInfo"]["Rate"]["BytesReceived"]
            elif "AppInfo" in summary and "Rate" in summary["AppInfo"]:
                transfer_rate = summary["AppInfo"]["Rate"]["NumBytes"]
            else:
                transfer_rate = 0
                
            if measurement_direction == direction or last_direction != direction:
                bitrate_Mbps = 8 * 1e-6 * transfer_rate
                last_summary = summary
                last_direction = measurement_direction
                self.update(taskid, completed=1 - 2**(- bitrate_Mbps / 100.0), total=1.0001, bitrate_Mbps=bitrate_Mbps)

        task.finished_time = task.elapsed
        return last_summary
