#!/usr/bin/env python3
"""
lulz — single-command CLI for the Lulzbot Mini.

Quiet by default: each subcommand prints one summary line. Full
serial transcript is always written to ~/.lulzbot/last_run.log.

Workflow to print the calibration cube:
    lulz status                              # printer responsive?
    lulz home
    lulz zcal                                # deep-descent Z calibration
    lulz slice ../xyzCalibration_cube.stl
    lulz print ../xyzCalibration_cube.gcode

Live monitoring (opt-in only):
    lulz watch                               # streams every reply

Emergency:
    lulz panic                               # heaters/steppers off, quickstop
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from printer import (
    LOG_PATH,
    PORT,
    Printer,
    parse_position,
    parse_temps,
    parse_z_min,
    prep_tpu,
    wait_for_temp,
    wipe_nozzle,
    z_calibrate,
)


HERE = Path(__file__).resolve().parent
SLICE_INI = HERE / "gcode" / "lulzbot_mini_tpu.ini"


def _connect(args):
    return Printer(port=args.port, verbose=args.verbose)


# ---------- subcommands ----------

def cmd_status(args):
    p = _connect(args)
    try:
        fw = p.send("M115")
        temps = parse_temps(p.send("M105"))
        pos = parse_position(p.send("M114"))
        endstops = p.send("M119")
        z_state = parse_z_min(endstops)

        fw_line = next((r for r in fw if r.upper().startswith("FIRMWARE_NAME")), "?")
        z_word = {True: "TRIGGERED", False: "open", None: "?"}[z_state]
        print(f"firmware : {fw_line[:80]}")
        print(f"temps    : T={temps.get('T', '?')}  B={temps.get('B', '?')}")
        print(f"position : X={pos.get('X', '?')}  Y={pos.get('Y', '?')}  "
              f"Z={pos.get('Z', '?')}  E={pos.get('E', '?')}")
        print(f"z_min    : {z_word}")
    finally:
        p.close()


def cmd_home(args):
    p = _connect(args)
    try:
        p.send("G28 X Y")
        print("homed X and Y (Z: use `lulz zcal`)")
    finally:
        p.close()


def cmd_heat(args):
    p = _connect(args)
    try:
        p.send(f"M104 S{args.temp}")
        if args.wait:
            t = wait_for_temp(p, args.temp, axis="T")
            print(f"hotend reached {args.temp}C in {t:.1f}s")
        else:
            print(f"hotend target set to {args.temp}C (not waiting)")
    finally:
        p.close()


def cmd_cool(args):
    p = _connect(args)
    try:
        p.send("M104 S0")
        p.send("M140 S0")
        print("heaters off")
    finally:
        p.close()


def cmd_jog(args):
    axis = args.axis.upper()
    if axis not in "XYZE":
        sys.exit(f"axis must be X/Y/Z/E (got {args.axis})")
    feed = {"X": 2000, "Y": 2000, "Z": 600, "E": 50}[axis]
    p = _connect(args)
    try:
        p.send("G91")
        p.send(f"G1 {axis}{args.mm} F{feed}")
        p.send("G90")
        print(f"jogged {axis} by {args.mm} mm")
    finally:
        p.close()


def cmd_move(args):
    parts = [f"X{args.x}", f"Y{args.y}"]
    if args.z is not None:
        parts.append(f"Z{args.z}")
    p = _connect(args)
    try:
        p.send("G90")
        p.send("G1 " + " ".join(parts) + " F4000")
        print(f"moved to X={args.x} Y={args.y}"
              + (f" Z={args.z}" if args.z is not None else ""))
    finally:
        p.close()


def cmd_zcal(args):
    if not args.yes:
        print("WARNING: zcal descends the nozzle up to 40mm at the front-left")
        print("washer. If the z_min wire is loose (see README troubleshooting),")
        print("the nozzle can crash into the bed. Re-run with --yes to proceed.")
        sys.exit(1)
    p = _connect(args)
    try:
        contact = z_calibrate(p)
        if contact is None:
            sys.exit("zcal FAILED: no z_min contact within descent range")
        print(f"z contact at {contact:.2f} mm — declared Z=1, lifted to Z=10")
    finally:
        p.close()


def cmd_wipe(args):
    p = _connect(args)
    try:
        wipe_nozzle(p)
        print("wipe sequence complete")
    finally:
        p.close()


def cmd_prep(args):
    p = _connect(args)
    try:
        prep_tpu(p, wipe_temp=args.wipe_temp, probe_temp=args.probe_temp)
        print(f"prepped: wipe {args.wipe_temp}C → retract → wipe → cool {args.probe_temp}C")
    finally:
        p.close()


def cmd_extrude(args):
    p = _connect(args)
    try:
        p.send("M83")
        p.send(f"G1 E{args.mm} F{args.feed}")
        print(f"extruded {args.mm} mm at F{args.feed}")
    finally:
        p.close()


def cmd_print(args):
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"not a file: {path}")

    with open(path) as fh:
        raw_lines = fh.readlines()

    cmds = []
    for raw in raw_lines:
        line = raw.split(";", 1)[0].strip()
        if line:
            cmds.append(line)
    total = len(cmds)
    if total == 0:
        sys.exit("nothing to send (file is empty or all comments)")

    p = _connect(args)
    start = time.time()
    last_progress = start
    last_percent = -1
    try:
        for i, cmd in enumerate(cmds, 1):
            replies = p.send(cmd)
            for r in replies:
                if r.lower().startswith(("error", "!!")):
                    sys.exit(f"aborted at line {i}/{total}: printer reported "
                             f"`{r}` on `{cmd}`")
            percent = (i * 100) // total
            now = time.time()
            if percent >= last_percent + 5 or now - last_progress >= 30:
                print(f"  {percent:3d}% — line {i}/{total}  ({now - start:.0f}s)",
                      flush=True)
                last_percent = percent
                last_progress = now
        print(f"done: {total} commands in {time.time() - start:.0f}s")
    finally:
        p.close()


def cmd_slice(args):
    stl = Path(args.file).expanduser().resolve()
    if not stl.is_file():
        sys.exit(f"not a file: {stl}")
    if not SLICE_INI.is_file():
        sys.exit(f"slicer config missing: {SLICE_INI}")
    prusa = shutil.which("prusa-slicer")
    if prusa is None:
        # Fall back to the .app bundle.
        candidate = Path("/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer")
        if candidate.is_file():
            prusa = str(candidate)
        else:
            sys.exit("prusa-slicer not found. install: brew install --cask prusaslicer")
    out = stl.with_suffix(".gcode")
    cmd = [prusa, "--export-gcode", "--load", str(SLICE_INI),
           "--output", str(out), str(stl)]
    print(f"slicing {stl.name} → {out.name}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stdout)
        sys.stderr.write(res.stderr)
        sys.exit(f"slicer failed (exit {res.returncode})")
    print(f"sliced ok: {out}")


def cmd_watch(args):
    """Live tail: read /everything/ from serial until Ctrl-C."""
    print(f"watching {args.port} at 250000 baud. Ctrl-C to quit.")
    print("(this opens the port, which will reset the board)")
    p = Printer(port=args.port, verbose=True)
    try:
        while True:
            line = p.ser.readline().decode(errors="replace").strip()
            if line:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] {line}", flush=True)
                p.log(f"<< {line}")
    except KeyboardInterrupt:
        print("\nwatch stopped.")
    finally:
        p.close()


def cmd_panic(args):
    p = _connect(args)
    try:
        p.send("M410")      # quickstop
        p.send("M104 S0")   # hotend off
        p.send("M140 S0")   # bed off
        p.send("M84")       # steppers off
        print("PANIC: quickstop sent, heaters and steppers off")
    finally:
        p.close()


def cmd_raw(args):
    p = _connect(args)
    try:
        replies = p.send(args.gcode, echo=True)
        # In raw mode, also echo the result back (caller asked for it).
        if not args.verbose:
            for r in replies:
                print(f"<< {r}")
    finally:
        p.close()


def cmd_log(args):
    if not LOG_PATH.is_file():
        sys.exit(f"no log at {LOG_PATH}")
    # Just point the user at the file; don't re-print thousands of lines into stdout.
    print(LOG_PATH)


# ---------- argparse ----------

def build_parser():
    ap = argparse.ArgumentParser(prog="lulz", description=__doc__.strip(),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=PORT, help=f"serial port (default {PORT})")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="echo every printer reply to stdout (chatty)")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="firmware, temps, position, endstops").set_defaults(func=cmd_status)
    sub.add_parser("home", help="G28 X Y (Z handled by zcal)").set_defaults(func=cmd_home)

    h = sub.add_parser("heat", help="set hotend target")
    h.add_argument("temp", type=int)
    h.add_argument("--wait", action="store_true", help="block until target reached")
    h.set_defaults(func=cmd_heat)

    sub.add_parser("cool", help="hotend + bed off").set_defaults(func=cmd_cool)

    j = sub.add_parser("jog", help="relative move on one axis")
    j.add_argument("axis", help="X / Y / Z / E")
    j.add_argument("mm", type=float)
    j.set_defaults(func=cmd_jog)

    m = sub.add_parser("move", help="absolute move")
    m.add_argument("x", type=float)
    m.add_argument("y", type=float)
    m.add_argument("z", type=float, nargs="?", default=None)
    m.set_defaults(func=cmd_move)

    z = sub.add_parser("zcal", help="deep-descent Z calibration on front-left washer")
    z.add_argument("--yes", action="store_true",
                   help="confirm you've read the bed-crash warning")
    z.set_defaults(func=cmd_zcal)

    sub.add_parser("wipe", help="run the wipe-pad routine").set_defaults(func=cmd_wipe)

    pr = sub.add_parser("prep", help="full TPU heat → retract → wipe → cool prep")
    pr.add_argument("--wipe-temp", type=int, default=180)
    pr.add_argument("--probe-temp", type=int, default=160)
    pr.set_defaults(func=cmd_prep)

    e = sub.add_parser("extrude", help="slow extrusion (TPU-safe default feed)")
    e.add_argument("mm", type=float)
    e.add_argument("--feed", type=int, default=50)
    e.set_defaults(func=cmd_extrude)

    p = sub.add_parser("print", help="stream a .gcode file to the printer")
    p.add_argument("file")
    p.set_defaults(func=cmd_print)

    s = sub.add_parser("slice", help="slice an STL via PrusaSlicer using the Lulzbot Mini TPU profile")
    s.add_argument("file")
    s.set_defaults(func=cmd_slice)

    sub.add_parser("watch", help="live serial tail (opt-in monitoring)").set_defaults(func=cmd_watch)
    sub.add_parser("panic", help="quickstop + heaters off + steppers off").set_defaults(func=cmd_panic)

    r = sub.add_parser("raw", help="send arbitrary G-code")
    r.add_argument("gcode")
    r.set_defaults(func=cmd_raw)

    sub.add_parser("log", help=f"print path to last_run.log ({LOG_PATH})").set_defaults(func=cmd_log)

    return ap


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
