"""
Lulzbot Mini library — serial transport + reusable routines.

Hardware: RAMBo controller, Marlin firmware, USB CDC at 250000 baud.
Opening the serial port pulses DTR and resets the board, so every
session starts with a boot-banner drain.

This module is the library. The CLI is `lulz.py`.

Public surface:
    Printer(port, baud)        — connection + send/recv (thread-safe send)
        .send(cmd, echo=False) — write a line, read replies until `ok`
        .close()
    parse_z_min(replies)       — bool | None (TRIGGERED / open / unknown)
    parse_temps(replies)       — {"T": float, "B": float}
    parse_position(replies)    — {"X": float, "Y": float, "Z": float, "E": float}
    wait_for_temp(p, target, axis="T", ...)
    wipe_nozzle(p)             — the validated TPU wipe move sequence
    z_calibrate(p, ...)        — deep-descent probe of front-left washer
    prep_tpu(p, ...)           — heat → retract → wipe → cool to probe temp
    jog_one(p, axis, mm)       — single relative jog on one axis (G91/G1/G90)
    safe_shutdown(p, *, close) — M410 + lift + heaters off; optionally close
    stream_gcode(p, path, ...) — line-by-line gcode streaming (cancellable)
    papertest_setup(p, ...)    — safety lift + G28-or-zcal + park at center
    papertest_offset(p, ...)   — read current Z, return recommended G92 offset

Everything routes through `Printer.log()`, which appends to
~/.lulzbot/last_run.log. That file is the full transcript; stdout
stays quiet unless the caller opts into echo.

Thread-safety: `Printer.send` is protected by an internal lock so a
single printer can be safely shared between the session's key-loop
thread and the optional FastAPI UI thread. Each call holds the lock
for one command's round-trip only.
"""

import os
import re
import sys
import threading
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
        self._lock = threading.Lock()
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

        Thread-safe: lock is held for one command's full round-trip
        (write + read-until-ok), so concurrent callers serialize per
        line. Holding it across read avoids interleaving replies.
        """
        if echo is None:
            echo = self.verbose
        cmd = cmd.strip()
        with self._lock:
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


def wipe_nozzle(p, z_offset=0.0):
    """The validated TPU wipe sequence. Assumes nozzle is already at wipe
    temperature and filament has been retracted. Lifts to Z=10 on exit.

    `z_offset` shifts every Z value in the sequence up by that many mm.
    The Cura-derived original (offset=0) assumes the wiper pad top sits
    at firmware Z=0 (i.e. exactly at bed-glass level). On printers where
    the pad sits HIGHER than bed glass — common after the paper-test
    `G92 Z<n>` patch — the nozzle dives through the pad into the rigid
    base. Bump `z_offset` up (e.g. 1.0 or 1.5) until the wipe touches
    but doesn't gouge.

    Use the session's `wipe [offset]` command to test interactively.
    Once you find a good number, set it as the matching constant in
    `start_gcode` of `gcode/lulzbot_mini_tpu.ini` for slicer prints.
    """
    moves = [
        ("G1 X42 Y173 F11520",           None),  # XY at wipe start, no Z change
        ("G1 Z{z:.2f} F1200",             0.0),   # approach pad top
        ("G1 X42 Y173 Z{z:.2f} F4000",   -0.5),   # press into pad
        ("G1 X52 Y171 Z{z:.2f} F4000",   -0.5),
        ("G1 X42 Y173 Z{z:.2f} F4000",    0.0),
        ("G1 X52 Y171 F4000",             None),
        ("G1 X42 Y173 F4000",             None),
        ("G1 X52 Y171 F4000",             None),
        ("G1 X57 Y173 F4000",             None),
        ("G1 X77 Y171 F4000",             None),
        ("G1 X87 Y171 F4000",             None),
        ("G1 X97 Y171 F4000",             None),
        ("G1 X107 Y173 F4000",            None),
        ("G1 X112 Y171 Z{z:.2f} F1000",  -0.5),   # final scrub
        ("G1 Z10 F600",                   None),  # safe lift
    ]
    for fmt, z_rel in moves:
        if z_rel is None:
            p.send(fmt)
        else:
            p.send(fmt.format(z=z_rel + z_offset))


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


# ---------- shared session / UI helpers ----------

# Per-axis jog feedrates (mm/min). Match the values used in cmd_jog so the
# session and the one-shot CLI feel identical.
JOG_FEED = {"X": 2000, "Y": 2000, "Z": 600, "E": 50}


def jog_one(p, axis, mm):
    """Send a single relative jog on one axis.

    Wraps the G91/G1/G90 triplet so callers (session key loop, UI
    endpoint, papertest sub-mode) don't repeat it. Each call ends with
    the printer back in absolute mode.
    """
    axis = axis.upper()
    if axis not in JOG_FEED:
        raise ValueError(f"axis must be one of {sorted(JOG_FEED)} (got {axis!r})")
    feed = JOG_FEED[axis]
    p.send("G91")
    p.send(f"G1 {axis}{mm} F{feed}")
    p.send("G90")


def safe_shutdown(p, *, close=True, lift_mm=10):
    """Single funnel for "stop everything safely" used by every abort path.

    Sends M410 (quickstop), best-effort Z lift, heaters off. If `close`,
    also closes the serial port. Each step swallows its own exception so
    a later step still runs if an earlier one fails (we'd rather get
    heaters off even if the lift fails).
    """
    for cmd in ("M410",                       # quickstop
                "G91",
                f"G1 Z{lift_mm} F600",         # best-effort lift
                "G90",
                "M104 S0",                     # hotend off
                "M140 S0"):                    # bed off
        try:
            p.send(cmd)
        except Exception:
            pass
    if close:
        try:
            p.close()
        except Exception:
            pass


def stream_gcode(p, path, *, on_progress=None, cancel_flag=None,
                 progress_percent_step=1, progress_time_step=0.5):
    """Stream a .gcode file line-by-line, reusing the held Printer.

    `on_progress(line_idx, total, elapsed, percent)` is called at most
    every `progress_percent_step` percent OR every `progress_time_step`
    seconds, whichever comes first.

    `cancel_flag` is any object with a `.is_set()` method (e.g.
    threading.Event); checked once per line. On cancel, sends M410 and
    returns the partial line count.

    Returns (lines_sent, total, elapsed_seconds, cancelled).

    Raises RuntimeError if the firmware reports `error` / `!!` on any
    line — caller decides what to do (abort, panic, etc.).
    """
    from pathlib import Path as _Path
    path = _Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    with open(path) as fh:
        raw_lines = fh.readlines()

    cmds = []
    for raw in raw_lines:
        line = raw.split(";", 1)[0].strip()
        if line:
            cmds.append(line)
    total = len(cmds)
    if total == 0:
        return (0, 0, 0.0, False)

    start = time.time()
    last_progress = start
    last_percent = -1

    for i, cmd in enumerate(cmds, 1):
        if cancel_flag is not None and cancel_flag.is_set():
            p.send("M410")
            return (i - 1, total, time.time() - start, True)

        replies = p.send(cmd)
        for r in replies:
            if r.lower().startswith(("error", "!!")):
                raise RuntimeError(
                    f"line {i}/{total}: printer reported `{r}` on `{cmd}`"
                )

        if on_progress is not None:
            percent = (i * 100) // total
            now = time.time()
            if percent >= last_percent + progress_percent_step \
                    or now - last_progress >= progress_time_step:
                on_progress(i, total, now - start, percent)
                last_percent = percent
                last_progress = now

    return (total, total, time.time() - start, False)


# ---------- paper-test workflow ----------

def safety_lift(p, mm=30):
    """Best-effort relative Z+ lift before any homing or probing.

    After a DTR reset Marlin doesn't know its real Z, so the head may
    be sitting close to the bed. This pushes it into known-safe
    airspace first. `M211 S0` is sent to disable software endstops
    (best-effort on this firmware fork — if it clamps, the move just
    stops at Z_MAX, which is still safe).

    Used by anything that's about to home or probe (`do_home`,
    `papertest_setup`, `do_print --zcal`).
    """
    p.send("M211 S0")
    p.send("G91")
    p.send(f"G1 Z{mm} F600")
    p.send("G90")


def papertest_setup(p, *, use_g28=False):
    """Prepare the printer for paper-test sweep, return reference Z.

    - Safety-lifts 30mm (in case prior commands left the head close).
    - Either runs G28 (matches real print flow) or `z_calibrate` (deep
      descent on the front-left washer) per `use_g28`.
    - Parks head at bed center (75, 75) at travel speed.

    Returns `(mode, post_ref_z)` where `mode` is "g28" or "zcal" and
    `post_ref_z` is the Marlin Z reading right after the chosen
    reference step. For zcal, this is implicitly 1.0 (set by G92 Z1
    inside z_calibrate) but we still read it back for symmetry.

    Raises RuntimeError if zcal can't find contact.
    """
    safety_lift(p)

    if use_g28:
        p.send("G28")
        mode = "g28"
    else:
        contact = z_calibrate(p)
        if contact is None:
            raise RuntimeError("zcal failed: no z_min contact within descent range")
        mode = "zcal"

    # Read Marlin's idea of Z right after the reference step.
    post_ref_z = parse_position(p.send("M114")).get("Z")
    if post_ref_z is None:
        raise RuntimeError("could not read post-reference Z from M114")

    p.send("G0 X75 Y75 F4000")
    if not use_g28:
        # zcal mode: drop nozzle to the firmware-declared bed glass
        # (Z=0). G28 mode: leave the head where G28 parked it so the
        # user jogs down explicitly from a safe height.
        p.send("G1 Z0 F300")

    return (mode, post_ref_z)


def papertest_offset(p, *, mode, post_ref_z):
    """After the user has found paper friction, read M114 and compute
    the `G92 Z<offset>` value to insert in start_gcode after G28.

    Returns `(z_sweet, offset)`:
      - `z_sweet`: the Marlin Z reading at the friction sweet spot.
      - `offset`: number to put after `G92 Z` in start_gcode so Marlin's
        Z=0 lands at the bed glass.

    Lift logic (Z+10) is intentionally NOT done here — callers may want
    to record without lifting, or lift differently. See `papertest_lift`.
    """
    z_sweet = parse_position(p.send("M114")).get("Z")
    if z_sweet is None:
        raise RuntimeError("could not read sweet-spot Z from M114")
    if mode == "g28":
        offset = post_ref_z - z_sweet
    else:
        # zcal mode declared washer-top as Z=1. If sweet spot reads
        # z_sweet, the washer is (1 - z_sweet) above bed glass; that's
        # exactly what we want for `G92 Z<offset>` after G28.
        offset = 1.0 - z_sweet
    return (z_sweet, offset)


def papertest_lift(p, mm=10):
    """Safe lift after sweet-spot recording or abort."""
    p.send("G91")
    p.send(f"G1 Z{mm} F600")
    p.send("G90")


# ---------- terminal progress bar helpers ----------

def fmt_dur(seconds):
    """Format a duration as MM:SS, or HH:MM:SS for prints over an hour."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def make_bar_printer():
    """Build an `on_progress` callback that draws a live single-line
    progress bar to stdout. Returns the callback. The caller must print
    a final newline when done — the bar uses \\r and never breaks lines.
    """
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    # Reserve room for: " 100% 12345/12345  12:34  ETA 12:34  123.4 ln/s"
    bar_width = max(10, min(40, cols - 60))

    def cb(i, total, elapsed, percent):
        filled = bar_width * percent // 100
        bar = "█" * filled + "░" * (bar_width - filled)
        rate = i / elapsed if elapsed > 0 else 0
        eta_s = (total - i) / rate if rate > 0 else 0
        line = (f"\r  [{bar}] {percent:3d}% {i:>6d}/{total}  "
                f"{fmt_dur(elapsed)}  ETA {fmt_dur(eta_s)}  "
                f"{rate:5.1f} ln/s")
        # Pad to terminal width so leftovers from prior longer lines
        # don't ghost on the right.
        sys.stdout.write(line + " " * max(0, cols - len(line) - 1))
        sys.stdout.flush()

    return cb


# ---------- shared print-job state ----------
#
# A single `PrintJob` instance is held by the Session and shared with
# the FastAPI UI (when started). Both the CLI bar and the browser UI
# read from the same source of truth, and a print started from one
# is visible (and cancellable) from the other.

class PrintJob:
    """State of the currently-running (or last-finished) print stream.

    Field invariants:
    - `running` is True from `reset()` until the worker thread's
      `finally` clears it. Use `is_active()` instead of inspecting
      thread liveness directly.
    - `cancel_event` is a `threading.Event`; setting it signals the
      worker to stop after the next line (via `stream_gcode`'s
      `cancel_flag` argument).
    - All fields are read/written from at most two threads (the worker
      and the reader), and reads of independent fields are atomic at
      the GIL level — no separate lock needed for snapshot reads.
    """

    def __init__(self):
        self.thread = None
        self.cancel_event = threading.Event()
        self.file = None
        self.line = 0
        self.total = 0
        self.percent = 0
        self.elapsed = 0.0
        self.started_at = None
        self.running = False
        self.cancelled = False
        self.error = None

    def reset(self, file):
        self.thread = None
        self.cancel_event = threading.Event()
        self.file = str(file)
        self.line = 0
        self.total = 0
        self.percent = 0
        self.elapsed = 0.0
        self.started_at = time.time()
        self.running = True
        self.cancelled = False
        self.error = None

    def is_active(self):
        return bool(self.running)

    def snapshot(self):
        return {
            "running": self.running,
            "cancelled": self.cancelled,
            "error": self.error,
            "file": self.file,
            "line": self.line,
            "total": self.total,
            "percent": self.percent,
            "elapsed": round(self.elapsed, 2),
        }
