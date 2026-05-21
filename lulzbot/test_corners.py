"""
Automatic test of all 4 bed corner washers.

For each corner:
  - Lift to a safe absolute Z
  - Move XY at that Z
  - Slowly descend by setting absolute Z lower in small steps
  - Poll M119 after each step; stop on z_min TRIGGERED
  - Lift back to safe Z, move on

Uses absolute moves throughout to avoid Marlin LE quirks with G91
persistence. Pre-flight checks confirm M211 S0 works and that M119
actually reports z_min on this firmware before we trust any results.

Usage:
    pkill -f session.py
    python3 -u test_corners.py
"""

import time
from printer import Printer

# Lulzbot Mini corner washer positions.
CORNERS = [
    ("back-left",   -3, 162),
    ("back-right", 160, 162),
    ("front-right", 160,  -9),
    ("front-left",   -3,  -9),
]

# Absolute Z values in Marlin's frame (after we declare Z=START_Z).
START_Z = 50.0          # We tell Marlin "current Z = this".
SAFE_Z = 60.0           # Lift target between corners (10mm above start).
MIN_Z = -20.0           # Lowest Z we'll descend to (80mm below safe).
DESCENT_STEP = 1.0      # mm per step (bigger so motion is visible).


def z_min_in_replies(replies):
    """Returns (found, triggered): whether z_min was reported, and state."""
    for r in replies:
        if "z_min" in r:
            return True, "TRIGGERED" in r
    return False, False


def main():
    p = Printer()
    try:
        # Pre-flight: confirm z_min is reportable on this firmware.
        print("[pre-flight] checking M119...", flush=True)
        replies = p.send("M119", echo=False)
        found, _ = z_min_in_replies(replies)
        if not found:
            print("[FATAL] M119 doesn't report z_min on this firmware. "
                  "Can't detect probe trigger. Aborting.", flush=True)
            return
        print("[pre-flight] z_min reporting confirmed.", flush=True)

        # Set known starting state.
        p.send("M211 S0")
        p.send("G28 X Y")
        p.send(f"G92 Z{START_Z}")
        # Absolute lift to safe Z.
        p.send(f"G0 Z{SAFE_Z} F600")

        results = []
        for name, x, y in CORNERS:
            print(f"\n=== {name} ({x}, {y}) ===", flush=True)
            # Re-assert M211 in case firmware re-enabled it.
            p.send("M211 S0", echo=False)
            # Move XY at safe Z.
            p.send(f"G0 X{x} Y{y} F4000", echo=False)
            # Slow absolute descent.
            current = SAFE_Z
            triggered_at = None
            while current - DESCENT_STEP >= MIN_Z:
                current -= DESCENT_STEP
                p.send(f"G0 Z{current} F60", echo=False)
                replies = p.send("M119", echo=False)
                found, triggered = z_min_in_replies(replies)
                if triggered:
                    triggered_at = SAFE_Z - current
                    break
            if triggered_at is not None:
                print(f"  [OK] contact at Z={current:.1f} "
                      f"({triggered_at:.1f}mm descent)", flush=True)
                results.append((name, triggered_at))
            else:
                print(f"  [FAIL] no contact between "
                      f"Z={SAFE_Z:.0f} and Z={MIN_Z:.0f}", flush=True)
                results.append((name, None))
            # Lift back to safe Z.
            p.send(f"G0 Z{SAFE_Z} F600", echo=False)

        # Park front-center.
        p.send(f"G0 X75 Y75 F4000", echo=False)

        # Summary.
        print("\n=== Summary ===", flush=True)
        for name, z in results:
            if z is None:
                print(f"  {name}: FAIL", flush=True)
            else:
                print(f"  {name}: triggered at -{z:.1f}mm", flush=True)
        okvals = [z for _, z in results if z is not None]
        if len(okvals) >= 2:
            spread = max(okvals) - min(okvals)
            print(f"\nSpread between hits: {spread:.1f}mm", flush=True)
    finally:
        p.close()


if __name__ == "__main__":
    main()
