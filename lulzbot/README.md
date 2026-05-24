# Lulzbot Mini control

Primary tool: **[`lulz`](lulz.py)** — a single Python CLI that owns the
USB port and can do everything we need (jog, paper-test calibration,
heat, print, slice, panic). Quiet by default; full transcript always at
`~/.lulzbot/last_run.log`.

Two modes:

- **Live session** (`python3 lulz.py` with no subcommand) — drops you
  into a persistent interactive `lulz>` shell. Arrow keys / Enter /
  Space jog the head in real time. Type `:` to run any one-shot command
  inline. Type `:ui` to spin up a basic browser UI **inside the same
  process** (status panel, temp graphs, paper-test wizard, print).
  Ctrl-C in the terminal is the trustworthy abort path.
- **One-shot subcommands** (`lulz status`, `lulz heat 180`, …) — open
  the port, run one command, close. Kept for scripting and quick
  probes.

[OctoPrint](OCTOPRINT.md) setup is preserved but not the primary path —
it shares G29 calibration assumptions that don't work on our machine
yet. See [README.md troubleshooting](#troubleshooting-log).

For G-code semantics, see [GCODE.md](GCODE.md).

## Hardware

- Controller: **RAMBo** (UltiMachine), Marlin LE firmware.
- USB CDC: `/dev/cu.usbmodem144101` (index may change per port).
- **Baud: 250000** (not 115200; RAMBo is unusual).
- Loaded filament: TPU 3mm. The wipe and prep routines assume TPU.

## One-time setup

```bash
/usr/local/bin/python3 -m pip install --user -r requirements.txt
brew install --cask prusaslicer    # only needed for `:slice`
```

[requirements.txt](requirements.txt) is annotated — `pyserial` is the
only hard dependency; `fastapi`/`uvicorn` are lazy-imported by `:ui`
only. Use the same python3 that owns your serial port (system
`/usr/local/bin/python3`, not a pyenv interpreter unless you mirror the
install there).

Quit Cura LE before running the tool — only one program can hold the
serial port. If the port shows "Resource busy", that's the cause.

## Live session — recommended

```bash
cd lulzbot
python lulz.py
```

You land at a `lulz>` REPL prompt. Type a command and hit Enter; Ctrl-C
exits cleanly anytime. Available commands:

```
status / home / cool                  one-shot info / homing / heaters off
heat 180 [--wait]                     set hotend (optionally block)
move 75 75 [10]                       absolute move
jog                                   enter arrow-key jog mode (see below)
jog X 1 / extrude 5                   one-shot relative move / extrude
step 0.1                              set the default step size
zcal / wipe / prep                    calibration / TPU prep
papertest [--use-g28]                 set the paper-test reference, then
                                      `jog` + Enter/Space + `r` to record
papertest write                       patch gcode/lulzbot_mini_tpu.ini
print path.gcode [--zcal]             stream a file to the printer
slice path.stl                        PrusaSlicer → .gcode (TPU profile)
raw 'M114'                            arbitrary G-code
ui [--port 8080] [--no-open]          start the browser UI in this process
ui stop                               stop the browser UI
panic                                 M410 + heaters off (stay in session)
help                                  cheat-sheet
quit / exit                           clean exit (lift Z + heat off)
```

A leading `:` is allowed but optional (`:papertest` == `papertest`).

### Jog mode

Type `jog` (no args) at the `lulz>` prompt to drop into arrow-key mode:

| key | action |
|---|---|
| ← → | jog X by ∓ step |
| ↑ ↓ | jog Y by ± step (↑ = Y+, away from you) |
| Enter | jog Z+ (lift) |
| Space | jog Z- (descend) |
| 1 / 2 / 3 | step = 0.1 / 1 / 10 mm |
| + / - | cycle step |
| s | one-shot status |
| r | record paper-test sweet spot (after `papertest`) |
| p | panic (M410 + heat off, stays in jog mode) |
| h or ? | cheat-sheet |
| q or ESC | back to `lulz>` prompt |
| Ctrl-C | exit session entirely |

Hold an arrow key to jog continuously — auto-repeat is throttled by the
serial round-trip so release stops the head within one move.

## Browser UI (`:ui` or `lulz ui`)

The session can host a small browser UI on `http://127.0.0.1:8080/` via
FastAPI running in a worker thread of the same Python process. It shares
the same `Printer` instance — there is no second USB owner. The CLI
remains the only abort path you should rely on (Ctrl-C in the terminal
runs the same safe-shutdown funnel).

The UI provides:

- Status panel: hotend / bed temps, position, z_min, step indicator
- Live temp graph (last ~5 min, inline SVG)
- Jog: arrow keys, Enter (Z+), Space (Z-), or click-and-hold buttons.
  Step radio (0.1 / 1 / 10 mm). Hold is throttled by serial round-trip.
- Heat / cool
- Paper-test wizard (start → arrow-jog → done → patch the .ini)
- Print a local `.gcode` file with progress + cancel
- Panic button (M410 + heaters off)

Start it two ways:
- `python3 lulz.py ui [--port 8080] [--no-open]` from a fresh shell, or
- type `:ui` inside an already-running session.

`:ui stop` shuts the server down while keeping the session alive.

## Print the calibration cube

End-to-end via the live session (recommended — one persistent USB
connection, no DTR reset between steps):

```bash
cd lulzbot
python lulz.py              # enter session
# at the lulz> prompt:
lulz> status                # printer responsive?
lulz> home
lulz> papertest --use-g28   # set the reference
lulz> jog                   # arrow-key mode; Space to descend, r to record
lulz> papertest write       # patch lulzbot_mini_tpu.ini
lulz> slice ../xyzCalibration_cube.stl
lulz> print ../xyzCalibration_cube.gcode
```

If something looks wrong mid-print, type `panic` (or Ctrl-C for a clean
exit). Both run the safe-shutdown funnel (M410 + lift + heaters off).

You can also use the one-shot subcommands from another terminal — but
they each open/close the port, which DTR-resets the board, so use them
for quick probes only:

```bash
python3 lulz.py status
python3 lulz.py panic
```

## Subcommands

Run `python3 lulz.py --help` for the full list. The important ones:

| Command | Purpose |
|---|---|
| `status` | One-shot: firmware, temps, position, endstops. Four lines, no chatter. |
| `home` | Heaters off, safety lift (Z+30 relative), then full `G28` (X, Y, and Z via firmware probe). "Park the machine cleanly" verb you can run anytime. |
| `zcal --yes` | Deep-descent Z calibration on the front-left washer. Sets `G92 Z1`. |
| `heat T [--wait]` | Set hotend target. `--wait` blocks silently and prints a single completion line. |
| `cool` | Heaters off. |
| `wipe` | Run the validated nozzle-wipe routine. |
| `prep` | Full TPU prep: heat → 30mm retract → wipe → cool to probe temp. |
| `extrude MM` | Slow extrusion (F50, TPU-safe by default). |
| `jog AXIS MM` | Relative move on one axis. |
| `move X Y [Z]` | Absolute move. |
| `slice FILE.stl` | PrusaSlicer with the Lulzbot Mini TPU profile → `FILE.gcode`. |
| `print FILE.gcode` | Stream G-code. Progress every 5% or 30s. Aborts on `error`/`!!`. |
| `watch` | Live serial tail. Opt-in; this is where unrestricted streaming lives. |
| `raw 'M114'` | Pass-through escape hatch. |
| `panic` | Quickstop + heaters off + steppers off. |
| `log` | Print path to `~/.lulzbot/last_run.log`. |

Global flags: `--port PATH` (override default), `-v/--verbose` (echo every
printer reply — chatty; use only when debugging a specific command).

## Why "quiet by default"

Earlier iterations of this codebase polled `M119` (endstops) at 1 Hz and
`M105` (temps) at 0.5 Hz, echoing every reply to a log we then tailed.
That log filled with thousands of `ok` and `T:180.0 /180.0` lines, which
made it expensive (in tokens) for an LLM assistant to read back through.

The current design splits monitoring into two paths:
- **Default**: polling happens internally; the *transcript* goes to
  `~/.lulzbot/last_run.log`, but only state changes / completions
  print to stdout. One terse line per subcommand.
- **`lulz watch`**: explicit live tail. Use this when you want to
  actively watch what's happening on the wire.

If you need to debug a specific command, add `-v`.

## Troubleshooting log

### "Heated up, wiped the nozzle, then never started printing"

Symptom: print starts, hotend reaches probe temp, nozzle wipes on the
pad, head moves toward a bed corner, then nothing — no error in Cura,
heaters stay on, printer just sits there.

Cause: **G29 auto-bed-leveling probe failed**. Lulzbot uses the nozzle
as the probe and touches 4 metal washers at the bed corners. If
electrical contact isn't made (filament drop on nozzle, dirty washer,
dead wiper pad), Marlin halts in place.

Fix (validated 2026-05-21):
1. `python3 lulz.py heat 150 --wait` — heats to a hand-safe-ish temp.
2. `python3 lulz.py move 80 20 100` — parks the head where you can reach it.
   The serial port stays open just long enough for the move; for an
   extended hold use `python3 lulz.py watch` in another terminal.
3. Scrub the nozzle with a brass wire brush until shiny. Brass only —
   150 °C burns skin instantly.
4. Wipe the 4 corner washers with isopropyl on a Q-tip.
5. `python3 lulz.py print gcode/level_test.gcode` — runs the Cura
   prep sequence + G29 and stops. If it completes without halt, the
   probe is happy and a real print will work.

### Confirmed: probe wiring works; we just needed deeper descent

After multiple debug rounds, descent extended to ~80mm of total travel
produced clean contacts at all 4 corners:

```
back-left   triggered at -32.0mm  (from G92 Z150 reference)
back-right  triggered at -34.0mm
front-right triggered at -34.0mm
front-left  triggered at -33.0mm
Spread between hits: 2.0mm   (bed is level)
```

What went wrong in earlier rounds: we assumed the head was ~10mm above
the washer after the prep sequence, so descents of 15–30mm "should have
been enough." They weren't — Marlin's actual Z after our `G92`
declarations put the washer ~32mm below the safe-Z we'd lifted to.

Lesson: when in doubt, descend further. The probe wire is fine. The
default `zcal` descends to Z = -40 for this reason.

### Probe triggers without contact (noisy z_min)

Symptom: `M119` reports z_min TRIGGERED while the head is idle and not
touching anything, OR triggers very early on a descent without reaching
the washer. G29 returns `ok` suspiciously fast.

Cause: floating / intermittent z_min wire. Lulzbot uses the nozzle as
ground and a wire from each corner washer to the z_min pin on the
RAMBo. Loose or broken wires pick up noise that reads as TRIGGERED.

Confirm: `python3 lulz.py watch`, then leave the head idle. If `z_min`
flickers TRIGGERED ↔ open with no input, that's noise.

Fix is physical:
1. Reseat the small terminals at the back of the print head.
2. Trace the wires from each corner washer to the controller (often
   hidden under the bed).
3. Inspect the drag chain at the bend points.

Until the wiring is solid, G29 cannot be trusted.

### Marlin LE quirks

- USB-open DTR-resets the board. In-RAM bed matrix and heater targets
  are wiped on every reconnect. If you need to inspect G29 results,
  do it in the same session.
- `G29` does NOT emit per-corner Z values, even with `V4`. Only `ok`.
  Diagnose via `M119` state changes during the descent, not via probe
  data.
- `G30` at a bare XY silently returns `ok` without probing if outside
  the firmware's configured probe area. Only the 4 washer positions
  G29 uses internally actually probe.
- `M211 S0` to disable software endstops doesn't reliably free Z below
  `Z_MIN_POS` in this firmware fork. Exact accepted syntax unconfirmed.

### "Encountered serial exception! Closing connection." in Cura log

USB serial dropped mid-job. Not a slicing problem. In order:
1. Different USB cable (shorter, shielded).
2. Plug directly into the Mac, not through a hub.
3. macOS USB power management putting the port to sleep.
4. Another app holding the port (Cura, Pronterface, stale `screen`).

CuraLE log: `~/Library/Application Support/CuraLE/4.13/CuraLE.log`.

If USB stays flaky, fall back to SD-card printing: export the `.gcode`
from CuraLE or via `lulz slice`, copy to an SD card, print from the
LCD. Removes USB from the equation.

## Files

- [lulz.py](lulz.py) — CLI dispatch (argparse + the `cmd_*` one-shots
  and their `do_*` cores that the session reuses).
- [session.py](session.py) — live interactive session: raw-terminal key
  loop, `:` line dispatcher, in-process UI lifecycle, `.ini` patcher.
- [printer.py](printer.py) — Printer class (thread-safe `send`) plus
  reusable routines (jog_one, safe_shutdown, stream_gcode, paper-test
  setup / offset helpers, zcal, wipe, prep_tpu).
- [ui/server.py](ui/server.py) — FastAPI app factory. Built lazily on
  `:ui` so fastapi/uvicorn don't need to be installed unless you use it.
- [ui/static/](ui/static/) — `index.html` / `app.js` / `style.css` for
  the browser UI.
- [GCODE.md](GCODE.md) — G-code reference.
- [gcode/level_test.gcode](gcode/level_test.gcode) — distilled CuraLE TPU
  start sequence; runs heat → retract → wipe → G29 then stops. Useful
  for probe validation without committing to a full print.
- [gcode/lulzbot_mini_tpu.ini](gcode/lulzbot_mini_tpu.ini) — PrusaSlicer
  config used by `:slice`. Patched in place by `:papertest write` —
  backup written next to it as `.ini.bak`.
