"""Settings window: tablet area, screen area, start/stop the driver.

The GUI only edits the settings file. The driver runs as its own process and
reloads the file automatically, so the GUI can be closed while playing.
"""

import math
import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from . import config, tablet, win32
from .driver import MUTEX_NAME, pid_path

TABLET_CANVAS_W = 420
SCREEN_CANVAS_W = 480
CANVAS_PAD = 10
CREATE_NO_WINDOW = 0x08000000


def _fmt(value, digits):
    text = ("%." + str(digits) + "f") % value
    return text.rstrip("0").rstrip(".") if "." in text else text


class AreaFields:
    """A row of labelled entries bound to one area dict."""

    def __init__(self, parent, fields, digits, on_change):
        self.digits = digits
        self.vars = {}
        for col, (key, label) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=0, column=col * 2, padx=(8 if col else 0, 2), sticky="e")
            var = tk.StringVar()
            ttk.Entry(parent, textvariable=var, width=8).grid(row=0, column=col * 2 + 1, sticky="w")
            var.trace_add("write", lambda *_: on_change())
            self.vars[key] = var

    def set(self, area):
        for key, var in self.vars.items():
            var.set(_fmt(area[key], self.digits))

    def get(self):
        """Return the area dict, or None if any field is not a number."""
        try:
            return {key: float(var.get()) for key, var in self.vars.items()}
        except ValueError:
            return None


class App:
    def __init__(self, root, config_path):
        self.root = root
        self.config_path = config_path
        self.driver_process = None
        self._status_job = None
        self._drag = (0, 0, None)

        self.vx, self.vy, self.vw, self.vh = win32.virtual_screen()
        self.monitors = win32.monitors()
        self.cfg = config.load(config_path, win32.primary_screen_area())

        root.title("PotTabletLite - %s" % tablet.NAME)
        root.resizable(False, False)
        main = ttk.Frame(root, padding=10)
        main.grid()

        self._build_tablet_section(main)
        self._build_screen_section(main)
        self._build_bottom(main)

        self._load_into_fields()
        self._poll_driver()

    # --- layout -----------------------------------------------------------

    def _build_tablet_section(self, main):
        box = ttk.LabelFrame(main, text="Tablet area (mm)", padding=8)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.t_scale = (TABLET_CANVAS_W - 2 * CANVAS_PAD) / tablet.WIDTH_MM
        h = int(tablet.HEIGHT_MM * self.t_scale) + 2 * CANVAS_PAD
        self.t_canvas = tk.Canvas(box, width=TABLET_CANVAS_W, height=h, bg="#1e1e1e", highlightthickness=0)
        self.t_canvas.grid(row=0, column=0, columnspan=2)
        self._bind_drag(self.t_canvas, self._tablet_drag_start, self._tablet_drag)

        fields = ttk.Frame(box)
        fields.grid(row=1, column=0, columnspan=2, pady=(8, 4), sticky="w")
        self.t_fields = AreaFields(fields, [("width", "Width"), ("height", "Height"), ("x", "X"),
                                            ("y", "Y"), ("rotation", "Rotation")], 2, self._redraw)

        buttons = ttk.Frame(box)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(buttons, text="Full area", command=self._tablet_full).grid(row=0, column=0)
        ttk.Button(buttons, text="Center", command=self._tablet_center).grid(row=0, column=1, padx=4)
        ttk.Button(buttons, text="Match screen aspect ratio",
                   command=self._tablet_match_aspect).grid(row=0, column=2)

    def _build_screen_section(self, main):
        box = ttk.LabelFrame(main, text="Screen area (pixels)", padding=8)
        box.grid(row=0, column=1, sticky="nsew")

        self.s_scale = min((SCREEN_CANVAS_W - 2 * CANVAS_PAD) / self.vw,
                           (TABLET_CANVAS_W * 0.75 - 2 * CANVAS_PAD) / self.vh)
        h = int(self.vh * self.s_scale) + 2 * CANVAS_PAD
        self.s_canvas = tk.Canvas(box, width=SCREEN_CANVAS_W, height=h, bg="#1e1e1e", highlightthickness=0)
        self.s_canvas.grid(row=0, column=0, columnspan=2)
        self._bind_drag(self.s_canvas, self._screen_drag_start, self._screen_drag)

        pick = ttk.Frame(box)
        pick.grid(row=1, column=0, columnspan=2, pady=(8, 4), sticky="w")
        ttk.Label(pick, text="Cover:").grid(row=0, column=0, padx=(0, 4))
        choices = ["All monitors (%dx%d)" % (self.vw, self.vh)]
        for i, m in enumerate(self.monitors, 1):
            choices.append("Monitor %d: %s %dx%d%s" % (i, m["name"], m["width"], m["height"],
                                                     " (primary)" if m["primary"] else ""))
        self.s_choice = ttk.Combobox(pick, values=choices, state="readonly", width=40)
        self.s_choice.grid(row=0, column=1)
        self.s_choice.bind("<<ComboboxSelected>>", self._screen_pick)

        fields = ttk.Frame(box)
        fields.grid(row=2, column=0, columnspan=2, sticky="w")
        self.s_fields = AreaFields(fields, [("width", "Width"), ("height", "Height"),
                                            ("x", "X"), ("y", "Y")], 0, self._redraw)

        opts = ttk.Frame(box)
        opts.grid(row=3, column=0, columnspan=2, pady=(8, 0), sticky="w")
        self.clipping = tk.BooleanVar()
        self.limiting = tk.BooleanVar()
        ttk.Checkbutton(opts, text="Clip cursor to screen area", variable=self.clipping).grid(row=0, column=0)
        ttk.Checkbutton(opts, text="Ignore input outside area",
                        variable=self.limiting).grid(row=0, column=1, padx=(12, 0))
        self.tap_click = tk.BooleanVar()
        ttk.Checkbutton(opts, text="Left click when the pen touches the tablet",
                        variable=self.tap_click).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_bottom(self, main):
        bar = ttk.Frame(main, padding=(0, 10, 0, 0))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(4, weight=1)
        ttk.Button(bar, text="Import from OpenTabletDriver", command=self._import_otd).grid(row=0, column=0)
        ttk.Button(bar, text="Save", command=self._save).grid(row=0, column=1, padx=4)
        self.driver_button = ttk.Button(bar, text="Start driver", command=self._toggle_driver)
        self.driver_button.grid(row=0, column=2)
        self.driver_state = ttk.Label(bar, text="")
        self.driver_state.grid(row=0, column=3, padx=(8, 0))
        self.status = ttk.Label(bar, text="", anchor="e")
        self.status.grid(row=0, column=4, sticky="e")

    # --- fields <-> config --------------------------------------------------

    def _load_into_fields(self):
        self.t_fields.set(self.cfg["tablet"])
        self.s_fields.set(self.cfg["screen"])
        self.clipping.set(self.cfg["clipping"])
        self.limiting.set(self.cfg["limiting"])
        self.tap_click.set(self.cfg["tap_click"])
        self._redraw()

    def _collect(self):
        t, s = self.t_fields.get(), self.s_fields.get()
        if t is None or s is None:
            raise ValueError("Every area field must be a number.")
        if min(t["width"], t["height"], s["width"], s["height"]) <= 0:
            raise ValueError("Area width and height must be greater than zero.")
        return {"tablet": t, "screen": s,
                "clipping": self.clipping.get(), "limiting": self.limiting.get(),
                "tap_click": self.tap_click.get()}

    def _save(self):
        try:
            self.cfg = self._collect()
            config.save(self.config_path, self.cfg)
        except (ValueError, OSError) as ex:
            messagebox.showerror("PotTabletLite", str(ex))
            return False
        running = win32.named_mutex_exists(MUTEX_NAME)
        self._set_status("Saved" + (" - applied to running driver" if running else ""))
        return True

    def _import_otd(self):
        try:
            t, s, clipping, limiting = config.import_opentabletdriver()
        except (OSError, ValueError, KeyError, TypeError) as ex:
            messagebox.showerror("PotTabletLite", "Could not import OpenTabletDriver settings:\n%s" % ex)
            return
        self.t_fields.set(t)
        self.s_fields.set(s)
        self.clipping.set(clipping)
        self.limiting.set(limiting)
        self._set_status("Imported - press Save to apply")

    # --- tablet helpers -----------------------------------------------------

    def _tablet_full(self):
        rot = (self.t_fields.get() or {}).get("rotation", 0.0)
        area = config.default_tablet_area()
        area["rotation"] = rot
        self.t_fields.set(area)

    def _tablet_center(self):
        t = self.t_fields.get()
        if t:
            t["x"], t["y"] = tablet.WIDTH_MM / 2, tablet.HEIGHT_MM / 2
            self.t_fields.set(t)

    def _tablet_match_aspect(self):
        t, s = self.t_fields.get(), self.s_fields.get()
        if not t or not s or s["width"] <= 0 or s["height"] <= 0:
            return
        aspect = s["height"] / s["width"]
        t["height"] = t["width"] * aspect
        if t["height"] > tablet.HEIGHT_MM:
            t["height"] = tablet.HEIGHT_MM
            t["width"] = t["height"] / aspect
        self.t_fields.set(t)

    # --- screen helpers -----------------------------------------------------

    def _screen_pick(self, _event=None):
        index = self.s_choice.current()
        if index <= 0:
            area = {"width": self.vw, "height": self.vh, "x": self.vw / 2, "y": self.vh / 2}
        else:
            area = win32.monitor_screen_area(self.monitors[index - 1])
        self.s_fields.set(area)

    # --- drawing ------------------------------------------------------------

    def _redraw(self):
        self._draw_tablet()
        self._draw_screen()

    def _draw_tablet(self):
        c, k, p = self.t_canvas, self.t_scale, CANVAS_PAD
        c.delete("all")
        c.create_rectangle(p, p, p + tablet.WIDTH_MM * k, p + tablet.HEIGHT_MM * k,
                           outline="#777", fill="#2b2b2b")
        t = self.t_fields.get()
        if not t:
            return
        r = math.radians(t["rotation"])
        cos_r, sin_r = math.cos(r), math.sin(r)
        points = []
        for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            dx *= t["width"] / 2
            dy *= t["height"] / 2
            points += [p + (t["x"] + dx * cos_r - dy * sin_r) * k,
                       p + (t["y"] + dx * sin_r + dy * cos_r) * k]
        c.create_polygon(points, outline="#4da3ff", fill="#21405f", width=2)
        c.create_text(p + t["x"] * k, p + t["y"] * k, fill="#ddd",
                      text="%s x %s mm" % (_fmt(t["width"], 2), _fmt(t["height"], 2)))

    def _draw_screen(self):
        c, k, p = self.s_canvas, self.s_scale, CANVAS_PAD
        c.delete("all")
        for i, m in enumerate(self.monitors, 1):
            x0 = p + (m["left"] - self.vx) * k
            y0 = p + (m["top"] - self.vy) * k
            c.create_rectangle(x0, y0, x0 + m["width"] * k, y0 + m["height"] * k,
                               outline="#777", fill="#2b2b2b")
            c.create_text(x0 + 6, y0 + 6, anchor="nw", fill="#888", text=str(i))
        s = self.s_fields.get()
        if not s:
            return
        x0 = p + (s["x"] - s["width"] / 2) * k
        y0 = p + (s["y"] - s["height"] / 2) * k
        c.create_rectangle(x0, y0, x0 + s["width"] * k, y0 + s["height"] * k,
                           outline="#4da3ff", fill="#21405f", width=2)
        c.create_text(p + s["x"] * k, p + s["y"] * k, fill="#ddd",
                      text="%dx%d" % (s["width"], s["height"]))

    # --- dragging -----------------------------------------------------------

    @staticmethod
    def _bind_drag(canvas, start, move):
        canvas.bind("<ButtonPress-1>", start)
        canvas.bind("<B1-Motion>", move)

    def _tablet_drag_start(self, event):
        self._drag = (event.x, event.y, self.t_fields.get())

    def _tablet_drag(self, event):
        x0, y0, area = self._drag
        if area:
            moved = dict(area, x=area["x"] + (event.x - x0) / self.t_scale,
                         y=area["y"] + (event.y - y0) / self.t_scale)
            self.t_fields.set(moved)

    def _screen_drag_start(self, event):
        self._drag = (event.x, event.y, self.s_fields.get())

    def _screen_drag(self, event):
        x0, y0, area = self._drag
        if area:
            moved = dict(area, x=round(area["x"] + (event.x - x0) / self.s_scale),
                         y=round(area["y"] + (event.y - y0) / self.s_scale))
            self.s_fields.set(moved)

    # --- driver process -----------------------------------------------------

    def _toggle_driver(self):
        if win32.named_mutex_exists(MUTEX_NAME):
            self._stop_driver()
        elif self._save():
            self._start_driver()
        self.root.after(300, self._poll_driver, False)

    def _start_driver(self):
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        python = sys.executable
        if os.path.basename(python).lower() == "pythonw.exe":
            python = os.path.join(os.path.dirname(python), "python.exe")
        log_path = os.path.join(os.path.dirname(self.config_path), "driver.log")
        try:
            log = open(log_path, "w")
            self.driver_process = subprocess.Popen(
                [python, "-m", "potlite.driver", "--config", self.config_path],
                cwd=package_root, stdout=log, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
            log.close()
        except OSError as ex:
            messagebox.showerror("PotTabletLite", "Could not start the driver:\n%s" % ex)

    def _stop_driver(self):
        try:
            with open(pid_path(self.config_path)) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            messagebox.showerror("PotTabletLite", "The driver is running but its process ID is unknown.\n"
                                                  "Close it from Task Manager (python.exe).")
            return
        win32.terminate_process(pid)
        try:
            os.remove(pid_path(self.config_path))
        except OSError:
            pass

    def _poll_driver(self, repeat=True):
        running = win32.named_mutex_exists(MUTEX_NAME)
        self.driver_button.configure(text="Stop driver" if running else "Start driver")
        self.driver_state.configure(text="Driver running" if running else "Driver stopped")
        if repeat:
            self.root.after(1000, self._poll_driver)

    def _set_status(self, text):
        self.status.configure(text=text)
        if self._status_job:
            self.root.after_cancel(self._status_job)
        self._status_job = self.root.after(4000, lambda: self.status.configure(text=""))


def main():
    if sys.platform != "win32":
        print("PotTabletLite only runs on Windows.", file=sys.stderr)
        return 1
    win32.set_dpi_aware()
    root = tk.Tk()
    App(root, config.DEFAULT_PATH)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
