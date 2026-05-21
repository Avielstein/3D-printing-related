"""
Live endstop status watcher. Polls M119 once a second and prints
whether z_min is TRIGGERED.

Lulzbot uses the nozzle-to-washer contact as the z_min probe. So if
you manually tap the (cool, idle) nozzle against any of the 4 bed
corner washers while this is running, z_min should flip to TRIGGERED.

If it never triggers, the probe circuit (nozzle wiring, washer
wiring, Z_MIN pin) is broken and no amount of cleaning will help.

Usage:
    python3 -u probe_continuity.py        # runs until Ctrl-C

Make sure the nozzle is cool first. Don't burn yourself.
"""

import signal
import time

from printer import Printer


def parse_z_min(replies):
    for r in replies:
        if "z_min" in r:
            return "TRIGGERED" in r
    return None


def main():
    p = Printer()

    stop = {"flag": False}

    def handle(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    # First, disable steppers so the user can move the head by hand.
    p.send("M84", echo=False)

    print("[probe] watching z_min. Tap nozzle on a corner washer.",
          flush=True)
    print("[probe] Ctrl-C to stop.", flush=True)

    last = None
    try:
        while not stop["flag"]:
            replies = p.send("M119", echo=False)
            state = parse_z_min(replies)
            if state != last:
                if state is True:
                    print(f"[{time.strftime('%H:%M:%S')}] z_min: "
                          f"TRIGGERED  <-- contact!", flush=True)
                elif state is False:
                    print(f"[{time.strftime('%H:%M:%S')}] z_min: open",
                          flush=True)
                last = state
            time.sleep(0.5)
    finally:
        p.close()


if __name__ == "__main__":
    main()
