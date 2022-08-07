from asyncio import Future
from dataclasses import dataclass
import math
import statistics
from typing import Iterable, List, Mapping, Optional, Set, Text

from rich.console import RenderableType
from rich.progress import Progress, TextColumn, Column, BarColumn, Task, TaskProgressColumn, TimeElapsedColumn, SpinnerColumn, ProgressColumn
from sortedcontainers import SortedList
from aionettools.ping import PingResult
from aionettools.util import timer

class PingStatistics:
    TIME_RESOLUTION = 1e-6
    def __init__(self, retention_time: Optional[float] = None, num_scheduled: int = 0) -> None:
        self.retention_time = retention_time
        self.status_count = {status: 0 for status in PingResult.Status}
        self.status_count[PingResult.Status.SCHEDULED] = num_scheduled
        self.results = SortedList[PingResult](key=lambda result: (result.start, result.seq, result.payload))
        self.total_sent = 0
        self._elapsed_n = 0
        self._elapsed_sum = 0.
        self._elapsed_sum_sqr = 0.
        self._changed = True
        self._cached_summary: PingStatistics.Summary

    @dataclass
    class Summary:
        status_count: Mapping[PingResult.Status, int]
        elapsed_mean: Optional[float]
        elapsed_std: Optional[float]

        @property
        def loss(self) -> int:
            pong = self.status_count[PingResult.Status.SUCESS]
            lost = self.status_count[PingResult.Status.TIMEOUT] + self.status_count[PingResult.Status.UNREACHABLE]
            total = pong + lost
            return lost / total if total > 0 else None

        @property
        def latency_pretty_mean(self) -> str:
            if self.elapsed_mean is None:
                return "N/A"
            return f"{1000 * self.elapsed_mean:.1f}"

        @property
        def latency_pretty_plusminus(self) -> str:
            return " ± " if self.elapsed_std is not None else ""

        @property
        def latency_pretty_units(self) -> str:
            return " ms" if self.elapsed_mean is not None else ""

        @property
        def latency_pretty_std(self) -> str:
            return f"{1000 * self.elapsed_std:.1f}" if self.elapsed_std is not None else ""

        @property
        def loss_pretty_perc(self) -> str:
            if self.loss is None:
                return "N/A"
            else:
                return f"{100 * self.loss:.1f}"

        @property
        def loss_pretty_units(self) -> str:
            if self.loss is None:
                return ""
            else:
                return " %"

        def __rich__(self):
            return str(self)

        def __str__(self) -> str:
            return f"time={self.latency_pretty}, loss={self.loss_pretty}"
 
    def pending_done_cb(self, future: Future):
        result = future.result()
        self.status_count[PingResult.Status.PENDING] -= 1
        self.total_sent -= 1
        if self.retention_time is not None:
            self.results.discard(result)
        self.add_ping(result, False)

    def add_ping(self, result: PingResult, new: bool = True):
        self._changed = True
        self.total_sent += 1
        self.status_count[result.status] += 1

        if new and self.status_count[PingResult.Status.SCHEDULED] > 0:
            self.status_count[PingResult.Status.SCHEDULED] -= 1

        if self.retention_time is not None:
            self.results.add(result)

        if result.status == PingResult.Status.PENDING:
            result._future.add_done_callback(self.pending_done_cb)

        if result.status == PingResult.Status.SUCESS:
            elapsed_int = int(result.elapsed / PingStatistics.TIME_RESOLUTION)
            self._elapsed_n += 1
            self._elapsed_sum += elapsed_int
            self._elapsed_sum_sqr += elapsed_int * elapsed_int

    def remove_ping(self, result: PingResult):
        self._changed = True
        self.results.remove(result)
        self.status_count[result.status] -= 1
        if result.status == PingResult.Status.SUCESS:
            elapsed_int = int(result.elapsed / PingStatistics.TIME_RESOLUTION)
            self._elapsed_n -= 1
            self._elapsed_sum -= elapsed_int
            self._elapsed_sum_sqr -= elapsed_int * elapsed_int

    def flush_old(self):
        if self.retention_time is not None:
            keep_since = timer() - self.retention_time
            while len(self.results) > 0:
                result = self.results[0]
                if result.start >= keep_since:
                    break
                self.remove_ping(result)

    @property
    def summary(self):
        if self._changed:
            self.flush_old()
            elapsed_mean = self._elapsed_sum / self._elapsed_n if self._elapsed_n >= 1 else None
            elapsed_var = (self._elapsed_sum_sqr - self._elapsed_n*elapsed_mean**2)/(self._elapsed_n - 1) if self._elapsed_n >= 2 else None
            try:
                elapsed_std = math.sqrt(elapsed_var) if elapsed_var is not None else None
            except:
                print(f"!!! elapsed_mean={elapsed_mean}, elapsed_var={elapsed_var}, n={self._elapsed_n}")
                elapsed_std = 0
            if elapsed_mean is not None:
                elapsed_mean *= PingStatistics.TIME_RESOLUTION
            if elapsed_std is not None:
                elapsed_std *= PingStatistics.TIME_RESOLUTION
            self._cached_summary = PingStatistics.Summary(self.status_count.copy(), elapsed_mean, elapsed_std)
            self._changed = False
        return self._cached_summary

class TaskFieldColumn(ProgressColumn):
    def __init__(self, fieldname: str, table_column: Optional[Column] = None) -> None:
        super().__init__(table_column)
        self.fieldname = fieldname

    def render(self, task: Task) -> RenderableType:
        return task.fields[self.fieldname]

class PingProgressBar(Progress):
    def __init__(self, hostnames: Set[str], retention_time: Optional[float] = None, count: int = 0) -> None:
        super().__init__(
            TimeElapsedColumn(),
            TextColumn(" "),
            SpinnerColumn(spinner_name="dots"),
            TextColumn(" "),
            TextColumn("[progress.description]{task.description},"),

            TextColumn(" sent: "),
            TextColumn("[bold]{task.fields[statistics].total_sent}", justify="right"),

            TextColumn(", time: "),
            TextColumn("[bold]{task.fields[statistics].summary.latency_pretty_mean}", justify="right"),
            TextColumn("[on]{task.fields[statistics].summary.latency_pretty_plusminus}"),
            TextColumn("[on]{task.fields[statistics].summary.latency_pretty_std}", justify="right"),
            TextColumn("[bold]{task.fields[statistics].summary.latency_pretty_units}"),

            TextColumn(", loss: "),
            TextColumn("{task.fields[statistics].summary.loss_pretty_perc}", justify="right"),
            TextColumn("[bold]{task.fields[statistics].summary.loss_pretty_units}"),
        )
        self.host_to_taskid = {
            hostname: self.add_task(f"Ping [bold]{hostname}", statistics=PingStatistics(retention_time=retention_time, num_scheduled=count))
            for hostname in hostnames
        }

    def make_tasks_table(self, tasks: Iterable[Task]):
        ret = super().make_tasks_table(tasks)
        ret.padding = (0, 0)
        return ret

    def add_ping(self, hostname: str, ping: PingResult):
        taskid = self.host_to_taskid[hostname]
        task = self.tasks[taskid]
        statistics = task.fields["statistics"]
        statistics.add_ping(ping)
