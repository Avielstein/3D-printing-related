"""
Heat nozzle to a target temp, park the head in an easy-to-reach
position, and HOLD the connection open so the board doesn't reset.

Marlin/RAMBo resets every time the serial port is (re)opened, which
also drops the heater target. So if you want to keep the nozzle hot
while you scrub it, the port must stay open the whole time.

Usage:
    python3 heat_and_hold.py                # 150C, hold up to 15 min
    python3 heat_and_hold.py 170 20         # 170C, hold 20 min

While running, the script prints temp once per second. Ctrl-C to stop;
on exit it sends M104 S0 (heater off) before closing.
"""

import signal
import sys
import time

from printer import Printer


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    hold_min = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

    p = Printer()

    # Start heating immediately (non-blocking).
    p.send(f"M104 S{target}")
    # Home, raise, move forward-center for access.
    p.send("G28")
    p.send("G1 Z100 F1000")
    p.send("G1 X80 Y20 F4000")
    # Wait for moves to complete.
    p.send("M400")
    # Wait for hotend to reach target (blocking).
    print(f"[heat_and_hold] waiting for hotend to reach {target}C...")
    p.send(f"M109 S{target}")
    print(f"[heat_and_hold] ready. Scrub away. Holding for {hold_min} min.")

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
        print("[heat_and_hold] cooling: M104 S0")
        try:
            p.send("M104 S0")
        except Exception:
            pass
        p.close()


if __name__ == "__main__":
    main()
