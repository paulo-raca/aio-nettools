from asyncio import Future
from dataclasses import dataclass
import math
from typing import List, Mapping, Optional

from sortedcontainers import SortedList
from aionettools.ping.ping import PingResult
from aionettools.util import quantiles, timer

class PingStatistics:
    TIME_RESOLUTION = 1e-6
    def __init__(self, window: Optional[float] = None, num_scheduled: int = 0) -> None:
        self.window = window
        self.status_count = {status: 0 for status in PingResult.Status}
        self.status_count[PingResult.Status.SCHEDULED] = num_scheduled
        self.results = SortedList[PingResult](key=lambda result: (result.start, result.seq, result.payload))
        self.total_sent = 0
        self._elapsed_n = 0
        self._elapsed_sum = 0.
        self._elapsed_sum_sqr = 0.
        self._elapsed_all = SortedList()
        self._changed = True
        self._cached_summary: PingStatistics.Summary

    @dataclass
    class Summary:
        status_count: Mapping[PingResult.Status, int]
        elapsed_mean: Optional[float]
        elapsed_std: Optional[float]
        elapsed_quantiles: Optional[List[float]]

        @property
        def loss(self) -> int:
            pong = self.status_count[PingResult.Status.SUCCESS]
            lost = self.status_count[PingResult.Status.TIMEOUT] + self.status_count[PingResult.Status.UNREACHABLE]
            total = pong + lost
            return lost / total if total > 0 else None

        @property
        def latency_pretty(self) -> str:
            if self.elapsed_mean is None:
                return "N/A"
            else:
                ret = f"{1000 * self.elapsed_mean:.1f}"
                if self.elapsed_std is not None:
                    ret += f" ± {1000 * self.elapsed_std:.1f}"
                ret += " ms"
                return ret

        @property
        def loss_pretty(self) -> str:
            if self.loss is None:
                return "N/A"
            else:
                return f"{100 * self.loss:.1f} %"

        def __rich__(self):
            return str(self)

        #def __str__(self) -> str:
        #    return f"time={self.latency_pretty_mean}{self.latency_pretty_plusminus}{self.latency_pretty_std}{self.latency_pretty_units}, loss={self.loss_pretty_perc}{self.loss_pretty_units}"
 
    def pending_done_cb(self, future: Future):
        result = future.result()
        self.status_count[PingResult.Status.PENDING] -= 1
        self.total_sent -= 1
        if self.window is not None:
            self.results.discard(result)
        self.add_ping(result, False)

    def add_ping(self, result: PingResult, new: bool = True):
        self._changed = True
        self.total_sent += 1
        self.status_count[result.status] += 1

        if new and self.status_count[PingResult.Status.SCHEDULED] > 0:
            self.status_count[PingResult.Status.SCHEDULED] -= 1

        if self.window is not None:
            self.results.add(result)

        if result.status == PingResult.Status.PENDING:
            result._future.add_done_callback(self.pending_done_cb)

        if result.status == PingResult.Status.SUCCESS:
            self._elapsed_all.add(result.elapsed)
            elapsed_int = int(result.elapsed / PingStatistics.TIME_RESOLUTION)
            self._elapsed_n += 1
            self._elapsed_sum += elapsed_int
            self._elapsed_sum_sqr += elapsed_int * elapsed_int

    def remove_ping(self, result: PingResult):
        self._changed = True
        self.results.remove(result)
        self.status_count[result.status] -= 1
        if result.status == PingResult.Status.SUCCESS:
            self._elapsed_all.remove(result.elapsed)
            elapsed_int = int(result.elapsed / PingStatistics.TIME_RESOLUTION)
            self._elapsed_n -= 1
            self._elapsed_sum -= elapsed_int
            self._elapsed_sum_sqr -= elapsed_int * elapsed_int

    def flush_old(self):
        if self.window is not None:
            keep_since = timer() - self.window
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

            elapsed_quantiles = quantiles(self._elapsed_all, (0, .25, .5, .75, 1))

            self._cached_summary = PingStatistics.Summary(self.status_count.copy(), elapsed_mean, elapsed_std, elapsed_quantiles)
            self._changed = False
        return self._cached_summary
