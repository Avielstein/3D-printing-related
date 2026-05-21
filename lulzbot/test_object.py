"""
Print a small 3D test object: a 20mm square outline, 5 layers tall.

Uses the same calibration + prime approach as test_print.py, then
traces the perimeter layer by layer, lifting Z by LAYER_H each loop.

The result is a small open-top box / wall structure that proves the
full chain works: heat, calibration, prime, first-layer adhesion,
multi-layer Z stepping, sustained extrusion.

Usage:
    pkill -f session.py
    python3 -u test_object.py
"""

import time
from printer import Printer

HOTEND_T = 225
BED_T = 60
CAL_X, CAL_Y = -3, -9
START_Z = 80.0
DESCENT_STEP = 1.0
MIN_Z = -10.0

# Object geometry. A 20mm square perimeter, centered around (60, 70).
SX, SY = 50.0, 60.0     # square start (front-left corner of square)
SIZE = 20.0             # mm side length
LAYERS = 5
LAYER_H = 0.4
FIRST_Z = 0.4           # first-layer Z (tighter than 1mm so it sticks)

# Extrusion: ~0.5mm line width × 0.4mm layer × 80mm perimeter = 16mm^3
# per layer. Filament 2.85mm dia => ~2.5mm of filament per layer.
E_PER_LAYER = 2.5
PRINT_FEED = 1500       # mm/min print speed (25mm/s)


def z_min_triggered(replies):
    for r in replies:
        if "z_min" in r and "TRIGGERED" in r:
            return True
    return False


def wipe_routine(p):
    """Run the Lulzbot Mini wiper-pad clean. Assumes head is at safe Z
    and nozzle is hot. Leaves head lifted to Z=10."""
    p.send("G0 X42 Y173 F11520")
    p.send("G0 Z1 F600")
    # Drag pattern across the pad.
    p.send("G1 X42 Y173 Z-0.5 F4000")
    p.send("G1 X52 Y171 Z-0.5 F4000")
    p.send("G1 X42 Y173 Z0 F4000")
    p.send("G1 X52 Y171 F4000")
    p.send("G1 X42 Y173 F4000")
    p.send("G1 X57 Y173 F4000")
    p.send("G1 X77 Y171 F4000")
    p.send("G1 X87 Y171 F4000")
    p.send("G1 X97 Y171 F4000")
    p.send("G1 X107 Y173 F4000")
    p.send("G1 X112 Y171 Z-0.5 F1000")
    p.send("G0 Z10 F600")


def main():
    p = Printer()
    try:
        print("[obj] heating + homing...", flush=True)
        p.send("M140 S{}".format(BED_T))
        p.send("M104 S{}".format(HOTEND_T))
        p.send("M211 S0")
        p.send("G28 X Y")
        p.send("G92 Z{}".format(START_Z))

        print("[obj] calibrating Z at corner...", flush=True)
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
            print("[FATAL] no calibration contact", flush=True)
            return
        # Declare washer top as Z=1 -> bed surface = Z=0.
        p.send("G92 Z1")
        p.send("G0 Z10 F600")

        print("[obj] waiting for full temps...", flush=True)
        p.send("M190 S{}".format(BED_T))
        p.send("M109 S{}".format(HOTEND_T))

        print("[obj] priming at wiper pad + wiping...", flush=True)
        # Move over the wiper pad area, extrude 30mm to clear bowden
        # (drips down into pad), then run the wipe pattern to scrub
        # the nozzle clean.
        p.send("G0 X42 Y170 F11520")
        p.send("G0 Z5 F600")
        p.send("M83")
        p.send("G1 E30 F75")
        # Small retract so travel to wipe pad doesn't drag a string.
        p.send("G1 E-2 F300")
        wipe_routine(p)

        # Move to square start at first layer height.
        x0, y0 = SX, SY
        x1, y1 = SX + SIZE, SY + SIZE
        print("[obj] moving to print start...", flush=True)
        p.send("G0 X{} Y{} F4000".format(x0, y0))
        p.send("G0 Z{} F600".format(FIRST_Z))
        # Push the retract back so filament is at the nozzle.
        p.send("G1 E2 F300")

        e_per_edge = E_PER_LAYER / 4.0
        for layer in range(LAYERS):
            z = FIRST_Z + layer * LAYER_H
            print(f"[obj] layer {layer + 1}/{LAYERS} at Z={z:.2f}",
                  flush=True)
            if layer > 0:
                p.send("G0 Z{} F600".format(z))
            # Trace perimeter, extruding.
            p.send(f"G1 X{x1} Y{y0} E{e_per_edge} F{PRINT_FEED}")
            p.send(f"G1 X{x1} Y{y1} E{e_per_edge} F{PRINT_FEED}")
            p.send(f"G1 X{x0} Y{y1} E{e_per_edge} F{PRINT_FEED}")
            p.send(f"G1 X{x0} Y{y0} E{e_per_edge} F{PRINT_FEED}")

        print("[obj] retract + wipe + park...", flush=True)
        # Big retract first so we don't drip on the print during travel.
        p.send("G1 E-6 F1500")
        # Lift well above the print top (max layer Z is about 2mm).
        p.send("G91")
        p.send("G0 Z30 F1500")
        p.send("G90")
        # Travel STRAIGHT BACK to the wiper pad. The pad is at Y~173,
        # print is at Y=60-80 — path doesn't cross the print.
        wipe_routine(p)
        p.send("M104 S0")
        p.send("M140 S0")
        # Park away from everything.
        p.send("G0 X150 Y150 F8000")
        print("[obj] done. Look for a small square wall at "
              f"({SX:.0f},{SY:.0f}) - ({SX + SIZE:.0f},{SY + SIZE:.0f}).",
              flush=True)
    finally:
        p.close()


if __name__ == "__main__":
    main()
