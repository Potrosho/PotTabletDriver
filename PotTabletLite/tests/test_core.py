"""Platform-independent tests: report parsing, area transform and the hot loop.

Run from the PotTabletLite folder:  python -m unittest discover tests
"""

import json
import math
import os
import struct
import tempfile
import unittest

from potlite import config, driver, tablet


def report(status, x, y, pressure=0):
    return struct.pack("<BBHHHbbBB", 0x02, status, x & 0xFFFF, y & 0xFFFF,
                       pressure, 0, 0, x >> 16, y >> 16)


def otd_transform(cfg, x, y):
    """Step-by-step copy of OpenTabletDriver's AbsoluteOutputMode math (pixels)."""
    t, s = cfg["tablet"], cfg["screen"]
    mx = x * tablet.WIDTH_MM / tablet.MAX_X - t["x"]
    my = y * tablet.HEIGHT_MM / tablet.MAX_Y - t["y"]
    r = -math.radians(t["rotation"])
    rx = mx * math.cos(r) - my * math.sin(r)
    ry = mx * math.sin(r) + my * math.cos(r)
    return (rx * s["width"] / t["width"] + s["x"],
            ry * s["height"] / t["height"] + s["y"])


SCREEN = {"width": 1920.0, "height": 1080.0, "x": 960.0, "y": 540.0}


class ParseTests(unittest.TestCase):
    def test_pen_report(self):
        self.assertEqual(tablet.parse(report(0xA0, 5080, 3810)), (5080, 3810))

    def test_high_bytes(self):
        self.assertEqual(tablet.parse(report(0xA1, 0x12345, 0x10001)), (0x12345, 0x10001))

    def test_non_pen_reports(self):
        for status in (0xC0, 0xB4, 0xF2, 0xF8, 0xF0, 0x10):
            self.assertIsNone(tablet.parse(report(status, 100, 100)), hex(status))

    def test_report_length(self):
        self.assertEqual(len(report(0xA0, 0, 0)), tablet.INPUT_REPORT_LENGTH)


class TransformTests(unittest.TestCase):
    def apply(self, cfg, x, y, vw=1920, vh=1080):
        a, b, c, d, e, f, *_ = config.build_transform(cfg, vw, vh)
        return (a * x + b * y + c) * vw / 65535, (d * x + e * y + f) * vh / 65535

    def test_full_area_corners(self):
        cfg = config.default_config(SCREEN)
        for (x, y), expected in (((0, 0), (0, 0)), ((tablet.MAX_X, tablet.MAX_Y), (1920, 1080)),
                                 ((tablet.MAX_X / 2, tablet.MAX_Y / 2), (960, 540))):
            px, py = self.apply(cfg, x, y)
            self.assertAlmostEqual(px, expected[0], places=6)
            self.assertAlmostEqual(py, expected[1], places=6)

    def test_matches_opentabletdriver(self):
        cfg = config.default_config({"width": 2560.0, "height": 1440.0, "x": 3200.0, "y": 720.0})
        cfg["tablet"] = {"width": 60.0, "height": 33.75, "x": 48.0, "y": 40.0, "rotation": 23.0}
        for x, y in ((0, 0), (1234, 5678), (10160, 7620), (5000, 100)):
            ours = self.apply(cfg, x, y, vw=4480, vh=1440)
            ref = otd_transform(cfg, x, y)
            self.assertAlmostEqual(ours[0], ref[0], places=6)
            self.assertAlmostEqual(ours[1], ref[1], places=6)

    def test_rotation_180_flips(self):
        cfg = config.default_config(SCREEN)
        cfg["tablet"]["rotation"] = 180.0
        px, py = self.apply(cfg, 0, 0)
        self.assertAlmostEqual(px, 1920, places=6)
        self.assertAlmostEqual(py, 1080, places=6)


class PumpTests(unittest.TestCase):
    def run_pump(self, cfg, reports, vw=1920, vh=1080):
        buf = bytearray(tablet.INPUT_REPORT_LENGTH)
        queue = list(reports)

        def read():
            if not queue:
                return False
            buf[:] = queue.pop(0)
            return True

        out = []
        driver.pump(read, buf, lambda x, y: out.append((x, y)),
                    [config.build_transform(cfg, vw, vh)])
        return out

    def test_moves_and_skips(self):
        cfg = config.default_config(SCREEN)
        out = self.run_pump(cfg, [
            report(0xA0, 0, 0),
            report(0xC0, 0, 0),           # out of range
            report(0xF0, 9999, 9999),     # aux buttons
            report(0xA0, 0, 0),           # same position: not re-sent
            report(0xA0, tablet.MAX_X, tablet.MAX_Y),
        ])
        self.assertEqual(out, [(0, 0), (65535, 65535)])

    def test_clipping(self):
        cfg = config.default_config(SCREEN)
        cfg["tablet"].update(width=50.0, height=28.125)
        out = self.run_pump(cfg, [report(0xA0, 0, 0)])
        self.assertEqual(out, [(0, 0)])

    def test_limiting(self):
        cfg = config.default_config(SCREEN)
        cfg["tablet"].update(width=50.0, height=28.125)
        cfg["limiting"] = True
        out = self.run_pump(cfg, [report(0xA0, 0, 0), report(0xA0, 5080, 3810)])
        self.assertEqual(out, [(32767, 32767)])

    def test_second_monitor(self):
        # Two 1920x1080 monitors side by side, area covers the right one.
        cfg = config.default_config({"width": 1920.0, "height": 1080.0, "x": 2880.0, "y": 540.0})
        out = self.run_pump(cfg, [report(0xA0, 0, 0)], vw=3840)
        self.assertEqual(out, [(32767, 0)])


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "config.json")
            cfg = config.default_config(SCREEN)
            cfg["tablet"]["width"] = 40.0
            config.save(path, cfg)
            self.assertEqual(config.load(path, SCREEN)["tablet"]["width"], 40.0)

            with open(path, "w") as f:
                json.dump({"tablet": {"width": 0}, "screen": {"height": -5}}, f)
            loaded = config.load(path, SCREEN)
            self.assertEqual(loaded["tablet"], config.default_tablet_area())
            self.assertEqual(loaded["screen"], SCREEN)

    def test_import_opentabletdriver(self):
        settings = {"Profiles": [{
            "Tablet": tablet.NAME,
            "AbsoluteModeSettings": {
                "Display": {"Width": 1920, "Height": 1080, "X": 960, "Y": 540, "Rotation": 0},
                "Tablet": {"Width": 48, "Height": 27, "X": 50.8, "Y": 38.1, "Rotation": 0},
                "EnableClipping": True, "EnableAreaLimiting": False,
            }}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w") as f:
                json.dump(settings, f)
            t, s, clipping, limiting = config.import_opentabletdriver(path)
        self.assertEqual(t["width"], 48.0)
        self.assertEqual(s, {"width": 1920.0, "height": 1080.0, "x": 960.0, "y": 540.0})
        self.assertTrue(clipping)
        self.assertFalse(limiting)


if __name__ == "__main__":
    unittest.main()
