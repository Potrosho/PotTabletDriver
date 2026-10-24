"""XP-Pen Star G430S V2 specification and report parsing.

Values come from OpenTabletDriver's tablet configuration
(https://github.com/OpenTabletDriver/OpenTabletDriver, file
OpenTabletDriver.Configurations/Configurations/XP-Pen/Star G430S V2.json)
and its XP_PenReportParser / XP_PenTabletOverflowReport.
"""

import struct

NAME = "XP-Pen Star G430S V2"

VENDOR_ID = 0x28BD   # 10429
PRODUCT_ID = 0x0913  # 2323

INPUT_REPORT_LENGTH = 12
OUTPUT_REPORT_LENGTH = 12

# Output report that switches the tablet into its full-resolution vendor mode
# ("ArAE" in the OTD configuration).
OUTPUT_INIT_REPORT = bytes([0x02, 0xB0, 0x04])

WIDTH_MM = 101.6
HEIGHT_MM = 76.2
MAX_X = 10160
MAX_Y = 7620

# Report layout (12 bytes):
#   [0] report id   [1] status   [2:4] X low   [4:6] Y low
#   [6:8] pressure  [8] tilt X   [9] tilt Y    [10] X high  [11] Y high
# Only status and position are needed for cursor movement.
REPORT = struct.Struct("<xBHH4xBB")

STATUS_OUT_OF_RANGE = 0xC0
# Echoed init reports, idle reports and wireless dongle "on" reports.
STATUS_IGNORED = frozenset((0xB4, 0xF2, 0xF8))
STATUS_AUX_BIT = 0x10


def is_pen_status(status):
    """True when a report with this status byte carries a pen position."""
    return (status != STATUS_OUT_OF_RANGE
            and status not in STATUS_IGNORED
            and not status & STATUS_AUX_BIT)


def parse(report):
    """Return (x, y) in tablet units, or None if the report has no pen position."""
    status, x_lo, y_lo, x_hi, y_hi = REPORT.unpack_from(report)
    if not is_pen_status(status):
        return None
    return x_lo | x_hi << 16, y_lo | y_hi << 16
