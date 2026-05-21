"""
Quick end-to-end print test using our deep-descent calibration.

Sequence:
  1. Heat bed + hotend
  2. Auto-calibrate Z: move to front-left washer, slowly descend until
     z_min triggers, declare that position as Z=1 (washer is ~1mm
     above bed glass), lift.
  3. Move to a clear area of the bed.
  4. Extrude a single ~50mm test line at Z=0.3.
  5. Lift, cool, done.

If a clean TPU line appears on the bed, the entire chain works:
heat, motion, Z calibration, extrusion. From there you can scale to
real prints.

Usage:
    pkill -f session.py    # if running
    python3 -u test_print.py
"""

import time
from printer import Printer

# Hotend / bed targets for TPU.
HOTEND_T = 225
BED_T = 60

# Corner washer for calibration. Front-left is closest to "natural"
# print start area; using it as zero is convenient.
CAL_X, CAL_Y = -3, -9

# Starting Z (we tell Marlin "current Z is this" after homing XY).
START_Z = 80.0
DESCENT_STEP = 1.0
MIN_Z = -10.0

# Where to lay down the test line.
LINE_Y = 75.0
LINE_X_START = 30.0
LINE_X_END = 100.0
LAYER_Z = 1.0      # higher first layer (forgives bed-level error).
EXTRUDE_MM = 8.0   # more TPU so the thicker layer is visible.


def z_min_triggered(replies):
    for r in replies:
        if "z_min" in r and "TRIGGERED" in r:
            return True
    return False


def main():
    p = Printer()
    try:
        print("[print] starting heat...", flush=True)
        p.send("M140 S{}".format(BED_T))    # bed heat background
        p.send("M104 S{}".format(HOTEND_T)) # hotend heat background
        p.send("M211 S0")
        p.send("G28 X Y")
        p.send("G92 Z{}".format(START_Z))

        print("[print] calibrating Z at corner washer...", flush=True)
        p.send("G0 X{} Y{} F4000".format(CAL_X, CAL_Y), echo=False)
        current = START_Z
        contact_z = None
        while current - DESCENT_STEP >= MIN_Z:
            current -= DESCENT_STEP
            p.send("G0 Z{} F60".format(current), echo=False)
            replies = p.send("M119", echo=False)
            if z_min_triggered(replies):
                contact_z = current
                break
        if contact_z is None:
            print("[FATAL] no contact during calibration descent",
                  flush=True)
            return
        print("[print] contact at Z={:.1f}. Declaring as Z=1.".format(
              contact_z), flush=True)
        # Washer top is ~1mm above bed glass. Declare current Z = 1 so
        # Z=0 means bed surface.
        p.send("G92 Z1")
        # Lift safely.
        p.send("G0 Z10 F600")

        print("[print] waiting for full print temps...", flush=True)
        p.send("M190 S{}".format(BED_T))    # wait for bed
        p.send("M109 S{}".format(HOTEND_T)) # wait for hotend

        print("[print] priming filament (30mm slow)...", flush=True)
        # Park at a safe spot well above the bed, push 30mm of TPU
        # back through the hotend, then retract a tad to stop ooze.
        p.send("G0 X10 Y10 F4000")
        p.send("G0 Z20 F600")
        p.send("M83")  # relative extruder
        p.send("G1 E30 F75")
        p.send("G1 E-2 F300")  # small retract to stop ooze hanging

        print("[print] moving to line start...", flush=True)
        p.send("G0 X{} Y{} F4000".format(LINE_X_START, LINE_Y))
        p.send("G0 Z{} F600".format(LAYER_Z))

        print("[print] extruding test line...", flush=True)
        # Push filament back to compensate for retract, then extrude line.
        p.send("G1 E2 F300")
        line_length = LINE_X_END - LINE_X_START
        p.send("G1 X{} E{} F600".format(LINE_X_END, EXTRUDE_MM))

        print("[print] lifting + cooling...", flush=True)
        p.send("G91", echo=False)
        p.send("G0 Z10 F600", echo=False)
        p.send("G90", echo=False)
        p.send("M104 S0")
        p.send("M140 S0")
        # Park out of the way.
        p.send("G0 X10 Y10 F4000")
        print("[print] done. Check the bed for a TPU line at "
              "Y={}.".format(LINE_Y), flush=True)
    finally:
        p.close()


if __name__ == "__main__":
    main()
