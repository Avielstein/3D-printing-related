"""
Simple serial helper for talking to a Lulzbot (RAMBo / Marlin firmware).

Usage:
    python3 printer.py                  # interactive REPL
    python3 printer.py "M105"           # send one command
    python3 printer.py "M115" "M105"    # send several in order

Lulzbot RAMBo runs at 250000 baud. On port open the board resets, so we
wait for the "start" line (or a short timeout) before sending anything.
Every command gets an "ok" reply; we read until we see it.

Safe read-only commands to start with:
    M115   firmware info
    M105   hotend + bed temperatures
    M114   current XYZ position
    M119   endstop status

Movement / heater commands (only when you know what you're doing):
    G28              home all axes
    G1 X100 Y100 F3000   move to (100,100) at feedrate 3000
    M104 S200        set hotend target to 200C (no wait)
    M140 S60         set bed target to 60C
    M104 S0 / M140 S0    turn off heaters
    M84              disable steppers
    M112             EMERGENCY STOP (firmware halt, needs power cycle)
"""

import sys
import time
import serial

PORT = "/dev/cu.usbmodem144101"
BAUD = 250000


class Printer:
    def __init__(self, port=PORT, baud=BAUD, boot_wait=3.0):
        self.ser = serial.Serial(port, baud, timeout=2)
        # Marlin resets on port open. Drain the boot banner.
        deadline = time.time() + boot_wait
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if line:
                print(f"<< {line}")
            if line.lower().startswith("start"):
                break

    def send(self, cmd, echo=True):
        cmd = cmd.strip()
        if echo:
            print(f">> {cmd}")
        self.ser.write((cmd + "\n").encode())
        return self._read_until_ok(echo=echo)

    def _read_until_ok(self, echo=True, timeout=10.0):
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if not line:
                continue
            if echo:
                print(f"<< {line}")
            lines.append(line)
            if line.lower().startswith("ok"):
                return lines
            if line.lower().startswith(("error", "!!")):
                return lines
        return lines

    def close(self):
        self.ser.close()


def repl(p):
    print("Type G-code, or 'quit'. M112 is emergency stop.")
    while True:
        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cmd:
            continue
        if cmd.lower() in ("quit", "exit", "q"):
            break
        p.send(cmd)


def main():
    p = Printer()
    try:
        if len(sys.argv) > 1:
            for cmd in sys.argv[1:]:
                p.send(cmd)
        else:
            repl(p)
    finally:
        p.close()


if __name__ == "__main__":
    main()
