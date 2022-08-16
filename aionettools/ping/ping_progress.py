from typing import Iterable, MutableMapping, Optional, Set

from rich.console import RenderableType
from rich.progress import Progress, TextColumn, Column, Task, TimeElapsedColumn, SpinnerColumn, ProgressColumn
from aionettools.ping.ping_stats import PingStatistics
from aionettools.ping.ping import PingResult

class PingProgressBar(Progress):
    def __init__(self, hostnames: Set[str], window: Optional[float] = None, count: int = 0) -> None:
        super().__init__(
            TimeElapsedColumn(),
            TextColumn(" "),
            SpinnerColumn(spinner_name="dots"),
            TextColumn(" "),
            TextColumn("[progress.description]{task.description},"),

            TextColumn(" sent: "),
            TextColumn("[bold]{task.fields[statistics].total_sent}", justify="right"),

            TextColumn(", time: "),
            TextColumn("[bold]{task.fields[statistics].summary.latency_pretty}", justify="right"),

            TextColumn(", loss: "),
            TextColumn("[bold]{task.fields[statistics].summary.loss_pretty}", justify="right"),
        )
        self.window = window
        self.host_to_taskid: MutableMapping[str, int] = {}
        for hostname in hostnames:
            self.add_host(hostname, count)

    def make_tasks_table(self, tasks: Iterable[Task]):
        ret = super().make_tasks_table(tasks)
        ret.padding = (0, 0)
        return ret

    def add_ping(self, hostname: str, ping: PingResult):
        taskid = self.host_to_taskid[hostname]
        task = self.tasks[taskid]
        statistics = task.fields["statistics"]
        statistics.add_ping(ping)

    def add_host(self, hostname: str, count: int = 0) -> Task:
        taskid = self.host_to_taskid.get(hostname)
        if taskid is None:
            taskid = self.add_task(f"Ping [bold]{hostname}", statistics=PingStatistics(window=self.window, num_scheduled=count))
            self.host_to_taskid[hostname] = taskid
        return self._tasks[taskid]

