"""Wall-clock helper shared by all devices. All stored timestamps are UTC epoch milliseconds."""

import time


def now_ms() -> int:
    return int(time.time() * 1000)
