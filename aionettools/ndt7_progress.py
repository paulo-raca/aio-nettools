from typing import Optional
from aionettools.ndt7 import Direction
from aionettools.ndt7_adaptive import AdaptiveMeasurement
from progressbar import FileTransferSpeed, GranularBar, ProgressBar, Timer

class TransferSpeedMeasurementMixin:
    def __init__(self, window_duration: Optional[float] = None, *args, **kwargs):
        self.adaptive_measurement = AdaptiveMeasurement(window_duration)
        self.cached_speed = 0

    def calculate_speed(self, data):
        last_measurement = data["variables"]["measurement"]
        if last_measurement is None:
            return self.cached_speed

        adaptive_measurement = self.adaptive_measurement.update(last_measurement)
        app_info = adaptive_measurement.get("TCPInfo")
        rate = app_info.get("Rate") if app_info is not None else None
        self.cached_speed = rate["BytesSent"] if rate is not None else self.cached_speed
        return self.cached_speed

class CustomTransferSpeedWidget(FileTransferSpeed, TransferSpeedMeasurementMixin):
    def __init__(self, **kwargs):
        TransferSpeedMeasurementMixin.__init__(self, **kwargs)
        FileTransferSpeed.__init__(self, **kwargs)
        

    def __call__(self, progress, data):
        speed = self.calculate_speed(data)
        if self.unit == "b":
            speed *= 8
        return FileTransferSpeed.__call__(self, progress, data, speed, 1)

class CustomTransferSpeedBarWidget(GranularBar, TransferSpeedMeasurementMixin):
    def __init__(self, reference_speed: float = 100e6, **kwargs):
        TransferSpeedMeasurementMixin.__init__(self, **kwargs)
        GranularBar.__init__(self, **kwargs)
        self.reference_speed = reference_speed
        

    def __call__(self, progress, data, width):
        speed = self.calculate_speed(data) * 8
        data = dict(data)
        normalized_value = 1 - 2**(-speed / self.reference_speed)
        old_value = progress.value
        progress.value = normalized_value * progress.max_value
        ret = super().__call__(progress, data, width)
        progress.value = old_value
        return ret

class TransferSpeedBar(ProgressBar):
    def __init__(self, direction: Direction) -> None:
        widgets=[
            '[', Timer(format="{elapsed}", new_style=True), '] ',
            f"{direction.name.capitalize()} | current: ",
            CustomTransferSpeedWidget(unit="b", window_duration=1),
            ", average: ",
            CustomTransferSpeedWidget(unit="b", window_duration=None),
            " ",
            CustomTransferSpeedBarWidget()
        ]
        super().__init__(widgets=widgets, max_value=1000, variables={"measurement": AdaptiveMeasurement.INITIAL_MEASUREMENT})
