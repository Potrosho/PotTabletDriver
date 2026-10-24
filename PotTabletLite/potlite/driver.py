"""PotTabletLite driver: tablet reports -> absolute cursor position, nothing else.

Run with:  python -m potlite.driver [--config PATH]
"""

import argparse
import ctypes
import functools
import gc
import os
import sys
import threading
import time

from . import config, tablet

MUTEX_NAME = "Local\\PotTabletLiteDriver"


def pid_path(config_path):
    return os.path.join(os.path.dirname(config_path), "driver.pid")


def pump(read, buf, emit, holder):
    """The hot loop. Returns only when ``read`` fails (device unplugged).

    read   -- callable filling ``buf`` with one report, returning falsy on error
    emit   -- callable(nx, ny) moving the cursor (SendInput units)
    holder -- one-item list with the current transform from config.build_transform;
              replaced from another thread when the settings change
    """
    unpack = tablet.REPORT.unpack_from
    out_of_range = tablet.STATUS_OUT_OF_RANGE
    ignored = tablet.STATUS_IGNORED
    aux_bit = tablet.STATUS_AUX_BIT
    last_x = last_y = -1

    while read():
        status, x_lo, y_lo, x_hi, y_hi = unpack(buf)
        if status == out_of_range or status in ignored or status & aux_bit:
            continue
        x = x_lo | x_hi << 16
        y = y_lo | y_hi << 16

        a, b, c, d, e, f, min_x, min_y, max_x, max_y, clipping, limiting = holder[0]
        nx = a * x + b * y + c
        ny = d * x + e * y + f
        if clipping:
            if nx < min_x or nx > max_x or ny < min_y or ny > max_y:
                if limiting:
                    continue
                nx = min_x if nx < min_x else max_x if nx > max_x else nx
                ny = min_y if ny < min_y else max_y if ny > max_y else ny

        ix = int(nx)
        iy = int(ny)
        if ix != last_x or iy != last_y:
            last_x = ix
            last_y = iy
            emit(ix, iy)


def _make_emitter(win32):
    inp = win32.INPUT()
    inp.type = win32.INPUT_MOUSE
    mi = inp.mi
    mi.dwFlags = win32.MOUSEEVENTF_MOVE | win32.MOUSEEVENTF_ABSOLUTE | win32.MOUSEEVENTF_VIRTUALDESK
    ref = ctypes.byref(inp)
    size = ctypes.sizeof(inp)
    send_input = win32.user32.SendInput

    def emit(nx, ny):
        mi.dx = nx
        mi.dy = ny
        send_input(1, ref, size)

    return emit


def _reader_thread(win32, holder, log):
    win32.raise_thread_priority()
    emit = _make_emitter(win32)
    waiting_logged = False

    while True:
        path = win32.find_hid_device(tablet.VENDOR_ID, tablet.PRODUCT_ID,
                                     tablet.INPUT_REPORT_LENGTH, tablet.OUTPUT_REPORT_LENGTH)
        if path is None:
            if not waiting_logged:
                log("Waiting for %s..." % tablet.NAME)
                waiting_logged = True
            time.sleep(1.0)
            continue

        try:
            device = win32.HidDevice(path, tablet.INPUT_REPORT_LENGTH, tablet.OUTPUT_REPORT_LENGTH)
        except OSError as ex:
            log("Could not open tablet (%s). Is another driver (XP-Pen, OpenTabletDriver) running?" % ex)
            time.sleep(2.0)
            continue

        try:
            device.set_input_buffers(2)
            if not device.write(tablet.OUTPUT_INIT_REPORT):
                log("Warning: tablet did not accept the init report")
            log("Connected: %s" % tablet.NAME)
            waiting_logged = False

            buf = ctypes.create_string_buffer(tablet.INPUT_REPORT_LENGTH)
            read_count = ctypes.c_ulong()
            read = functools.partial(win32.kernel32.ReadFile, device.handle, buf,
                                     tablet.INPUT_REPORT_LENGTH, ctypes.byref(read_count), None)
            pump(read, buf, emit, holder)
            log("Tablet disconnected")
        finally:
            device.close()
        time.sleep(0.5)


def run(config_path):
    from . import win32

    def log(msg):
        print(time.strftime("[%H:%M:%S] ") + msg, flush=True)

    mutex = win32.create_named_mutex(MUTEX_NAME)
    if mutex is None:
        log("PotTabletLite driver is already running.")
        return 1

    win32.set_dpi_aware()
    win32.raise_process_priority()

    def current_state():
        try:
            mtime = os.stat(config_path).st_mtime_ns
        except OSError:
            mtime = None
        return mtime, win32.virtual_screen()

    def build():
        cfg = config.load(config_path, win32.primary_screen_area())
        _, _, vw, vh = win32.virtual_screen()
        return config.build_transform(cfg, vw, vh)

    state = current_state()
    holder = [build()]
    log("Settings: %s" % config_path)

    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(pid_path(config_path), "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    # Nothing in the hot loop allocates containers that need cycle collection,
    # so the collector would only add pauses.
    gc.disable()

    threading.Thread(target=_reader_thread, args=(win32, holder, log),
                     name="tablet-reader", daemon=True).start()

    # Main thread: pick up settings or monitor layout changes without restarting.
    try:
        while True:
            time.sleep(0.5)
            new_state = current_state()
            if new_state != state:
                state = new_state
                try:
                    holder[0] = build()
                    log("Settings reloaded")
                except (KeyError, ValueError, ZeroDivisionError) as ex:
                    log("Ignoring invalid settings: %s" % ex)
    except KeyboardInterrupt:
        log("Stopping")
    finally:
        try:
            os.remove(pid_path(config_path))
        except OSError:
            pass
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="PotTabletLite driver for the %s" % tablet.NAME)
    parser.add_argument("--config", default=config.DEFAULT_PATH, help="settings file (default: %(default)s)")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("PotTabletLite only runs on Windows.", file=sys.stderr)
        return 1
    return run(os.path.abspath(args.config))


if __name__ == "__main__":
    sys.exit(main())
