# Lulzbot control + notes

Workspace for talking to the Lulzbot 3D printer directly over USB and
keeping notes that aren't worth re-discovering each time.

## Hardware

- Controller: **RAMBo** (UltiMachine), runs **Marlin** firmware.
- Connects as a USB CDC serial device on macOS:
  `/dev/cu.usbmodem144101` (number may change if you plug into a
  different port).
- **Baud rate: 250000** (not the usual 115200 — RAMBo is unusual here).

## Files

- `printer.py` — small Python helper. Opens the serial port, drains
  the boot banner, sends G-code, waits for `ok`. CLI lets you send
  one command at a time or drop into a REPL.
- `heat_and_hold.py` — heats the nozzle to a chosen temp, parks the
  head in front, and **holds the serial connection open** so the
  board doesn't reset (RAMBo resets on every port-open). Use this
  when you need to scrub the nozzle by hand.
- `extrude_test.py` — heats, parks, pushes a small amount of filament
  to verify flow. Defaults are TPU-safe (225°C, slow F50). Retracts
  on exit so the nozzle stops oozing.
- `stream_gcode.py` — streams a `.gcode` file line-by-line, waiting
  for `ok` between lines. Logs every command + reply with timestamps.
- `gcode/` — `.gcode` files. Includes `level_test.gcode`, a
  distilled Cura TPU start sequence (heat → retract → wipe → probe)
  that ends after `G29` so you can validate the bed-leveling probe
  without doing a full print.

## Setup (one-time)

```
python3 -m pip install --user pyserial
```

## Use

**Interactive REPL** — best for poking around:
```
python3 printer.py
> M115        # firmware info
> M105        # temperatures
> M114        # position
> quit
```

**One-shot** — useful for scripts:
```
python3 printer.py "M105"
python3 printer.py "G28" "M114"
```

**Important:** only one program can hold the serial port at a time.
Disconnect Cura LulzBot Edition (or quit it) before running this, or
you'll get "Resource busy" / "Device or resource in use".

## Useful G-code cheat sheet

Read-only / safe:
- `M115` — firmware + capabilities
- `M105` — hotend + bed temperatures
- `M114` — current XYZ position
- `M119` — endstop status (X, Y, Z, Z-probe)
- `M503` — print current settings (steps/mm, accel, PID, etc.)

Movement / heat (knows-what-they're-doing territory):
- `G28` — home all axes
- `G28 X Y` — home X and Y only
- `G1 X100 Y100 F3000` — move to (100,100) at feedrate 3000 mm/min
- `M104 S200` — set hotend target to 200 C (no wait)
- `M109 S200` — set hotend target and wait
- `M140 S60` / `M190 S60` — bed target, no-wait / wait
- `M104 S0` and `M140 S0` — turn heaters off
- `M84` — disable steppers (lets you push the head by hand)
- `G29` — auto bed level (Lulzbot probes corner washers with nozzle)

Stop:
- `M112` — **emergency stop**. Marlin halts everything; needs a power
  cycle to recover. Use when something's clearly wrong.

## Troubleshooting log

### "Heated up, wiped the nozzle, then never started printing"

Symptom: print starts, hotend reaches probe temp, nozzle wipes on the
pad, head moves toward a bed corner, then nothing — no error in Cura,
heaters stay on, printer just sits there.

Cause: **G29 auto-bed-leveling probe failed**. Lulzbot uses the nozzle
as the probe and touches 4 metal washers at the bed corners. If
electrical contact isn't made (filament drop on the nozzle, dirty
washer, dead wiper pad), Marlin halts in place.

Fix (validated 2026-05-21):
1. Run `python3 heat_and_hold.py 150` — heats to 150 C and parks the
   head where you can reach it, holding the port open so the heater
   stays on.
2. Scrub the nozzle with a brass wire brush until it's shiny. Brass
   brush only — 150 C burns skin instantly.
3. Wipe the 4 corner washers with isopropyl alcohol on a Q-tip.
4. Ctrl-C the hold script (clean exit: retracts, heater off).
5. Optional sanity check: `python3 -u stream_gcode.py
   gcode/level_test.gcode` — runs the Cura prep sequence + G29 and
   stops. If it completes without halt, the probe is happy and a
   real Cura print will work.

### Confirmed: probe wiring DOES work; we just needed deeper descent

After multiple debug rounds, `test_corners.py` with descent range
extended to 80mm produced clean real contacts at all 4 corners:

```
back-left   triggered at -32.0mm
back-right  triggered at -34.0mm
front-right triggered at -34.0mm
front-left  triggered at -33.0mm
Spread between hits: 2.0mm   (bed is level)
```

What went wrong in earlier rounds:
- We assumed the head was ~10mm above the washer after our prep
  sequence, so descents of 15-30mm should have been enough. They
  weren't — Marlin's actual Z position (after our G92 declarations)
  put the washer ~32mm below the safe-Z we'd lifted to. Earlier
  tests stopped descending before reaching contact.
- Earlier "noise" reads were probably real intermittent contact at
  near-zero Z, not floating-wire noise. We were mis-interpreting the
  data because we didn't have enough descent to confirm real contact
  vs. spurious triggers.

Lesson: when in doubt, descend further. The probe wire is fine.

### Probe triggers without contact (noisy z_min signal)

Symptom: `z_min` reports TRIGGERED spontaneously while the head is
idle and not touching anything, OR triggers very early on a G30
descent without the nozzle reaching the washer. G29 returns `ok`
suspiciously fast (seconds, not the expected ~30s for 4 corners).

Cause: a floating or intermittent connection on the z_min input.
Lulzbot uses the nozzle as ground and runs a wire from each corner
washer to the z_min pin on the RAMBo. If any of those wires (or the
combined signal path) is loose or broken, the input picks up noise
that reads as TRIGGERED in software — Marlin thinks the probe is
already touching and skips the actual descent.

How to confirm: with the head parked away from any washer, watch
M119 / z_min for ~30 seconds. If it flickers between TRIGGERED and
open with no input, it's noise.

Tools in this folder:
- `session.py` + `repl.py` — interactive session that auto-polls
  z_min so a noisy signal is obvious as random state-change events.
- `probe_continuity.py` — same idea, dedicated watcher.

Fix is physical:
1. Inspect the small terminals at the back of the print head where
   the probe wire connects. Reseat / tighten.
2. Inspect the wires from each corner washer to the controller —
   often hidden under the bed.
3. Inspect the drag chain / cable bundle for flex damage at the bend
   points (where the loom moves with the head).

Until the wiring is solid, G29 cannot be trusted on this machine.

### Marlin LE quirks observed

- Opening the USB port resets the board (DTR pulse). Every reconnect
  wipes the in-RAM bed-leveling matrix and heater targets. Any test
  that needs to inspect probe results MUST do so in the same session
  where G29 ran — don't reconnect first.
- G29 in this firmware does NOT emit per-corner Z values or a Bed
  Topography report on the serial line. You only see `ok` at the end
  — even with `G29 V4`. Diagnose via z_min state changes during the
  probe (watch the auto-poll output), not via probe data.
- G30 (single-point probe) at a bare position appears to silently
  succeed without probing if the XY is outside the firmware's
  configured probe area. Only the 4 corner-washer positions that G29
  uses internally seem to actually probe.
- M211 S0 to disable software endstops did not reliably free Z to
  descend below `Z_MIN_POS` in our testing. The exact syntax this
  firmware fork accepts is still unconfirmed.

### TPU oozes constantly when the nozzle is hot

Normal. TPU is soft and has thermal expansion pressure; a parked hot
nozzle will drool indefinitely. Two consequences:

- During bed-leveling, the ooze can re-contaminate the nozzle right
  after the wipe. Cura's TPU start gcode handles this with
  `G1 E-30 F75` (retract 30mm) BEFORE the wipe and probe. Any
  hand-rolled start gcode for TPU needs that retract too —
  `gcode/level_test.gcode` has it.
- If you're holding the nozzle hot for inspection, retract first or
  expect ooze. `extrude_test.py` retracts 2mm on exit for this
  reason.

### "Encountered serial exception! Closing connection." in Cura log

USB serial dropped mid-job. Not a slicing problem. Check, in order:

1. USB cable — try a different (preferably shorter, shielded) one.
2. Plug directly into the Mac, not through a hub or dock.
3. macOS USB power management putting the port to sleep.
4. Another app holding the port (OctoPrint, Pronterface, a stale
   `screen` session).

CuraLE log lives at:
`~/Library/Application Support/CuraLE/4.13/CuraLE.log`

## If you want to print without a computer at all

Export the sliced `.gcode` to an SD card from Cura and print from the
printer's LCD. Removes USB from the equation entirely — useful as a
test when you suspect a connection issue vs. a slicing/leveling issue.
