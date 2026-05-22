#!/usr/bin/env python3
"""
lulz — single-command CLI for the Lulzbot Mini.

Quiet by default: each subcommand prints one summary line. Full
serial transcript is always written to ~/.lulzbot/last_run.log.

Workflow to print the calibration cube via OctoPrint:
    lulz host start                          # start OctoPrint (auto-picks free port)
    # → open the printed URL, upload sliced .gcode, click Print

For direct serial control (OctoPrint disconnected):
    lulz status                              # printer responsive?
    lulz home
    lulz zcal --yes                          # deep-descent Z calibration
    lulz slice ../xyzCalibration_cube.stl
    lulz print ../xyzCalibration_cube.gcode

Live monitoring (opt-in only):
    lulz watch                               # streams every serial reply

Emergency:
    lulz panic                               # heaters/steppers off, quickstop
"""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
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


def _resolve_port(requested):
    """Return `requested` if it exists. Otherwise, if exactly one
    /dev/cu.usbmodem* device is present, use that (macOS reshuffles the
    index between reconnects, so the hardcoded default goes stale).
    """
    if Path(requested).exists():
        return requested
    candidates = sorted(Path("/dev").glob("cu.usbmodem*"))
    if len(candidates) == 1:
        chosen = str(candidates[0])
        print(f"note: {requested} not present; auto-detected {chosen}",
              file=sys.stderr)
        return chosen
    if not candidates:
        sys.exit(f"no serial device found. {requested} doesn't exist and "
                 f"no /dev/cu.usbmodem* devices are present. "
                 f"check USB cable and printer power.")
    sys.exit(f"{requested} not present and {len(candidates)} candidate "
             f"devices found ({', '.join(str(c) for c in candidates)}); "
             f"specify --port explicitly.")


def _connect(args):
    port = _resolve_port(args.port)
    return Printer(port=port, verbose=args.verbose)


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
        if args.zcal:
            # Run zcal in the same serial session so its G92 Z=1 reference
            # persists across the streamed print (no DTR reset between).
            print("[zcal] safety lift...", flush=True)
            p.send("M211 S0")
            p.send("G91")
            p.send("G1 Z30 F600")
            p.send("G90")
            print("[zcal] probing front-left washer...", flush=True)
            contact = z_calibrate(p)
            if contact is None:
                sys.exit("zcal FAILED: no z_min contact, aborting before print")
            print(f"[zcal] contact at {contact:.2f}, Z=1 set, lifted to Z=10",
                  flush=True)

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


def cmd_papertest(args):
    """Interactive paper-test calibration of the bed-glass Z offset.

    All operations happen in ONE serial session (no DTR reset between
    steps), so the G92 Z=1 reference from zcal persists through the
    move + jog + M114 sequence.

    After the user finds the paper-friction sweet spot, we read M114 and
    print the offset needed in start_gcode (`G92 Z<offset>` right after G28).
    """
    if not args.yes:
        print("WARNING: papertest runs zcal first (descends nozzle up to 40mm")
        print("at the front-left washer). Re-run with --yes to proceed.")
        sys.exit(1)

    p = _connect(args)
    post_ref_z = None  # Marlin Z right after calibration (G28 mode only)
    try:
        # Safety lift: previous commands may have left the head close to the
        # bed. After DTR reset, Marlin doesn't know its position.
        print("safety lift (relative +30mm Z)...")
        p.send("M211 S0")
        p.send("G91")
        p.send("G1 Z30 F600")
        p.send("G90")

        if args.use_g28:
            print("running G28 (probe-based Z homing — matches print flow)...")
            p.send("G28")
            post_ref_z = parse_position(p.send("M114")).get("Z", 0)
            print(f"after G28: Marlin Z = {post_ref_z:.3f}")
            print("moving to bed center...")
            p.send("G0 X75 Y75 F4000")
            # Don't auto-descend — user jogs from here to find paper friction
            print(f"head is at Marlin Z={post_ref_z:.3f} above center bed.")
            print("jog DOWN to find paper friction.")
        else:
            contact = z_calibrate(p)
            if contact is None:
                sys.exit("zcal FAILED: no z_min contact within descent range")
            print(f"z contact at {contact:.2f} mm — declared Z=1 (washer top)")
            print("moving to bed center...")
            p.send("G0 X75 Y75 F4000")
            print("dropping to presumed bed glass (Z=0)...")
            p.send("G1 Z0 F300")

        print()
        print("Slide paper between nozzle and bed. Commands:")
        print("  d [N]       descend N mm (default 0.1)")
        print("  u [N]       ascend N mm (default 0.1)")
        print("  z           report current Z (M114)")
        print("  done        record current Z as sweet spot, lift and exit")
        print("  abort       lift and exit without recording")
        print()

        while True:
            try:
                line = input("paper> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                line = "abort"
            if not line:
                continue
            parts = line.split()
            cmd = parts[0]
            try:
                arg = float(parts[1]) if len(parts) > 1 else 0.1
            except ValueError:
                print(f"bad number: {parts[1]}")
                continue

            if cmd in ("d", "down"):
                p.send("G91")
                p.send(f"G1 Z-{arg} F300")
                p.send("G90")
            elif cmd in ("u", "up"):
                p.send("G91")
                p.send(f"G1 Z{arg} F300")
                p.send("G90")
            elif cmd == "z":
                pos = parse_position(p.send("M114"))
                print(f"  Z = {pos.get('Z', '?')}")
            elif cmd == "done":
                pos = parse_position(p.send("M114"))
                z = pos.get("Z")
                p.send("G91")
                p.send("G1 Z10 F600")
                p.send("G90")
                if z is None:
                    sys.exit("could not read Z from M114")
                print()
                print(f"sweet-spot Z = {z:.3f}")
                if args.use_g28:
                    # G28 mode: head physically descended by (post_ref_z - z)
                    # from post-G28 position to sweet-spot (bed glass).
                    # The patch makes Marlin's Z=0 land at bed glass.
                    patch = post_ref_z - z
                    print(f"post-G28 Z was: {post_ref_z:.3f}")
                    print(f"=> patch start_gcode to: G28\\nG92 Z{patch:.3f}\\n...")
                else:
                    # zcal mode: zcal declared washer-top as Z=1. If sweet-spot
                    # (= bed glass) reads Z = z_sweet, washer is (1 - z_sweet)
                    # above bed. In start_gcode after G28 (firmware Z=0 at
                    # washer top), `G92 Z<1 - z_sweet>` makes Z=0 = bed glass.
                    offset = 1.0 - z
                    print(f"actual washer-to-bed distance = {offset:.3f} mm")
                    print(f"=> patch start_gcode to: G28\\nG92 Z{offset:.3f}\\n...")
                print(f"(tell me this number — i'll patch the .ini)")
                break
            elif cmd in ("abort", "q", "quit"):
                p.send("G91")
                p.send("G1 Z10 F600")
                p.send("G90")
                break
            else:
                print(f"unknown: {cmd}")
    finally:
        p.close()


# ---------- OctoPrint host management ----------
#
# OctoPrint defaults to port 5000, which collides with macOS AirPlay
# Receiver (Control Center). We pick the first free port in 5001-5020
# automatically so a conflict never leaves the user stuck.

OCTO_VENV = Path.home() / ".octoprint-venv"
OCTO_LOG = Path.home() / ".octoprint" / "serve.log"
OCTO_PORT_RANGE = range(5001, 5021)


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pick_free_port():
    for p in OCTO_PORT_RANGE:
        if _port_free(p):
            return p
    return None


def _remember_port(port):
    """Record the last-used port to ~/.octoprint/last_port so `host status`
    can find it. The actual server port is set via `octoprint serve --port`
    on the CLI (config.yaml's server.port doesn't propagate reliably to
    OctoPrint's intermediary startup server, so we bypass config for port).
    """
    (Path.home() / ".octoprint" / "last_port").write_text(str(port))


def _remembered_port():
    p = Path.home() / ".octoprint" / "last_port"
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        return None


def _octoprint_pid():
    """Return the PID of a running `octoprint serve`, or None."""
    try:
        res = subprocess.run(["pgrep", "-f", "octoprint serve"],
                             capture_output=True, text=True)
    except FileNotFoundError:
        return None
    for line in res.stdout.split():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def _octoprint_responds(port, timeout=1.0):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout)
        return True
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def cmd_host(args):
    action = args.action
    if action == "status":
        pid = _octoprint_pid()
        port = _remembered_port()
        if pid and port and _octoprint_responds(port):
            print(f"OctoPrint running: http://127.0.0.1:{port}/  (pid {pid})")
        elif pid:
            print(f"OctoPrint process running (pid {pid}) but not responding")
        else:
            print("OctoPrint not running")
        return

    if action == "stop":
        pid = _octoprint_pid()
        if not pid:
            print("OctoPrint not running")
            return
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if _octoprint_pid() is None:
                print(f"OctoPrint stopped (pid {pid})")
                return
            time.sleep(0.5)
        print(f"OctoPrint did not stop within 10s (pid {pid}); try `kill -9 {pid}`")
        return

    if action == "start":
        if _octoprint_pid() is not None:
            port = _remembered_port()
            if port and _octoprint_responds(port):
                print(f"OctoPrint already running: http://127.0.0.1:{port}/")
                return
            sys.exit("an octoprint process exists but isn't responding; "
                     "stop it first with `lulz host stop`")

        octoprint_bin = OCTO_VENV / "bin" / "octoprint"
        if not octoprint_bin.is_file():
            sys.exit(f"OctoPrint not installed at {OCTO_VENV}. "
                     f"see lulzbot/OCTOPRINT.md for one-time setup.")

        port = _pick_free_port()
        if port is None:
            sys.exit(f"no free port in {OCTO_PORT_RANGE.start}-{OCTO_PORT_RANGE.stop - 1}")

        OCTO_LOG.parent.mkdir(exist_ok=True)
        with open(OCTO_LOG, "w") as fh:
            proc = subprocess.Popen(
                [str(octoprint_bin), "serve", "--port", str(port)],
                stdout=fh, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _remember_port(port)

        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                sys.exit(f"OctoPrint exited (code {proc.returncode}). see {OCTO_LOG}")
            if _octoprint_responds(port, timeout=0.5):
                print(f"OctoPrint up: http://127.0.0.1:{port}/  (pid {proc.pid})")
                return
            time.sleep(1)
        sys.exit(f"OctoPrint did not respond within 120s. see {OCTO_LOG}")


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
    p.add_argument("--zcal", action="store_true",
                   help="run zcal calibration before streaming (in same serial session, so reference persists)")
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

    pt = sub.add_parser("papertest", help="interactive paper-test bed-glass Z calibration")
    pt.add_argument("--yes", action="store_true", help="confirm you've read the zcal warning")
    pt.add_argument("--use-g28", action="store_true",
                    help="use G28 (probe-based Z homing) instead of zcal — matches start_gcode flow")
    pt.set_defaults(func=cmd_papertest)

    host = sub.add_parser("host", help="manage OctoPrint server (auto-picks free port)")
    host.add_argument("action", choices=["start", "stop", "status"])
    host.set_defaults(func=cmd_host)

    return ap


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
