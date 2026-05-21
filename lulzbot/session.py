"""
Long-running interactive session. Holds the serial connection open
and watches a command file. Append G-code to the command file and the
script sends it to the printer; everything (commands sent + replies)
is logged to a single file we can both tail.

On startup it does an automatic Z calibration: homes XY, slowly
descends to the front-left corner washer until z_min triggers, then
declares Z=1 (washer top = 1mm above bed glass). After that, Marlin
has a working Z reference and the command loop begins.

Layout:
    /tmp/lulzbot_cmd   - append G-code here (one per line)
    /tmp/lulzbot_log   - tail this to see everything happening

Usage:
    python3 -u session.py > /tmp/lulzbot_log 2>&1 &
    echo "M114" >> /tmp/lulzbot_cmd
    tail -f /tmp/lulzbot_log

Special command: type EXIT in the cmd file to clean up and quit.
"""

import os
import signal
import time

from printer import Printer

CMD_PATH = "/tmp/lulzbot_cmd"
POLL = 0.2


def main():
    # Truncate command file so we start fresh.
    open(CMD_PATH, "w").close()

    p = Printer()

    stop = {"flag": False}

    def handle(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    # Auto-calibrate Z on startup. Handles the case where the head
    # is anywhere from bed-level to top-of-travel: declare a high
    # virtual Z, fast-descend to bed proximity, then slow-descend
    # until z_min triggers.
    print("[session] calibrating Z at front-left washer...", flush=True)
    p.send("M211 S0", echo=False)
    p.send("G28 X Y", echo=False)
    p.send("G92 Z150", echo=False)               # assume head is high
    p.send("G0 X-3 Y-9 F4000", echo=False)        # over washer
    p.send("G0 Z20 F1200", echo=False)            # fast: come close to bed
    # Slow descent in 0.5mm steps until contact, or until 30mm passed.
    current = 20.0
    contact = None
    while current > -10:
        current -= 0.5
        p.send(f"G0 Z{current} F60", echo=False)
        replies = p.send("M119", echo=False)
        triggered = any("z_min" in r and "TRIGGERED" in r for r in replies)
        if triggered:
            contact = current
            break
    if contact is None:
        print("[session] WARNING: no Z calibration contact; Z is uncalibrated.",
              flush=True)
    else:
        print(f"[session] Z contact at {contact:.1f}. Declaring as Z=1.",
              flush=True)
        p.send("G92 Z1", echo=False)
        p.send("G0 Z10 F600", echo=False)

    print(f"[session] ready. append G-code to {CMD_PATH}.", flush=True)
    print(f"[session] write 'EXIT' to quit cleanly.", flush=True)

    last_pos = 0
    last_z_poll = 0.0
    last_z_state = None

    while not stop["flag"]:
        # Pick up any new lines from the command file.
        try:
            size = os.path.getsize(CMD_PATH)
        except FileNotFoundError:
            size = 0

        if size > last_pos:
            with open(CMD_PATH) as fh:
                fh.seek(last_pos)
                data = fh.read()
                last_pos = fh.tell()
            for line in data.splitlines():
                cmd = line.strip()
                if not cmd:
                    continue
                if cmd.upper() == "EXIT":
                    stop["flag"] = True
                    break
                p.send(cmd)

        # Periodically poll z_min so we can see it live.
        now = time.time()
        if now - last_z_poll > 1.0:
            replies = p.send("M119", echo=False)
            for r in replies:
                if "z_min" in r:
                    state = "TRIGGERED" if "TRIGGERED" in r else "open"
                    if state != last_z_state:
                        print(f"[{time.strftime('%H:%M:%S')}] "
                              f"z_min: {state}", flush=True)
                        last_z_state = state
            last_z_poll = now

        time.sleep(POLL)

    print("[session] cleaning up...", flush=True)
    try:
        p.send("M104 S0")
        p.send("M140 S0")
        p.send("M84")
    finally:
        p.close()
    print("[session] done.", flush=True)


if __name__ == "__main__":
    main()
