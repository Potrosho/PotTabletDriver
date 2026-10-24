"""Measure the Python cost of handling one tablet report (parse + transform +
dedupe), excluding the Windows ReadFile/SendInput calls themselves.

Run from the PotTabletLite folder:  python bench.py
"""

import gc
import struct
import time

from potlite import config, driver, tablet

N = 1_000_000
SCREEN = {"width": 1920.0, "height": 1080.0, "x": 960.0, "y": 540.0}

reports = [struct.pack("<BBHHHbbBB", 2, 0xA0, (i * 7) % tablet.MAX_X, (i * 3) % tablet.MAX_Y, 0, 0, 0, 0, 0)
           for i in range(1024)]
buf = bytearray(tablet.INPUT_REPORT_LENGTH)
holder = [config.build_transform(config.default_config(SCREEN), 1920, 1080)]


def make_read():
    remaining = [N]

    def read():
        remaining[0] -= 1
        buf[:] = reports[remaining[0] & 1023]
        return remaining[0] >= 0

    return read


def emit(x, y):
    pass


def run(read):
    gc.disable()
    start = time.perf_counter()
    driver.pump(read, buf, emit, holder)
    return time.perf_counter() - start


# Baseline: the fake read() alone, so it can be subtracted.
def baseline():
    read = make_read()
    start = time.perf_counter()
    while read():
        pass
    return time.perf_counter() - start


total = run(make_read())
base = baseline()
per_report_us = (total - base) / N * 1e6
print("Python per-report processing: %.2f us" % per_report_us)
print("Share of a 1 ms report interval: %.2f%%" % (per_report_us / 10))
