import statistics
from typing import List
from aionettools.ping import PingResult
from progressbar import FormatLabel, MultiRangeBar, ProgressBar, Timer
from termcolor import colored

class PingProgressBar(ProgressBar):
    def __init__(self, **kwargs) -> None:
        markers = [
            colored('!', 'red', attrs=['bold']),  # Lost
            colored('●', 'green'),  # Pongs
            colored('·', 'yellow'),  # Sent
            ' ',  # Pending
        ]
        widgets = [
            '[', Timer(format="{elapsed}", new_style=True), '] ',
            f"Ping | ",
            FormatLabel("{variables.progress}, Time: {variables.latency}, Loss: {variables.loss} ", new_style=True),
            MultiRangeBar("status", markers=markers),
        ]
        super().__init__(widgets=widgets, variables={"latency": "N/A", "loss": 0, "progress": "0"}, **kwargs)

    def update_pings(self, sent: int, received: List[PingResult], pending: int = 0, **kwargs):
        #print(f"sent={sent}, received={received}, pending={pending}")
        pongs = 0
        lost = 0
        latencies = []

        for result in received:
            if result.status == PingResult.Status.SUCESS:
                latencies.append(result.elapsed)
                pongs += 1
            elif result.status != PingResult.Status.CANCELED:
                lost += 1

        if pongs > 0:
            loss = lost / (pongs + lost)
            time_mean = statistics.mean(latencies)
            time_stderr = statistics.stdev(latencies) if len(latencies) >= 2 else 0
            latency = f"{1000 * time_mean :.1f}±{1000 * time_stderr :.1f} ms"
        else:
            loss = 0
            latency = "N/A"

        if pending is None:
            progress = f"{sent}"
        else:
            progress = f"{sent} / {sent + pending}"

        status = [lost, pongs, sent - len(received), pending or 0]
        self.update(sent, status=status, latency=latency, loss=f"{100*loss:.1f} %", progress=progress, **kwargs)