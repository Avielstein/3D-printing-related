"""
Lulzbot Mini library — serial transport + reusable routines.

Hardware: RAMBo controller, Marlin firmware, USB CDC at 250000 baud.
Opening the serial port pulses DTR and resets the board, so every
session starts with a boot-banner drain.

This module is the library. The CLI is `lulz.py`.

Public surface:
    Printer(port, baud)        — connection + send/recv
        .send(cmd, echo=False) — write a line, read replies until `ok`
        .close()
    parse_z_min(replies)       — bool | None (TRIGGERED / open / unknown)
    parse_temps(replies)       — {"T": float, "B": float}
    parse_position(replies)    — {"X": float, "Y": float, "Z": float, "E": float}
    wait_for_temp(p, target, axis="T", ...)
    wipe_nozzle(p)             — the validated TPU wipe move sequence
    z_calibrate(p, ...)        — deep-descent probe of front-left washer
    prep_tpu(p, ...)           — heat → retract → wipe → cool to probe temp

Everything routes through `Printer.log()`, which appends to
~/.lulzbot/last_run.log. That file is the full transcript; stdout
stays quiet unless the caller opts into echo.
"""

import os
import re
import time
from pathlib import Path

import serial


PORT = "/dev/cu.usbmodem144101"
BAUD = 250000

LOG_DIR = Path.home() / ".lulzbot"
LOG_PATH = LOG_DIR / "last_run.log"


class Printer:
    def __init__(self, port=PORT, baud=BAUD, boot_wait=3.0, verbose=False):
        self.verbose = verbose
        LOG_DIR.mkdir(exist_ok=True)
        # Truncate the log at session start so it's always the current run.
        self._log_fh = open(LOG_PATH, "w", buffering=1)
        self.log(f"# session start {time.strftime('%Y-%m-%d %H:%M:%S')}")

        self.ser = serial.Serial(port, baud, timeout=2)
        deadline = time.time() + boot_wait
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if line:
                self.log(f"<< {line}")
                if self.verbose:
                    print(f"<< {line}")
            if line.lower().startswith("start"):
                break

    def log(self, line):
        self._log_fh.write(line + "\n")

    def send(self, cmd, echo=None):
        """Send a G-code line. Read replies until `ok` or error.

        echo: None → use self.verbose; True/False overrides per-call.
        Returns the list of reply lines (including the `ok`).
        """
        if echo is None:
            echo = self.verbose
        cmd = cmd.strip()
        self.log(f">> {cmd}")
        if echo:
            print(f">> {cmd}")
        self.ser.write((cmd + "\n").encode())
        return self._read_until_ok(echo=echo)

    def _read_until_ok(self, echo=False, timeout=30.0):
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if not line:
                continue
            self.log(f"<< {line}")
            if echo:
                print(f"<< {line}")
            lines.append(line)
            low = line.lower()
            if low.startswith("ok"):
                return lines
            if low.startswith(("error", "!!")):
                return lines
        return lines

    def close(self):
        try:
            self.ser.close()
        finally:
            self.log(f"# session end {time.strftime('%Y-%m-%d %H:%M:%S')}")
            self._log_fh.close()


# ---------- reply parsers ----------

def parse_z_min(replies):
    """Look at M119 output. True if z_min triggered, False if open, None if missing."""
    for r in replies:
        if "z_min" not in r:
            continue
        if "TRIGGERED" in r:
            return True
        return False
    return None


_TEMP_RE = re.compile(r"([TB]):(-?\d+\.?\d*)")

def parse_temps(replies):
    """M105 → {"T": hotend, "B": bed}. Missing fields omitted."""
    out = {}
    for r in replies:
        for axis, val in _TEMP_RE.findall(r):
            out[axis] = float(val)
    return out


_POS_RE = re.compile(r"([XYZE]):(-?\d+\.?\d*)")

def parse_position(replies):
    """M114 → {"X":..., "Y":..., "Z":..., "E":...}. Missing fields omitted.

    Marlin's M114 line is e.g. "X:75.00 Y:75.00 Z:10.00 E:0.00 Count X:0 Y:0 Z:200".
    The trailing `Count X:Y:Z:` reports step counters, not position — strip
    it before parsing or those values will overwrite the real coords.
    """
    out = {}
    for r in replies:
        head = r.split("Count")[0]
        for axis, val in _POS_RE.findall(head):
            out[axis] = float(val)
    return out


# ---------- reusable routines ----------

def wait_for_temp(p, target, axis="T", poll=2.0, timeout=600.0, tolerance=2.0):
    """Block until `axis` (T or B) reaches `target`. Silent — only the log
    sees the per-poll readings. Returns elapsed seconds.
    """
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
        temps = parse_temps(p.send("M105"))
        current = temps.get(axis)
        if current is not None and current >= target - tolerance:
            return time.time() - start
        time.sleep(poll)
    raise TimeoutError(f"{axis} did not reach {target} within {timeout}s")


def wipe_nozzle(p):
    """The validated TPU wipe sequence. Assumes nozzle is already at wipe
    temperature and filament has been retracted. Lifts to Z=10 on exit.
    """
    moves = [
        "G1 X42 Y173 F11520",
        "G1 Z0 F1200",
        "G1 X42 Y173 Z-0.5 F4000",
        "G1 X52 Y171 Z-0.5 F4000",
        "G1 X42 Y173 Z0 F4000",
        "G1 X52 Y171 F4000",
        "G1 X42 Y173 F4000",
        "G1 X52 Y171 F4000",
        "G1 X57 Y173 F4000",
        "G1 X77 Y171 F4000",
        "G1 X87 Y171 F4000",
        "G1 X97 Y171 F4000",
        "G1 X107 Y173 F4000",
        "G1 X112 Y171 Z-0.5 F1000",
        "G1 Z10 F600",
    ]
    for m in moves:
        p.send(m)


def z_calibrate(p, start_z=20.0, min_z=-40.0, step=0.5, washer_xy=(-3, -9)):
    """Deep-descent calibration on the front-left washer.

    Assumes user has homed XY. Declares Z=150 (head is high), moves to
    the washer, fast-descends to `start_z`, then slow-steps in `step`
    mm increments until z_min triggers OR `min_z` reached.

    Returns the contact Z on success, then runs G92 Z1 and lifts to Z=10.
    Returns None if no contact (caller should treat this as failure).
    """
    p.send("M211 S0")  # disable software endstops (best-effort)
    p.send("G28 X Y")
    p.send("G92 Z150")
    p.send(f"G0 X{washer_xy[0]} Y{washer_xy[1]} F4000")
    p.send(f"G0 Z{start_z} F1200")

    current = start_z
    while current > min_z:
        current -= step
        p.send(f"G0 Z{current:.2f} F60")
        if parse_z_min(p.send("M119")) is True:
            p.send("G92 Z1")
            p.send("G0 Z10 F600")
            return current
    return None


def prep_tpu(p, wipe_temp=180, probe_temp=160):
    """Heat → retract 30mm (TPU anti-ooze) → wipe → cool to probe temp.
    Mirrors the Cura LE TPU start sequence we validated by hand.
    """
    p.send(f"M104 S{wipe_temp}")
    p.send("G28")
    p.send(f"M109 R{wipe_temp}")  # wait for wipe temp
    p.send("G91")
    p.send("G1 E-30 F75")          # retract 30mm slowly
    p.send("G90")
    wipe_nozzle(p)
    p.send("G28 X Y")
    p.send(f"M109 R{probe_temp}")  # cool to probe temp
