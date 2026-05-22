# Lulzbot Mini control

Two tools, two jobs:

- **[OctoPrint](OCTOPRINT.md)** — primary host for actual printing.
  Replaces Cura LE's USB-printing role. You slice in PrusaSlicer (or
  Cura), upload `.gcode` to OctoPrint at <http://localhost:5001>, click
  Print. Setup: see [OCTOPRINT.md](OCTOPRINT.md).
- **[`lulz`](lulz.py)** — direct-USB CLI for debugging and calibration
  work that OctoPrint isn't suited for (deep-descent Z calibration,
  one-shot G-code probing, nozzle cleaning). Quiet by default, full
  transcript logged to `~/.lulzbot/last_run.log`.

Only one program can hold `/dev/cu.usbmodem144101` at a time — disconnect
OctoPrint in its UI before using `lulz`, and quit `lulz` before letting
OctoPrint connect.

For G-code semantics, see [GCODE.md](GCODE.md).

## Hardware

- Controller: **RAMBo** (UltiMachine), Marlin LE firmware.
- USB CDC: `/dev/cu.usbmodem144101` (index may change per port).
- **Baud: 250000** (not 115200; RAMBo is unusual).
- Loaded filament: TPU 3mm. The wipe and prep routines assume TPU.

## One-time setup

```bash
python3 -m pip install --user pyserial
brew install --cask prusaslicer
```

Quit Cura LE before running the tool — only one program can hold the
serial port. If the port shows "Resource busy", that's the cause.

## Print the calibration cube

This is the end-to-end path the tool is designed for:

```bash
cd lulzbot
python3 lulz.py status                       # printer responsive?
python3 lulz.py home
python3 lulz.py zcal --yes                   # deep-descent Z calibration
python3 lulz.py slice ../xyzCalibration_cube.stl
python3 lulz.py print ../xyzCalibration_cube.gcode
```

If something looks wrong mid-print, from another terminal:

```bash
python3 lulz.py panic
```

## Subcommands

Run `python3 lulz.py --help` for the full list. The important ones:

| Command | Purpose |
|---|---|
| `status` | One-shot: firmware, temps, position, endstops. Four lines, no chatter. |
| `home` | `G28 X Y` (Z handled separately via `zcal`). |
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

- [lulz.py](lulz.py) — single CLI tool.
- [printer.py](printer.py) — library (Printer class + reusable routines).
- [GCODE.md](GCODE.md) — G-code reference.
- [gcode/level_test.gcode](gcode/level_test.gcode) — distilled CuraLE TPU
  start sequence; runs heat → retract → wipe → G29 then stops. Useful
  for probe validation without committing to a full print.
- [gcode/lulzbot_mini_tpu.ini](gcode/lulzbot_mini_tpu.ini) — PrusaSlicer
  config used by `lulz slice`. Starting point — expect to iterate.
