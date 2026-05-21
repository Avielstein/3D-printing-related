"""
Drive the printer through a full Cura-style prep, then run G29 in
verbose mode so we see each corner probe's Z value live. Dumps the
stored bed-level matrix at the end (while connection is still open,
before the board resets and wipes it).

Output tells us:
- Did each of the 4 corner probes return a Z value? (probe works)
- Are the Z values reasonable, or 0 / NaN / "failed"? (contact ok?)
- Is the resulting matrix non-identity? (data was stored)

Usage:
    python3 -u auto_probe_test.py
"""

import signal
import time

from printer import Printer


def main():
    p = Printer()

    stop = {"flag": False}

    def handle(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    # Cura-style TPU prep: heat to wipe temp, retract to stop ooze.
    print("[auto_probe] starting prep sequence...", flush=True)
    p.send("M140 S60")           # bed heat in background
    p.send("M109 R180")          # heat extruder to wipe temp (waits)
    p.send("G28")                # home all
    p.send("G0 X0 Y187 Z156 F200")
    p.send("G1 E-30 F75")        # TPU anti-ooze retract
    # Wipe pad sequence (abridged).
    p.send("G1 X42 Y173 F11520")
    p.send("G1 Z0 F1200")
    for x1, x2 in [(42, 52), (57, 77), (87, 97), (107, 112)]:
        p.send(f"G1 X{x2} Y171 Z-0.5 F4000")
        p.send(f"G1 X{x1} Y173 Z-0.5 F4000")
    p.send("G1 Z10")
    p.send("G28 X0 Y0")
    p.send("G0 X0 Y187 F200")
    # Cool to probe temp, set probing accel.
    print("[auto_probe] cooling to probe temp...", flush=True)
    p.send("M109 R160")
    p.send("M204 S300")

    # Run G29 verbose — Marlin prints each probe point + Z value.
    print("[auto_probe] *** running G29 V4 (verbose probe) ***",
          flush=True)
    p.send("G29 V4")

    # Immediately query stored matrix BEFORE board reset wipes it.
    print("[auto_probe] *** matrix after probe ***", flush=True)
    p.send("M420 V")

    # Cleanup.
    print("[auto_probe] cooling down...", flush=True)
    p.send("M104 S0")
    p.send("M140 S0")
    p.send("M84")
    p.close()
    print("[auto_probe] done.", flush=True)


if __name__ == "__main__":
    main()
