import threading
import time


class RequestPacer:
    """Keeps at least `min_interval` seconds between calls to one service."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last_request = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            remaining = self._last_request + self.min_interval - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            self._last_request = time.monotonic()
