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
    jog_one,
    papertest_lift,
    papertest_offset,
    papertest_setup,
    parse_position,
    parse_temps,
    parse_z_min,
    prep_tpu,
    safe_shutdown,
    safety_lift,
    stream_gcode,
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
#
# Each subcommand is split into a `do_*(p, ...)` core that takes an
# already-connected `Printer` and a `cmd_*(args)` wrapper used by argparse
# that handles `_connect` / `p.close()`. The session ([session.py](session.py))
# imports `do_*` directly so it can run any command against its held printer
# without re-opening the serial port (which would DTR-reset the board).

def do_status(p):
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


def cmd_status(args):
    p = _connect(args)
    try:
        do_status(p)
    finally:
        p.close()


def do_home(p):
    """Reset to a safe idle state: heaters off, then safety lift, then
    full G28 (homes X, Y, and Z via the firmware probe routine). Matches
    what start_gcode does at the top of a print, plus turns off heat so
    `home` is a "park the machine cleanly" verb you can run anytime.
    """
    p.send("M104 S0")             # hotend target → 0
    p.send("M140 S0")             # bed target → 0
    safety_lift(p)
    p.send("G28")
    print("homed X Y Z, heaters off (safety lift + G28)")


def cmd_home(args):
    p = _connect(args)
    try:
        do_home(p)
    finally:
        p.close()


def do_heat(p, temp, wait=False):
    p.send(f"M104 S{temp}")
    if wait:
        t = wait_for_temp(p, temp, axis="T")
        print(f"hotend reached {temp}C in {t:.1f}s")
    else:
        print(f"hotend target set to {temp}C (not waiting)")


def cmd_heat(args):
    p = _connect(args)
    try:
        do_heat(p, args.temp, wait=args.wait)
    finally:
        p.close()


def do_cool(p):
    p.send("M104 S0")
    p.send("M140 S0")
    print("heaters off")


def cmd_cool(args):
    p = _connect(args)
    try:
        do_cool(p)
    finally:
        p.close()


def do_jog(p, axis, mm):
    """Single relative jog. Reuses the shared printer.jog_one helper so
    the session arrow-key loop and the CLI `lulz jog` command share the
    same G91/G1/G90 sequence and feedrates.
    """
    jog_one(p, axis, mm)
    print(f"jogged {axis.upper()} by {mm} mm")


def cmd_jog(args):
    axis = args.axis.upper()
    if axis not in "XYZE":
        sys.exit(f"axis must be X/Y/Z/E (got {args.axis})")
    p = _connect(args)
    try:
        do_jog(p, axis, args.mm)
    finally:
        p.close()


def do_move(p, x, y, z=None):
    parts = [f"X{x}", f"Y{y}"]
    if z is not None:
        parts.append(f"Z{z}")
    p.send("G90")
    p.send("G1 " + " ".join(parts) + " F4000")
    print(f"moved to X={x} Y={y}" + (f" Z={z}" if z is not None else ""))


def cmd_move(args):
    p = _connect(args)
    try:
        do_move(p, args.x, args.y, args.z)
    finally:
        p.close()


def do_zcal(p):
    contact = z_calibrate(p)
    if contact is None:
        sys.exit("zcal FAILED: no z_min contact within descent range")
    print(f"z contact at {contact:.2f} mm — declared Z=1, lifted to Z=10")


def cmd_zcal(args):
    if not args.yes:
        print("WARNING: zcal descends the nozzle up to 40mm at the front-left")
        print("washer. If the z_min wire is loose (see README troubleshooting),")
        print("the nozzle can crash into the bed. Re-run with --yes to proceed.")
        sys.exit(1)
    p = _connect(args)
    try:
        do_zcal(p)
    finally:
        p.close()


def do_wipe(p):
    wipe_nozzle(p)
    print("wipe sequence complete")


def cmd_wipe(args):
    p = _connect(args)
    try:
        do_wipe(p)
    finally:
        p.close()


def do_prep(p, wipe_temp=180, probe_temp=160):
    prep_tpu(p, wipe_temp=wipe_temp, probe_temp=probe_temp)
    print(f"prepped: wipe {wipe_temp}C → retract → wipe → cool {probe_temp}C")


def cmd_prep(args):
    p = _connect(args)
    try:
        do_prep(p, wipe_temp=args.wipe_temp, probe_temp=args.probe_temp)
    finally:
        p.close()


def do_extrude(p, mm, feed=50):
    p.send("M83")
    p.send(f"G1 E{mm} F{feed}")
    print(f"extruded {mm} mm at F{feed}")


def cmd_extrude(args):
    p = _connect(args)
    try:
        do_extrude(p, args.mm, feed=args.feed)
    finally:
        p.close()


def do_print(p, file, zcal=False, cancel_flag=None):
    """Stream a .gcode file with a live progress bar. Reuses
    `printer.stream_gcode` so the session and the FastAPI UI share the
    same streaming logic, just with different `on_progress` sinks.

    This is the *synchronous* one-shot path used by `lulz print …` from
    the OS shell. The interactive session uses `Session._start_print`
    instead, which runs the stream in a background thread so the UI can
    be started mid-print.
    """
    from printer import make_bar_printer, fmt_dur
    if zcal:
        # Run zcal in the same serial session so its G92 Z=1 reference
        # persists across the streamed print (no DTR reset between).
        print("[zcal] safety lift...", flush=True)
        safety_lift(p)
        print("[zcal] probing front-left washer...", flush=True)
        contact = z_calibrate(p)
        if contact is None:
            sys.exit("zcal FAILED: no z_min contact, aborting before print")
        print(f"[zcal] contact at {contact:.2f}, Z=1 set, lifted to Z=10",
              flush=True)

    label = Path(file).name
    print(f"streaming {label}")
    try:
        sent, total, elapsed, cancelled = stream_gcode(
            p, file,
            on_progress=make_bar_printer(),
            cancel_flag=cancel_flag,
        )
    except RuntimeError as e:
        print()
        sys.exit(f"aborted: {e}")
    except KeyboardInterrupt:
        print()
        raise
    print()
    if total == 0:
        sys.exit("nothing to send (file is empty or all comments)")
    if cancelled:
        print(f"cancelled: {sent}/{total} lines in {fmt_dur(elapsed)}")
    else:
        print(f"done: {total} lines in {fmt_dur(elapsed)} "
              f"(avg {total/max(elapsed,0.001):.1f} ln/s)")


def cmd_print(args):
    p = _connect(args)
    try:
        do_print(p, args.file, zcal=args.zcal)
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


def do_panic(p):
    """Stop motion + heaters + steppers, but DO NOT close the port.

    The session calls this on the `p` shortcut so the user can keep
    using arrow keys afterward. The argparse one-shot wraps it with
    a close.
    """
    p.send("M410")      # quickstop
    p.send("M104 S0")   # hotend off
    p.send("M140 S0")   # bed off
    p.send("M84")       # steppers off
    print("PANIC: quickstop sent, heaters and steppers off")


def cmd_panic(args):
    p = _connect(args)
    try:
        do_panic(p)
    finally:
        p.close()


def do_raw(p, gcode, echo_to_stdout=True):
    replies = p.send(gcode, echo=True)
    if echo_to_stdout:
        # In raw mode, also echo the result back to stdout (caller asked
        # for it). When verbose is already on, `p.send` already echoed.
        for r in replies:
            print(f"<< {r}")
    return replies


def cmd_raw(args):
    p = _connect(args)
    try:
        do_raw(p, args.gcode, echo_to_stdout=not args.verbose)
    finally:
        p.close()


def cmd_log(args):
    if not LOG_PATH.is_file():
        sys.exit(f"no log at {LOG_PATH}")
    # Just point the user at the file; don't re-print thousands of lines into stdout.
    print(LOG_PATH)


def do_papertest(p, *, use_g28=False):
    """Interactive paper-test calibration using line-mode `d N` / `u N`.

    Returns the recorded offset on `done`, or None on `abort`. Either
    way, head is lifted to Z+10 before return.

    Same workflow as before, but the safety-lift + G28-or-zcal +
    park-at-center logic is now in `printer.papertest_setup` so the
    UI's wizard can reuse it.
    """
    print("safety lift + reference step...")
    try:
        mode, post_ref_z = papertest_setup(p, use_g28=use_g28)
    except RuntimeError as e:
        sys.exit(str(e))

    if mode == "g28":
        print(f"after G28: Marlin Z = {post_ref_z:.3f}")
        print(f"head is at Marlin Z={post_ref_z:.3f} above center bed.")
        print("jog DOWN to find paper friction.")
    else:
        print(f"z contact (washer) → declared Z=1 (washer top)")
        print(f"dropped to presumed bed glass (Z=0).")

    print()
    print("Slide paper between nozzle and bed. Commands:")
    print("  d [N]       descend N mm (default 0.1)")
    print("  u [N]       ascend N mm (default 0.1)")
    print("  z           report current Z (M114)")
    print("  done        record current Z as sweet spot, lift and exit")
    print("  abort       lift and exit without recording")
    print()

    recorded_offset = None
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
            jog_one(p, "Z", -arg)
        elif cmd in ("u", "up"):
            jog_one(p, "Z", arg)
        elif cmd == "z":
            pos = parse_position(p.send("M114"))
            print(f"  Z = {pos.get('Z', '?')}")
        elif cmd == "done":
            try:
                z_sweet, offset = papertest_offset(
                    p, mode=mode, post_ref_z=post_ref_z)
            except RuntimeError as e:
                sys.exit(str(e))
            papertest_lift(p, 10)
            print()
            print(f"sweet-spot Z = {z_sweet:.3f}")
            if mode == "g28":
                print(f"post-G28 Z was: {post_ref_z:.3f}")
            else:
                print(f"actual washer-to-bed distance = {offset:.3f} mm")
            print(f"=> patch start_gcode to: G28\\nG92 Z{offset:.3f}\\n...")
            print(f"(`:papertest write` from the session will patch the .ini)")
            recorded_offset = offset
            break
        elif cmd in ("abort", "q", "quit"):
            papertest_lift(p, 10)
            break
        else:
            print(f"unknown: {cmd}")
    return recorded_offset


def cmd_papertest(args):
    """Interactive paper-test calibration of the bed-glass Z offset.

    All operations happen in ONE serial session (no DTR reset between
    steps), so the G92 Z=1 reference from zcal persists through the
    move + jog + M114 sequence.
    """
    if not args.yes:
        print("WARNING: papertest runs zcal first (descends nozzle up to 40mm")
        print("at the front-left washer). Re-run with --yes to proceed.")
        sys.exit(1)

    p = _connect(args)
    try:
        do_papertest(p, use_g28=args.use_g28)
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


# ---------- live session entry points ----------

def cmd_session(args):
    """Default mode: drop into the live interactive `lulz>` session.

    Holds the serial port for the lifetime of the session (so DTR-reset
    happens once at start, not per command). Arrow keys jog the head;
    `:cmd args` runs any of the one-shot commands inline; `:ui` spins up
    the browser UI in a worker thread sharing the same `Printer`.
    """
    from session import Session
    p = _connect(args)
    s = Session(p, run_ui_on_start=False)
    s.run()


def cmd_ui(args):
    """Live session + auto-launch the browser UI. Same process, same
    Printer, same abort path (Ctrl-C in the terminal).
    """
    from session import Session
    p = _connect(args)
    s = Session(p, run_ui_on_start=True, ui_port=args.port,
                ui_no_open=args.no_open)
    s.run()


# ---------- argparse ----------

def build_parser():
    ap = argparse.ArgumentParser(prog="lulz", description=__doc__.strip(),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=PORT, help=f"serial port (default {PORT})")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="echo every printer reply to stdout (chatty)")

    # subcommand is optional — no subcommand = live session.
    sub = ap.add_subparsers(dest="cmd", required=False)

    sub.add_parser("status", help="firmware, temps, position, endstops").set_defaults(func=cmd_status)
    sub.add_parser("home", help="heaters off + safety lift + full G28 (X Y Z via firmware probe)").set_defaults(func=cmd_home)

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

    se = sub.add_parser("session",
                        help="live interactive session (default when no subcommand)")
    se.set_defaults(func=cmd_session)

    u = sub.add_parser("ui",
                       help="live session + auto-launch browser UI (FastAPI inside this process)")
    u.add_argument("--port", type=int, default=8080,
                   help="HTTP port to bind (default 8080)")
    u.add_argument("--no-open", action="store_true",
                   help="don't auto-open the browser")
    u.set_defaults(func=cmd_ui)

    return ap


def main():
    args = build_parser().parse_args()
    if not getattr(args, "cmd", None):
        # No subcommand → drop into the live session.
        return cmd_session(args)
    args.func(args)


if __name__ == "__main__":
    main()
