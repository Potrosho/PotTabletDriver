"""Settings file and the tablet -> screen transform.

Areas use the same convention as OpenTabletDriver, so values can be copied
over directly:
  * tablet area: millimetres, X/Y is the centre of the area, rotation in degrees
  * screen area: pixels, X/Y is the centre of the area, measured from the
    top-left corner of the whole virtual desktop (all monitors)
"""

import json
import math
import os

from . import tablet

DEFAULT_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "PotTabletLite", "config.json")


def default_tablet_area():
    return {
        "width": tablet.WIDTH_MM,
        "height": tablet.HEIGHT_MM,
        "x": tablet.WIDTH_MM / 2,
        "y": tablet.HEIGHT_MM / 2,
        "rotation": 0.0,
    }


def default_config(screen_area):
    return {
        "tablet": default_tablet_area(),
        "screen": dict(screen_area),
        "clipping": True,
        "limiting": False,
        "tap_click": True,
    }


def load(path, default_screen):
    """Load settings, filling in anything missing. ``default_screen`` is a
    screen area dict used when the file has none (normally the primary monitor)."""
    cfg = default_config(default_screen)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cfg

    for key in ("tablet", "screen"):
        if isinstance(data.get(key), dict):
            for field, value in data[key].items():
                if field in cfg[key] and isinstance(value, (int, float)):
                    cfg[key][field] = float(value)
    for key in ("clipping", "limiting", "tap_click"):
        if isinstance(data.get(key), bool):
            cfg[key] = data[key]

    # A zero or negative size would break the transform; fall back to defaults.
    if cfg["tablet"]["width"] <= 0 or cfg["tablet"]["height"] <= 0:
        cfg["tablet"] = default_tablet_area()
    if cfg["screen"]["width"] <= 0 or cfg["screen"]["height"] <= 0:
        cfg["screen"] = dict(default_screen)
    return cfg


def save(path, cfg):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def import_opentabletdriver(settings_path=None):
    """Read the tablet and screen areas from an OpenTabletDriver settings.json.
    Returns (tablet_area, screen_area, clipping, limiting) or raises ValueError."""
    if settings_path is None:
        settings_path = os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "OpenTabletDriver", "settings.json")
    with open(settings_path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    profiles = data.get("Profiles") or []
    if isinstance(profiles, dict):
        profiles = list(profiles.values())
    if not profiles:
        raise ValueError("No tablet profiles found in OpenTabletDriver settings")

    # Prefer the profile for this tablet, otherwise take the first one.
    profile = next((p for p in profiles if p.get("Tablet") == tablet.NAME), profiles[0])
    abs_settings = profile.get("AbsoluteModeSettings")
    if not abs_settings:
        raise ValueError("Profile has no absolute mode settings")

    def area(src):
        return {
            "width": float(src["Width"]),
            "height": float(src["Height"]),
            "x": float(src["X"]),
            "y": float(src["Y"]),
            "rotation": float(src.get("Rotation", 0.0)),
        }

    tablet_area = area(abs_settings["Tablet"])
    screen_area = area(abs_settings["Display"])
    screen_area.pop("rotation")
    return (tablet_area, screen_area,
            bool(abs_settings.get("EnableClipping", True)),
            bool(abs_settings.get("EnableAreaLimiting", False)))


def build_transform(cfg, virtual_width, virtual_height):
    """Precompute everything the hot loop needs.

    Returns (a, b, c, d, e, f, min_x, min_y, max_x, max_y, clipping, limiting, tap_click)
    such that, for a raw tablet position (x, y):
        nx = a*x + b*y + c
        ny = d*x + e*y + f
    gives SendInput absolute coordinates (0..65535 across the virtual desktop),
    min/max are the screen area bounds in the same units, and tap_click says
    whether touching the tablet with the pen tip holds the left mouse button.

    This is the same math as OpenTabletDriver's AbsoluteOutputMode, folded into
    one affine matrix and pre-scaled to SendInput units.
    """
    t, s = cfg["tablet"], cfg["screen"]

    # raw units -> mm
    sx = tablet.WIDTH_MM / tablet.MAX_X
    sy = tablet.HEIGHT_MM / tablet.MAX_Y
    # mm -> pixels
    kx = s["width"] / t["width"]
    ky = s["height"] / t["height"]
    # pixels -> SendInput units
    nx = 65535.0 / virtual_width
    ny = 65535.0 / virtual_height

    r = -math.radians(t["rotation"])
    cos_r, sin_r = math.cos(r), math.sin(r)
    ix, iy = t["x"], t["y"]
    ox, oy = s["x"], s["y"]

    a = sx * cos_r * kx * nx
    b = -sy * sin_r * kx * nx
    c = ((-ix * cos_r + iy * sin_r) * kx + ox) * nx
    d = sx * sin_r * ky * ny
    e = sy * cos_r * ky * ny
    f = ((-ix * sin_r - iy * cos_r) * ky + oy) * ny

    min_x = (ox - s["width"] / 2) * nx
    max_x = (ox + s["width"] / 2) * nx
    min_y = (oy - s["height"] / 2) * ny
    max_y = (oy + s["height"] / 2) * ny

    limiting = bool(cfg["limiting"])
    clipping = bool(cfg["clipping"]) or limiting
    return (a, b, c, d, e, f, min_x, min_y, max_x, max_y, clipping, limiting,
            bool(cfg["tap_click"]))
