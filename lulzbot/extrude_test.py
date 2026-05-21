"""
Heat to print temp, park the head, extrude a small amount of filament
to verify flow, then hold the connection open.

Defaults are tuned for TPU (flexible): 225C, slow extrusion (F50).
TPU buckles inside the extruder if pushed too fast.

Usage:
    python3 -u extrude_test.py                 # 225C, 10mm at F50
    python3 -u extrude_test.py 210 15 100      # 210C, 15mm at F100 (PLA)
"""

import signal
import sys
import time

from printer import Printer


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 225
    extrude_mm = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    feed = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    hold_min = 10.0

    p = Printer()

    # Start heating immediately.
    p.send(f"M104 S{target}")
    p.send("G28")
    p.send("G1 Z100 F1000")
    p.send("G1 X80 Y20 F4000")
    p.send("M400")

    # Wait for hotend to reach target.
    print(f"[extrude_test] heating to {target}C...", flush=True)
    p.send(f"M109 S{target}")
    print(f"[extrude_test] at temp. Extruding {extrude_mm}mm at F{feed}...",
          flush=True)

    # Relative extruder mode, then push filament.
    p.send("M83")
    p.send(f"G1 E{extrude_mm} F{feed}")
    p.send("M400")
    print("[extrude_test] extrusion done. Inspect the bead.", flush=True)
    print(f"[extrude_test] holding {hold_min} min. Ctrl-C to stop early.",
          flush=True)

    stop = {"flag": False}

    def handle(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    deadline = time.time() + hold_min * 60
    try:
        while not stop["flag"] and time.time() < deadline:
            p.send("M105", echo=False)
            time.sleep(2)
    finally:
        print("[extrude_test] cooling: M104 S0", flush=True)
        try:
            # Small retract so a hot strand doesn't keep oozing.
            p.send("G1 E-2 F300")
            p.send("M104 S0")
        except Exception:
            pass
        p.close()


if __name__ == "__main__":
    main()
