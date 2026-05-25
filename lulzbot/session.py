"""
Live interactive lulz session.

Holds one persistent `Printer` instance for its whole lifetime (the DTR
boot reset happens once at start).

Two modes:

1. **REPL** (default). A `lulz> ` prompt; type a command + Enter. Same
   vocabulary as the argparse CLI's subcommands (status, heat, move,
   papertest, print, ui, panic, …) plus `quit`. A leading `:` is allowed
   but optional, so both `papertest` and `:papertest` work the same. The
   REPL never enters raw terminal mode, so Ctrl-C reliably interrupts.

2. **Jog mode**. Entered by typing `jog` (no args) at the REPL. Drops
   the terminal into cbreak so arrow keys / Enter / Space drive the
   head directly. ESC or `q` returns to the REPL; Ctrl-C exits the
   whole session.

Reliability rules:
- SIGINT raises KeyboardInterrupt (Python default). Blocking
  `os.read` / `serial.read` calls unwind immediately on Ctrl-C, which
  fixes the "hung up" feel of the previous setter-based handler.
- Single funnel `safe_shutdown(p, close=True)` for every exit path
  (`quit`, KeyboardInterrupt, atexit).
- `panic` stops motion + heaters but stays in the session.
- termios cleanup runs in a `finally` so the terminal is never left in
  raw mode after a crash or an early exit out of jog mode.
- In jog mode, after every jog we drain pending stdin bytes, so
  terminal auto-repeat while a key is held doesn't accumulate massive
  overshoot — the natural rate-limit becomes one jog per serial
  round-trip.

Jog-mode key bindings:
    ← / →           jog X by ∓step
    ↑ / ↓           jog Y by ±step  (↑ = Y+ "away from you")
    Enter           jog Z by +step (lift)
    Space           jog Z by −step (descend)
    1 / 2 / 3       set step to 0.1 / 1 / 10 mm
    + / -           bump step up/down through [0.1, 1, 10]
    s               one-shot status
    r               record current Z as paper-test sweet spot
    p               panic (M410 + heat off; stay in jog mode)
    h / ?           print key cheat-sheet
    q / ESC         back to REPL prompt
    Ctrl-C          exit session entirely
"""

import atexit
import os
import select
import shlex
import signal
import socket
import sys
import termios
import time
import tty
import webbrowser
from pathlib import Path

from printer import (
    PrintJob,
    fmt_dur,
    jog_one,
    make_bar_printer,
    papertest_lift,
    papertest_offset,
    papertest_setup,
    parse_position,
    safe_shutdown,
    stream_gcode,
)


# ESC sequences for the arrow keys on common terminals (xterm, iTerm,
# Terminal.app). The leading 0x1b is consumed before this table is
# consulted, so these are the *tails*.
ARROW_TAILS = {
    b"[A": "UP",
    b"[B": "DOWN",
    b"[C": "RIGHT",
    b"[D": "LEFT",
}

STEPS = [0.1, 1.0, 10.0]

REPL_HELP = """\
commands (Enter to submit; Ctrl-C exits)
  status / home / cool                     one-shot info / homing / heaters off
  heat 180 [--wait]                        set hotend (optionally block)
  move 75 75 [10]                          absolute move
  jog                                      enter arrow-key jog mode
  jog X 1 / extrude 5                      one-shot relative move / extrude
  step 0.1                                 set the default step size
  zcal / prep                              Z calibration / full TPU prep
  wipe [z_offset]                          run the wipe routine (default
                                           z_offset=0; if nozzle digs into
                                           pad, raise to e.g. 1.0 or 1.5)
  papertest [--use-g28]                    set the paper-test reference,
                                           then `jog` and Enter/Space/r
  papertest write                          patch lulzbot_mini_tpu.ini with
                                           the last recorded offset
  print path.gcode [--zcal] [--bg]         stream a file to the printer
                                           (Ctrl-C cancels & returns to prompt;
                                            --bg runs in background, REPL stays
                                            available — use `ui` to watch.)
  cancel                                   abort the running print
  progress                                 one-shot snapshot of the print job
  wait                                     re-attach the live bar to a bg print
  slice path.stl                           PrusaSlicer → .gcode (TPU profile)
  auto path.stl [--zcal]                   FULL one-shot pipeline:
                                            1. paper-test setup (G28, park)
                                            2. enters jog mode for paper test
                                               (Space to descend, `r` record,
                                                `q` continue)
                                            3. patch .ini with new offset
                                            4. slice (if .stl)
                                            5. stream the gcode
  raw 'M114'                               arbitrary G-code
  ui [--port 8080] [--no-open]             start in-process browser UI
  ui stop                                  stop browser UI
  panic                                    M410 + heaters off (stay in session)
  clear                                    clear the terminal screen
  help                                     this cheat-sheet
  quit / exit                              clean exit (lift Z + heat off)

a leading `:` is allowed (`:papertest` == `papertest`).
"""

JOG_HELP = """\
jog mode — arrow keys move; ESC or q returns to lulz> ; Ctrl-C exits
  ← →                jog X (∓step)
  ↑ ↓                jog Y (±step;  ↑ = Y+ "away from you")
  Enter              jog Z+ (lift)
  Space              jog Z− (descend)
  1 2 3              step = 0.1 / 1 / 10 mm
  + -                cycle step
  s                  one-shot status
  r                  record paper-test sweet spot
                       (run `papertest` first to set the reference)
  p                  panic (M410 + heat off, stay in jog mode)
  h ?                this cheat-sheet
  q ESC              back to lulz>
"""


class Session:
    def __init__(self, p, *, run_ui_on_start=False, ui_port=8080,
                 ui_no_open=False):
        self.p = p
        self.step = 1.0

        # Paper-test reference state. Set by `:papertest`, cleared on
        # successful `r`. `last_offset` persists across runs so `:papertest
        # write` can patch the .ini even after the reference has been
        # cleared.
        self.papertest_mode = None
        self.papertest_post_ref_z = None
        self.last_offset = None

        # Optional in-process FastAPI/uvicorn UI.
        self._ui_server = None       # uvicorn.Server
        self._ui_thread = None       # threading.Thread
        self._ui_port = None

        # Print job (shared with the UI server when running). Holds the
        # threading.Event used to cancel, plus running counters. The UI
        # reads from this directly — single source of truth.
        self.print_job = PrintJob()

        self._exit_requested = False
        self._tty_was = None

        # If the OS-shell invocation was `lulz ui`, start the UI as soon
        # as the session is up.
        self._auto_ui = run_ui_on_start
        self._auto_ui_port = ui_port
        self._auto_ui_no_open = ui_no_open

    # ---------- terminal helpers ----------

    def _enter_raw(self):
        fd = sys.stdin.fileno()
        self._tty_was = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    def _exit_raw(self):
        if self._tty_was is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                                  self._tty_was)
            except Exception:
                pass
            self._tty_was = None

    def _read_byte(self, timeout=None):
        """Read one byte from stdin. `timeout=None` blocks. Returns b''
        on timeout (no data) or on EOF.
        """
        fd = sys.stdin.fileno()
        if timeout is not None:
            r, _, _ = select.select([fd], [], [], timeout)
            if not r:
                return b""
        try:
            return os.read(fd, 1)
        except OSError:
            return b""

    def _read_key(self):
        """Block until we have a complete logical key.

        Returns one of:
          - one of ARROW_TAILS' values: "UP" / "DOWN" / "LEFT" / "RIGHT"
          - "ENTER", "SPACE", "ESC", "CTRL_C", "BACKSPACE"
          - a single-character string for normal keys ("s", ":", "1", …)
          - "" on EOF
        """
        b = self._read_byte()
        if b == b"":
            return ""
        if b == b"\x1b":
            # ESC. Could be an arrow (ESC + [X) or bare ESC.
            # Use a tiny timeout to disambiguate.
            b2 = self._read_byte(timeout=0.05)
            if b2 == b"":
                return "ESC"
            b3 = self._read_byte(timeout=0.05)
            tail = b2 + b3
            if tail in ARROW_TAILS:
                return ARROW_TAILS[tail]
            # Unknown sequence — swallow and ignore.
            return ""
        if b == b"\r" or b == b"\n":
            return "ENTER"
        if b == b" ":
            return "SPACE"
        if b == b"\x03":
            return "CTRL_C"
        if b == b"\x7f":
            return "BACKSPACE"
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    def _drain_pending(self):
        """Consume any bytes that arrived while we were busy talking to
        the printer. Prevents auto-repeat backlog from causing overshoot
        when a held key is released — the OS sends bytes faster than the
        serial round-trip, so we discard the surplus.
        """
        fd = sys.stdin.fileno()
        drained = 0
        while True:
            r, _, _ = select.select([fd], [], [], 0)
            if not r:
                return drained
            try:
                chunk = os.read(fd, 64)
            except OSError:
                return drained
            if not chunk:
                return drained
            drained += len(chunk)

    # ---------- shutdown ----------
    #
    # Note on SIGINT: we leave Python's default handler in place (which
    # raises KeyboardInterrupt). An earlier version of this module
    # installed a setter-style handler that just flipped a flag, but
    # that meant blocking `os.read` / `serial.read` calls could sit
    # for ~2s before the loop saw the flag, which felt hung. With the
    # default handler, KeyboardInterrupt unwinds blocking syscalls
    # immediately and is caught by `run()`'s try/except.

    def _shutdown(self):
        """Single funnel: stop UI thread (if any), safe_shutdown printer,
        restore terminal.
        """
        self._stop_ui(quiet=True)
        try:
            safe_shutdown(self.p, close=True)
        except Exception:
            pass
        self._exit_raw()

    # ---------- key dispatch ----------

    def _set_step(self, value):
        self.step = value
        print(f"step = {value} mm")

    def _cycle_step(self, direction):
        try:
            i = STEPS.index(self.step)
        except ValueError:
            # current step isn't one of the presets — snap to nearest
            i = min(range(len(STEPS)), key=lambda k: abs(STEPS[k] - self.step))
        i = max(0, min(len(STEPS) - 1, i + direction))
        self._set_step(STEPS[i])

    def _dispatch_key(self, key):
        """Dispatch one key in jog mode. Returns True to stay in jog
        mode, False to return to REPL.
        """
        if key in ("UP", "DOWN", "LEFT", "RIGHT", "ENTER", "SPACE"):
            mapping = {
                "RIGHT": ("X", +self.step),
                "LEFT":  ("X", -self.step),
                "UP":    ("Y", +self.step),
                "DOWN":  ("Y", -self.step),
                "ENTER": ("Z", +self.step),
                "SPACE": ("Z", -self.step),
            }
            axis, mm = mapping[key]
            try:
                jog_one(self.p, axis, mm)
            except Exception as e:
                print(f"\rjog failed: {e}")
            # Drain auto-repeat backlog so release doesn't overshoot.
            self._drain_pending()
            return True

        if key in ("q", "ESC"):
            return False

        if key == "p":
            try:
                self.p.send("M410")
                self.p.send("M104 S0")
                self.p.send("M140 S0")
                self.p.send("M84")
                print("\rPANIC: motion stopped, heaters and steppers off")
            except Exception as e:
                print(f"\rpanic failed: {e}")
            return True

        if key == "s":
            # Drop out of raw briefly so do_status output is readable.
            self._exit_raw()
            try:
                import lulz
                lulz.do_status(self.p)
            except Exception as e:
                print(f"status failed: {e}")
            self._enter_raw()
            return True

        if key == "r":
            self._exit_raw()
            self._record_papertest()
            self._enter_raw()
            return True

        if key in ("h", "?"):
            self._exit_raw()
            print(JOG_HELP)
            self._enter_raw()
            return True

        if key in ("1", "2", "3"):
            self._set_step(STEPS[int(key) - 1])
            return True

        if key == "+":
            self._cycle_step(+1)
            return True
        if key == "-":
            self._cycle_step(-1)
            return True

        # Unknown / unbound key — silent.
        return True

    # ---------- paper test recording ----------

    def _record_papertest(self):
        if self.papertest_mode is None:
            print("no paper-test reference set. Run `papertest` first.")
            return
        try:
            z_sweet, offset = papertest_offset(
                self.p,
                mode=self.papertest_mode,
                post_ref_z=self.papertest_post_ref_z,
            )
        except RuntimeError as e:
            print(f"record failed: {e}")
            return
        papertest_lift(self.p, 10)
        self.last_offset = offset
        print(f"sweet-spot Z = {z_sweet:.3f}  →  G92 Z{offset:.3f}")
        print(f"recorded. type `papertest write` to patch the .ini.")
        # Clear the reference so a stray `r` doesn't double-record.
        self.papertest_mode = None
        self.papertest_post_ref_z = None

    # ---------- line dispatch (REPL) ----------

    def _dispatch_line(self, line):
        # Leading `:` is allowed but optional, so `papertest` and
        # `:papertest` are equivalent.
        if line.startswith(":"):
            line = line[1:].strip()
        try:
            argv = shlex.split(line)
        except ValueError as e:
            print(f"parse error: {e}")
            return
        if not argv:
            return
        verb, args = argv[0], argv[1:]

        # Defer import to avoid circular dep at module load.
        import lulz

        try:
            if verb == "status":
                lulz.do_status(self.p)
            elif verb == "home":
                lulz.do_home(self.p)
            elif verb == "heat":
                temp = int(args[0])
                wait = "--wait" in args[1:]
                lulz.do_heat(self.p, temp, wait=wait)
            elif verb == "cool":
                lulz.do_cool(self.p)
            elif verb == "move":
                x = float(args[0]); y = float(args[1])
                z = float(args[2]) if len(args) > 2 else None
                lulz.do_move(self.p, x, y, z)
            elif verb == "jog":
                if not args:
                    # `jog` with no args drops into arrow-key cbreak mode.
                    self._jog_mode()
                else:
                    lulz.do_jog(self.p, args[0].upper(), float(args[1]))
            elif verb == "step":
                self._set_step(float(args[0]))
            elif verb == "zcal":
                lulz.do_zcal(self.p)
            elif verb == "wipe":
                # `wipe`        → z_offset = 0 (Cura original; pad top = Z0)
                # `wipe 1.0`    → z_offset = 1mm  (pad top is 1mm above Z0)
                z_off = float(args[0]) if args else 0.0
                from printer import wipe_nozzle
                wipe_nozzle(self.p, z_offset=z_off)
                print(f"wipe complete (z_offset={z_off:+.2f})")
            elif verb == "prep":
                wipe_temp = 180; probe_temp = 160
                for i, a in enumerate(args):
                    if a == "--wipe-temp": wipe_temp = int(args[i + 1])
                    elif a == "--probe-temp": probe_temp = int(args[i + 1])
                lulz.do_prep(self.p, wipe_temp=wipe_temp, probe_temp=probe_temp)
            elif verb == "extrude":
                mm = float(args[0])
                feed = 50
                if "--feed" in args:
                    feed = int(args[args.index("--feed") + 1])
                lulz.do_extrude(self.p, mm, feed=feed)
            elif verb == "papertest":
                self._cmd_papertest(args)
            elif verb == "print":
                file = args[0]
                zcal = "--zcal" in args[1:]
                bg = "--bg" in args[1:]
                self.start_print(file, zcal=zcal, foreground=not bg)
            elif verb == "cancel":
                self.cancel_print()
            elif verb == "progress":
                self.print_progress_snapshot()
            elif verb == "wait":
                if not self.print_job.running:
                    self.print_progress_snapshot()
                else:
                    self._watch_print()
            elif verb == "slice":
                # cmd_slice runs prusa-slicer as a subprocess — no serial.
                # Build a fake argparse Namespace so we can reuse cmd_slice
                # unchanged.
                import argparse
                ns = argparse.Namespace(file=args[0])
                lulz.cmd_slice(ns)
            elif verb == "auto":
                # Full pipeline: paper-test calibration → patch .ini →
                # slice (if .stl) → stream. The paper-test step is
                # interactive — see Session.run_auto.
                zcal = "--zcal" in args[1:]
                self.run_auto(args[0], zcal=zcal)
            elif verb == "raw":
                lulz.do_raw(self.p, args[0], echo_to_stdout=True)
            elif verb == "panic":
                lulz.do_panic(self.p)  # also turns off steppers
            elif verb == "ui":
                self._cmd_ui(args)
            elif verb == "help":
                print(REPL_HELP)
            elif verb in ("clear", "cls"):
                # ANSI: clear screen + home cursor. Works in Terminal.app,
                # iTerm, VSCode, anything that supports the standard
                # escape sequences.
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.flush()
            elif verb in ("quit", "exit"):
                self._exit_requested = True
            else:
                print(f"unknown command: {verb}  (type `help`)")
        except SystemExit as e:
            # do_* funcs occasionally sys.exit on unrecoverable errors —
            # in the session we want to keep going, not kill the process.
            msg = str(e) if e.code not in (None, 0) else "command failed"
            print(msg)
        except (IndexError, ValueError) as e:
            print(f"bad args: {e}")
        except Exception as e:
            print(f"error: {e}")

    # ---------- papertest sub-command (session-side) ----------

    def _cmd_papertest(self, args):
        """`:papertest [--use-g28]`  or  `:papertest write`."""
        if args and args[0] == "write":
            self._papertest_write()
            return

        use_g28 = "--use-g28" in args
        print("safety lift + reference step...")
        try:
            mode, post_ref_z = papertest_setup(self.p, use_g28=use_g28)
        except RuntimeError as e:
            print(f"papertest setup failed: {e}")
            return
        self.papertest_mode = mode
        self.papertest_post_ref_z = post_ref_z

        if mode == "g28":
            print(f"after G28: Marlin Z = {post_ref_z:.3f}, parked at center.")
        else:
            print(f"zcal contact → washer top = Z=1, dropped to Z=0 at center.")
        print("now use Enter/Space to jog Z, then press `r` to record "
              "the sweet spot.")

    def _papertest_write(self):
        if self.last_offset is None:
            print("no offset recorded yet. Run `papertest`, then `jog` + `r`.")
            return
        ini = Path(__file__).resolve().parent / "gcode" / "lulzbot_mini_tpu.ini"
        if not ini.is_file():
            print(f"slicer config missing: {ini}")
            return
        patch_g92_z(ini, self.last_offset)

    # ---------- one-shot auto pipeline (calibrate → slice → print) ----------

    def run_auto(self, file, *, zcal=False):
        """Full calibrate-slice-print pipeline.

        Blocks on the paper-test step: the head parks at bed center,
        then we drop into jog mode so the user can slide paper, find
        friction with Space, `r` to record, and `q` to exit jog. The
        recorded offset is then written to the .ini, the STL is sliced,
        and the resulting .gcode is streamed to the printer. If the
        user exits jog without recording (no `r` pressed), the pipeline
        aborts cleanly.

        `file` may be a .stl (sliced first) or a .gcode (printed directly).
        """
        src = Path(file).expanduser()
        if not src.is_absolute():
            src = (Path.cwd() / src).resolve()
        if not src.is_file():
            print(f"not a file: {src}")
            return False

        # 1. Paper-test setup — safety lift + G28 + park at center.
        print("[auto] running paper-test setup (safety lift + G28 + park at center)...")
        try:
            mode, post_ref_z = papertest_setup(self.p, use_g28=True)
        except RuntimeError as e:
            print(f"[auto] papertest setup failed: {e}")
            return False
        self.papertest_mode = mode
        self.papertest_post_ref_z = post_ref_z
        print(f"[auto] after G28: Marlin Z = {post_ref_z:.3f}, parked at (75, 75).")

        # Clear any stale offset from a prior run so the abort check
        # below distinguishes "user pressed r this time" from "user has
        # ever pressed r".
        self.last_offset = None

        # 2. Drop into jog mode for the human step.
        print()
        print("[auto] slide a sheet of paper between nozzle and bed.")
        print("[auto] in jog mode below: hold Space to descend, `1`/`2` for step,")
        print("[auto]                    `r` to record sweet spot, `q` to continue.")
        print()
        self._jog_mode()

        if self.last_offset is None:
            print("[auto] no offset recorded — aborting before slice/print.")
            return False

        # 3. Patch the .ini with the new offset.
        print(f"[auto] patching .ini with G92 Z{self.last_offset:.3f}...")
        self._papertest_write()

        # 4. Slice if STL; otherwise expect a .gcode.
        if src.suffix.lower() == ".stl":
            print(f"[auto] slicing {src.name}...")
            import argparse, lulz
            lulz.cmd_slice(argparse.Namespace(file=str(src)))
            gcode = src.with_suffix(".gcode")
            if not gcode.is_file():
                print(f"[auto] slicer produced no output: {gcode}")
                return False
        elif src.suffix.lower() == ".gcode":
            gcode = src
        else:
            print(f"[auto] file must be .stl or .gcode (got {src.suffix})")
            return False

        # 5. Stream the gcode (foreground bar; Ctrl-C cancels).
        print(f"[auto] printing {gcode.name}...")
        self.start_print(str(gcode), zcal=zcal, foreground=True)
        return True

    # ---------- print orchestration ----------

    def start_print(self, file, *, zcal=False, foreground=True):
        """Start streaming `file` to the printer in a background thread.

        If `foreground` is True, also draws the live bar and blocks
        until the print finishes / is cancelled / KeyboardInterrupt is
        caught (which sets the cancel flag and returns to the REPL —
        does NOT exit the session).

        If False (`print foo --bg` or the UI's /api/print/start),
        returns immediately with the thread running. Use `progress`,
        `wait`, or `cancel` from the REPL to interact with it.

        Returns True if started, False if a print was already running.
        """
        if self.print_job.running:
            print("a print is already running. `cancel` to abort, "
                  "or `wait` to watch.")
            return False

        path = Path(file).expanduser()
        if not path.is_absolute():
            # Resolve relative to whichever directory the user launched
            # the session from (typical workflow: `cd lulzbot; python lulz.py`).
            path = (Path.cwd() / path).resolve()
        if not path.is_file():
            print(f"not a file: {path}")
            return False

        # zcal must happen on the main thread BEFORE the streaming thread
        # starts — it's a probe sequence with safety lifts and we want
        # any failure to abort cleanly without spawning a worker.
        if zcal:
            print("[zcal] safety lift...")
            safety_lift(self.p)
            print("[zcal] probing front-left washer...")
            from printer import z_calibrate
            contact = z_calibrate(self.p)
            if contact is None:
                print("zcal FAILED: no z_min contact, aborting before print")
                return False
            print(f"[zcal] contact at {contact:.2f}, Z=1 set, lifted to Z=10")

        self.print_job.reset(path)

        def _runner():
            try:
                sent, total, elapsed, cancelled = stream_gcode(
                    self.p, path,
                    on_progress=self._update_print_job,
                    cancel_flag=self.print_job.cancel_event,
                )
                self.print_job.line = sent
                self.print_job.total = total
                self.print_job.elapsed = elapsed
                self.print_job.cancelled = cancelled
            except Exception as e:
                self.print_job.error = str(e)
            finally:
                self.print_job.running = False

        import threading
        t = threading.Thread(target=_runner, daemon=True, name="lulz-print")
        self.print_job.thread = t
        t.start()

        if foreground:
            self._watch_print()
        else:
            print(f"started in background. `progress` for status, "
                  f"`wait` to attach bar, `cancel` to abort, `ui` to watch in browser.")
        return True

    def _update_print_job(self, i, total, elapsed, percent):
        self.print_job.line = i
        self.print_job.total = total
        self.print_job.elapsed = elapsed
        self.print_job.percent = percent

    def _watch_print(self):
        """Foreground bar drawer. Polls self.print_job and redraws ~4Hz.
        Returns when the print thread finishes. Ctrl-C sets the cancel
        flag and waits for the worker to wind down, then returns to the
        REPL (does NOT raise KeyboardInterrupt — narrower scope than
        Ctrl-C at the lulz> prompt).
        """
        j = self.print_job
        print(f"streaming {Path(j.file).name}")
        bar_cb = make_bar_printer()
        try:
            while j.running:
                if j.total > 0:
                    bar_cb(j.line, j.total, j.elapsed, j.percent)
                time.sleep(0.25)
            # One final draw so the bar shows 100% (or final state).
            if j.total > 0:
                bar_cb(j.line, j.total, j.elapsed, j.percent)
        except KeyboardInterrupt:
            print("\ncancel requested...", flush=True)
            j.cancel_event.set()
            # Wait for the worker to actually exit (it checks the flag
            # between lines; one line ≤ ~1s in the worst case).
            if j.thread is not None:
                j.thread.join(timeout=15.0)
        print()  # newline so the summary doesn't overwrite the bar
        if j.error:
            print(f"error: {j.error}")
        elif j.cancelled:
            print(f"cancelled: {j.line}/{j.total} lines in {fmt_dur(j.elapsed)}")
        else:
            print(f"done: {j.total} lines in {fmt_dur(j.elapsed)} "
                  f"(avg {j.total/max(j.elapsed,0.001):.1f} ln/s)")

    def cancel_print(self):
        if not self.print_job.running:
            print("no print running.")
            return
        self.print_job.cancel_event.set()
        print("cancel requested. (worker will exit after the next line)")

    def print_progress_snapshot(self):
        j = self.print_job
        if not j.file:
            print("no print has run yet.")
            return
        state = "running" if j.running else (
            "cancelled" if j.cancelled else ("error" if j.error else "done"))
        rate = j.line / j.elapsed if j.elapsed > 0 else 0
        eta = (j.total - j.line) / rate if rate > 0 and j.running else 0
        print(f"  file     : {Path(j.file).name}")
        print(f"  state    : {state}")
        if j.error:
            print(f"  error    : {j.error}")
        print(f"  progress : {j.line}/{j.total} ({j.percent}%)")
        print(f"  elapsed  : {fmt_dur(j.elapsed)}")
        if j.running:
            print(f"  ETA      : {fmt_dur(eta)}  ({rate:.1f} ln/s)")

    # ---------- UI lifecycle ----------

    def _cmd_ui(self, args):
        if args and args[0] == "stop":
            self._stop_ui()
            return
        port = 8080
        no_open = False
        if "--port" in args:
            port = int(args[args.index("--port") + 1])
        if "--no-open" in args:
            no_open = True
        self._start_ui(port=port, no_open=no_open)

    def _start_ui(self, *, port=8080, no_open=False):
        if self._ui_thread is not None and self._ui_thread.is_alive():
            print(f"UI already running at http://127.0.0.1:{self._ui_port}/")
            return

        # Lazy import — most sessions won't ever need FastAPI installed.
        try:
            import uvicorn
            from ui.server import make_app
        except ImportError as e:
            print(f"UI dependency missing: {e}")
            print("install with:  python3 -m pip install --user fastapi uvicorn")
            return

        if not _port_free(port):
            print(f"port {port} is in use. pick another with `ui --port N`.")
            return

        app = make_app(self.p, self)
        # uvicorn lifespan="off" — we don't have startup/shutdown async
        # hooks and the lifespan protocol can deadlock when uvicorn is
        # embedded in a thread.
        config = uvicorn.Config(app, host="127.0.0.1", port=port,
                                log_level="warning", lifespan="off")
        self._ui_server = uvicorn.Server(config)
        self._ui_port = port

        import threading
        def _run():
            try:
                self._ui_server.run()
            except Exception as e:
                print(f"UI server crashed: {e}")
        self._ui_thread = threading.Thread(target=_run, daemon=True,
                                           name="lulz-ui")
        self._ui_thread.start()

        # Wait briefly for the server to bind before we declare success.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._ui_server.started:
                break
            time.sleep(0.05)

        url = f"http://127.0.0.1:{port}/"
        print(f"UI: {url}  (type `ui stop` to shut it down)")
        if not no_open:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def _stop_ui(self, *, quiet=False):
        if self._ui_server is None:
            if not quiet:
                print("UI is not running.")
            return
        self._ui_server.should_exit = True
        if self._ui_thread is not None:
            self._ui_thread.join(timeout=5.0)
        self._ui_server = None
        self._ui_thread = None
        self._ui_port = None
        if not quiet:
            print("UI stopped.")

    # ---------- main loop (REPL) ----------

    def run(self):
        atexit.register(self._shutdown_on_exit)

        print("lulz session ready — type `help` for commands, `jog` for "
              "arrow-key mode, `quit` to exit (Ctrl-C anytime).")
        try:
            if self._auto_ui:
                # OS-shell invoked `lulz ui` → start the UI immediately.
                self._start_ui(port=self._auto_ui_port,
                               no_open=self._auto_ui_no_open)

            while not self._exit_requested:
                try:
                    line = input("lulz> ").strip()
                except EOFError:
                    # Ctrl-D / closed stdin → quit cleanly.
                    print()
                    break
                if not line:
                    continue
                self._dispatch_line(line)
        except KeyboardInterrupt:
            # Ctrl-C at the prompt (or during a command). Exit cleanly.
            print()
        finally:
            self._shutdown()

    # ---------- jog mode (cbreak) ----------

    def _jog_mode(self):
        """Enter raw arrow-key mode. Return to REPL on ESC/q."""
        print(JOG_HELP)
        print(f"step = {self.step} mm")
        try:
            self._enter_raw()
            while True:
                key = self._read_key()
                if key == "":
                    break       # EOF
                cont = self._dispatch_key(key)
                if cont is False:
                    break
        finally:
            self._exit_raw()
        print(f"\n(back at lulz>)")

    def _shutdown_on_exit(self):
        # atexit fires even on uncaught exceptions; restore terminal at
        # minimum even if the printer talk fails.
        try:
            self._exit_raw()
        except Exception:
            pass


# ---------- module-level helpers ----------

def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def patch_g92_z(ini_path, new_offset):
    """Replace the `G92 Z<n>` token inside the `start_gcode = ...` line
    of the PrusaSlicer profile with `G92 Z<new_offset>`.

    Scope is intentionally narrow: only the start_gcode line is touched.
    Narrative comments elsewhere (e.g. `# The G92 Z0.1 after G28
    declares...`) are left alone so they don't get silently
    de-synchronised by a patch.

    Inside start_gcode, PrusaSlicer encodes newlines as literal `\\n`,
    so the line looks like `start_gcode = G21\\nM107\\n...\\nG92 Z0.1\\n...`.

    Writes a `.bak` next to the file before rewriting.
    """
    import re as _re
    ini_path = Path(ini_path)
    original = ini_path.read_text()
    lines = original.splitlines(keepends=True)
    g92_pat = _re.compile(r"G92\s+Z-?\d+(?:\.\d+)?")
    target_idx = None
    for i, line in enumerate(lines):
        # Match `start_gcode = ...` (PrusaSlicer key=value). The value
        # is the rest of the line; we patch inside it only.
        if line.lstrip().startswith("start_gcode") and "=" in line:
            target_idx = i
            break
    if target_idx is None:
        print(f"no `start_gcode = ...` line in {ini_path.name}")
        return
    new_line, n = g92_pat.subn(f"G92 Z{new_offset:.3f}", lines[target_idx])
    action = "replaced"
    if n == 0:
        # No existing `G92 Z<n>` token. Try to INSERT one right after
        # the `G28\n` in start_gcode. start_gcode is a single PrusaSlicer
        # config line with literal `\n` as the command separator, so we
        # look for the two-character sequence `\` + `n` after `G28`.
        g28_pat = _re.compile(r"(G28)(\\n)")
        new_line, n_inserted = g28_pat.subn(
            rf"\g<1>\g<2>G92 Z{new_offset:.3f}\g<2>",
            lines[target_idx], count=1,
        )
        if n_inserted == 0:
            print(f"no `G92 Z<n>` and no `G28` in start_gcode of "
                  f"{ini_path.name}. Add `G28\\nG92 Z{new_offset:.3f}\\n` "
                  f"manually to the `start_gcode = ...` line.")
            return
        action = "inserted"
        n = n_inserted
    lines[target_idx] = new_line
    bak = ini_path.with_suffix(ini_path.suffix + ".bak")
    bak.write_text(original)
    ini_path.write_text("".join(lines))
    print(f"{action} `G92 Z{new_offset:.3f}` in start_gcode of "
          f"{ini_path.name}")
    print(f"backup: {bak.name}")
