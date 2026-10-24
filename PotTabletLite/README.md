# PotTabletLite

A stripped-down tablet driver for playing **osu!** on Windows with an
**XP-Pen Star G430S V2**. Its only job is moving the cursor: the tablet area is
mapped to a screen area in absolute mode, and nothing else runs.

It is a lightweight Python rewrite of the absolute-cursor part of
[OpenTabletDriver](https://github.com/OpenTabletDriver/OpenTabletDriver) (the
rest of this repository). The tablet specs, init report, report parsing and area
math are taken from OpenTabletDriver, so areas behave exactly the same.

## What was removed (and why it's faster)

| OpenTabletDriver | PotTabletLite |
| --- | --- |
| Daemon + GUI talking over RPC | One small driver process; the GUI only edits a settings file |
| Filters, plugins, binding system, output-mode pipeline | Nothing. Each report is parsed, multiplied by one precomputed matrix, and sent |
| Pressure, tilt, pen/express buttons, Windows Ink | Not handled. Use the keyboard to click in osu! |
| 300+ tablets | One tablet, hard-coded |

On top of that, the hot loop:

- blocks on the HID read and calls `SendInput` the moment a report arrives (no timers, no smoothing)
- runs on a time-critical priority thread inside a high-priority process
- keeps the Windows HID input queue at 2 reports, so a hiccup never replays stale positions
- skips `SendInput` when the cursor position hasn't changed
- runs with Python's cyclic garbage collector disabled, so there are no collection pauses

`python bench.py` measures the Python work per report (parse, transform,
dedupe) at under 1 µs. With the `ReadFile`/`SendInput` call overhead it is a few
µs, under 1% of even a 1000 Hz report interval.

## Requirements

- Windows 10 or 11
- [Python 3.8+](https://www.python.org/downloads/windows/) (64-bit recommended). Tick "Add python.exe to PATH" in the installer. **No pip packages are needed.**
- The XP-Pen driver **uninstalled**, and OpenTabletDriver closed. Only one program can own the tablet.

## Usage

1. Plug in the tablet.
2. Double-click **`PotTabletLite.pyw`** to open the settings window.
3. Set the **tablet area** (left). Type values, drag the blue area, or use
   *Full area*, *Center* and *Match screen aspect ratio*. Values are in mm, X/Y is the
   centre of the area, and rotation is in degrees.
4. Set the **screen area** (right). Pick a monitor or *All monitors* from
   *Cover*, or type and drag a custom area. Values are in pixels, and X/Y is the centre
   measured from the top-left of the whole desktop (same as OpenTabletDriver).
5. Click **Start driver**. Changes you **Save** while the driver runs apply immediately.
6. You can close the settings window while you play; the driver keeps running.
   Reopen it and click **Stop driver** to stop it.

Already have an area in OpenTabletDriver? Click **Import from OpenTabletDriver**.
It reads `%LOCALAPPDATA%\OpenTabletDriver\settings.json`. Then click Save.

Options:

- **Clip cursor to screen area** (on by default): pen positions outside the tablet area
  stick to the edge of the screen area.
- **Ignore input outside area**: pen positions outside the tablet area are dropped instead.

### Without the GUI

`start_driver.bat` runs the driver in a console window. Stop it with Ctrl+C.
Equivalent: `python -m potlite.driver [--config PATH]` from this folder.

Settings are stored in `%LOCALAPPDATA%\PotTabletLite\config.json`. When the GUI
starts the driver, its log is written to `driver.log` in the same folder.

### osu! settings

Leave **"Map absolute raw input to the osu! window"** off, so the area set here is
the one that applies. Raw input can be on or off.

## Troubleshooting

- **The cursor doesn't move / "Could not open tablet"**: another driver owns the
  tablet. Uninstall the XP-Pen driver, quit OpenTabletDriver, then replug the tablet.
  Check `driver.log`.
- **The cursor jumps between two positions**: Windows is still treating the tablet as
  a mouse in parallel. Replug the tablet while the driver is running so it gets the init
  report again.
- **After stopping the driver, the tablet behaves like a small mouse**: that's the
  tablet's built-in mode. Replug it or start the driver again.
- **Wrong monitor positions**: open the settings window again after changing
  display layout or scaling. The driver itself follows layout changes automatically.

## Development

The parsing, transform and hot-loop code is platform independent and tested:

```
python -m unittest discover tests
python bench.py
```

`potlite/win32.py` is the only Windows-specific module. It contains ctypes bindings for
the HID API, `SendInput`, monitor enumeration and priorities.

## License

LGPL-3.0-or-later, the same as OpenTabletDriver, from which the tablet data and
transform math are derived.
