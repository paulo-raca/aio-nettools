from collections import defaultdict
from typing import Any, Mapping, Optional

from aionettools.util import timer


Measurement = Mapping[str, Any]


class AdaptiveMeasurement:
    INITIAL_MEASUREMENT = {
        "AppInfo": {
            "ElapsedTime": 0,
            "NumBytes": 0,
        },
        "TCPInfo": {
            "BusyTime": 0,
            "BytesAcked": 0,
            "BytesReceived": 0,
            "BytesSent": 0,
            "BytesRetrans": 0,
            "ElapsedTime": 0,
            "RWndLimited": 0,
            "SndBufLimited": 0,
        },
    }

    def __init__(self, window_duration: Optional[float] = None) -> None:
        self.window_duration = window_duration
        self.groups: Mapping[Any, Measurement] = defaultdict(list)
        self.upload_measurements = []
        self.result = None

    def time_difference(self, a: Measurement, b: Measurement) -> float:
        if "AppInfo" in a and "AppInfo" in b:
            return (b["AppInfo"]["ElapsedTime"] - a["AppInfo"]["ElapsedTime"]) * 1e-6
        if "TCPInfo" in a and "TCPInfo" in b:
            return (b["TCPInfo"]["ElapsedTime"] - a["TCPInfo"]["ElapsedTime"]) * 1e-6
        return b["timestamp"] - a["timestamp"]

    def update(self, measurement: Measurement, group: Optional[Any] = None):
        measurement = dict(measurement)
        measurement["timestamp"] = timer()
        group = self.groups[group]
        group.append(measurement)

        while len(group) >= 3 and group[0] is AdaptiveMeasurement.INITIAL_MEASUREMENT:
            group.pop(0)

        if self.window_duration is None:
            while len(group) >= 3:
                group.pop(1)
        else:
            while len(group) >= 3 and self.time_difference(group[1], group[-1]) >= self.window_duration:
                group.pop(0)

        before = group[0] if len(group) > 1 else AdaptiveMeasurement.INITIAL_MEASUREMENT
        after = measurement
        if "AppInfo" in before and "AppInfo" in after:
            after["AppInfo"]["Delta"] = {
                "ElapsedTime": after["AppInfo"]["ElapsedTime"] - before["AppInfo"]["ElapsedTime"],
                "NumBytes": after["AppInfo"]["NumBytes"] - before["AppInfo"]["NumBytes"],
            }
            elapsedTimeSeconds = after["AppInfo"]["Delta"]["ElapsedTime"] * 1e-6
            if elapsedTimeSeconds > 0.01:
                after["AppInfo"]["Rate"] = {"NumBytes": after["AppInfo"]["Delta"]["NumBytes"] / elapsedTimeSeconds}

        if "TCPInfo" in before and "TCPInfo" in after:
            after["TCPInfo"]["Delta"] = {
                "BusyTime": after["TCPInfo"]["BusyTime"] - before["TCPInfo"]["BusyTime"],
                "BytesAcked": after["TCPInfo"]["BytesAcked"] - before["TCPInfo"]["BytesAcked"],
                "BytesReceived": after["TCPInfo"]["BytesReceived"] - before["TCPInfo"]["BytesReceived"],
                "BytesSent": after["TCPInfo"]["BytesSent"] - before["TCPInfo"]["BytesSent"],
                "BytesRetrans": after["TCPInfo"]["BytesRetrans"] - before["TCPInfo"]["BytesRetrans"],
                "ElapsedTime": after["TCPInfo"]["ElapsedTime"] - before["TCPInfo"]["ElapsedTime"],
                "RWndLimited": after["TCPInfo"]["RWndLimited"] - before["TCPInfo"]["RWndLimited"],
                "SndBufLimited": after["TCPInfo"]["SndBufLimited"] - before["TCPInfo"]["SndBufLimited"],
            }
            elapsedTimeSeconds = after["TCPInfo"]["Delta"]["ElapsedTime"] * 1e-6
            if elapsedTimeSeconds > 0.01:
                after["TCPInfo"]["Rate"] = {
                    "BusyTime": after["TCPInfo"]["Delta"]["BusyTime"] / elapsedTimeSeconds,
                    "BytesAcked": after["TCPInfo"]["Delta"]["BytesAcked"] / elapsedTimeSeconds,
                    "BytesReceived": after["TCPInfo"]["Delta"]["BytesReceived"] / elapsedTimeSeconds,
                    "BytesSent": after["TCPInfo"]["Delta"]["BytesSent"] / elapsedTimeSeconds,
                    "BytesRetrans": after["TCPInfo"]["Delta"]["BytesRetrans"] / elapsedTimeSeconds,
                    "ElapsedTime": after["TCPInfo"]["Delta"]["ElapsedTime"] / elapsedTimeSeconds,
                    "RWndLimited": after["TCPInfo"]["Delta"]["RWndLimited"] / elapsedTimeSeconds,
                    "SndBufLimited": after["TCPInfo"]["Delta"]["SndBufLimited"] / elapsedTimeSeconds,
                }

        return measurement
