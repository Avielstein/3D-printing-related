# G-code reference — Lulzbot Mini / Marlin LE

A lookup table for the G-code commands this printer accepts. Everything
here has been validated on our specific Lulzbot Mini (RAMBo, Marlin LE
firmware fork). Where Marlin LE differs from generic Marlin, the
difference is noted.

For the *how* (procedures, troubleshooting), see the README. This file
is the *what* (one command per line, what it does).

## Connection

| Setting | Value | Notes |
|---|---|---|
| Port | `/dev/cu.usbmodem144101` | macOS USB-CDC. Index changes per port. |
| Baud | **250000** | Not the usual 115200. RAMBo is unusual here. |
| Line ending | `\n` | One command per line. |
| Reply terminator | `ok` | Every command produces an `ok` (or `error`). |
| Boot quirk | DTR-on-open resets the board | Wait ~3s for `start` banner; in-RAM state (bed matrix, heater targets) is wiped. |

## Safe queries (read-only, never move anything)

| Command | Returns |
|---|---|
| `M115` | Firmware name, version, capabilities. First call in any session. |
| `M105` | Hotend + bed temps: `T:181.2 /180.0 B:60.1 /60.0`. |
| `M114` | Current position: `X:.. Y:.. Z:.. E:.. Count X:.. Y:.. Z:..`. (Strip the `Count` half — those are step counters, not coordinates.) |
| `M119` | Endstop / probe state: `x_min`, `y_min`, `z_min`, `z_probe`. Each is `open` or `TRIGGERED`. |
| `M503` | Dump all settings (steps/mm, accel, PID, jerk, etc.). |

## Movement

| Command | Effect |
|---|---|
| `G28` | Home all axes. Z homing uses the washer probe — fails if probe is unreliable. |
| `G28 X Y` | Home X and Y only. Use this when Z probing is unreliable; calibrate Z separately. |
| `G0 X100 Y100 F4000` | Rapid move to (100, 100). `F` is mm/min. `G0` and `G1` are interchangeable on Marlin. |
| `G1 X100 Y100 Z10 F3000` | Linear move with Z too. |
| `G1 E5 F50` | Extrude 5mm of filament. F50 is TPU-safe; faster will buckle TPU. |
| `G90` | Absolute positioning (default). |
| `G91` | Relative positioning. Use for jogging; always pair with `G90` after. |
| `G92 X0 Y0 Z0` | Declare current physical position as (0,0,0). Doesn't move. |
| `G92 Z1` | "I assert the current Z is 1mm" — used after washer contact (washer top = 1mm above bed glass). |

## Heat

| Command | Effect |
|---|---|
| `M104 S200` | Set hotend target to 200°C. Returns `ok` immediately, doesn't wait. |
| `M109 S200` | Set hotend target AND block until reached. |
| `M109 R200` | Same as `S` but ignores any prior target — Marlin "reset" form. We use this for wipe→probe temp transitions. |
| `M140 S60` | Set bed target. No wait. |
| `M190 S60` | Set bed target and wait. |
| `M104 S0` / `M140 S0` | Heaters off. |

## Extrusion modes

| Command | Effect |
|---|---|
| `M82` | Extruder absolute mode. `G1 E10` then `G1 E20` extrudes 10mm net. |
| `M83` | Extruder relative mode. `G1 E10` then `G1 E10` extrudes 20mm net. |
| `G92 E0` | Reset extruder coordinate to 0. |

**TPU rules** (validated on this machine):
- Feedrate ≤ F50 for any push. Faster buckles the soft filament between drive gear and nozzle.
- Retract 30mm before any move that parks the hot nozzle — anti-ooze. See [gcode/level_test.gcode](gcode/level_test.gcode) for the full Cura-derived prep.

## Bed leveling

| Command | Effect |
|---|---|
| `G29` | Auto-bed-level: probe 4 corner washers with nozzle as probe. Marlin LE quirk: does NOT emit per-corner Z data on serial, even with `G29 V4`. You only see `ok` at the end. Diagnose via `M119` z_min state changes during the descent. |
| `G30` | Single-point probe at current XY. At positions outside the firmware's probe area, silently returns `ok` without probing. |
| `M420 S1` | Enable leveling matrix (after G29). |
| `M420 V` | Dump current leveling matrix. Useful before reconnecting, since reconnect wipes it. |

## Endstops & probe

| Command | Effect |
|---|---|
| `M119` | Read endstop / probe state. See [Safe queries](#safe-queries-read-only-never-move-anything). |
| `M211 S0` | Disable software endstops. Used to allow Z below `Z_MIN_POS` during deep-descent calibration. **Caveat:** this firmware fork doesn't reliably honor `M211 S0` — exact accepted syntax is unconfirmed. Treat as best-effort. |
| `M211 S1` | Re-enable software endstops. |

## System / safety

| Command | Effect |
|---|---|
| `M84` | Disable steppers. Lets you push the head/bed by hand. |
| `M400` | Block until all queued moves finish. Useful before changing temps or shutting down. |
| `M410` | Quickstop. Aborts the current move buffer but leaves heat on. Good "stop now" without a power cycle. |
| `M112` | **EMERGENCY STOP.** Marlin halts everything. Requires a power cycle to recover. Use only when something is clearly wrong. |
| `M204 S<accel>` | Set acceleration (mm/s²). `M204 S300` is the CuraLE TPU probe setting. |
| `M107` | Fans off. |
| `M106 S<0-255>` | Part-cooling fan on at speed S. |

## Lulzbot Mini hardware constants

Validated by hand against this specific machine. Numbers may drift between Lulzbot Mini revisions — re-measure if behavior surprises you.

| Thing | Value |
|---|---|
| Bed size | 152 × 152 mm |
| Origin | `(0, 0)` at front-left. Y_MIN is at the front of the printer. |
| Nozzle | 0.5 mm (Lulzbot Mini stock) |
| Filament | 3 mm |
| Front-left washer XY | `(-3, -9)` (over-travel into the front-left corner) |
| Wiper pad XY range | `X ≈ 42–112, Y ≈ 171–173` (the wipe routine in `wipe_nozzle()` traces this) |
| Z=0 declaration | After washer contact, run `G92 Z1` — washer top is 1mm above bed glass |
| Deep-descent observation | After `G92 Z150`, washer contact triggers between Z=-32 and Z=-34 (i.e. ~182–184mm of travel from the assumed top). Earlier "no contact" failures were just shallow descent. |
| TPU wipe temp | 180 °C |
| TPU probe temp | 160 °C |
| TPU retract | 30 mm |
| TPU max push feed | F50 (50 mm/min) |
| Probe acceleration | `M204 S300` (CuraLE default for the G29 step) |

## Quick recipes

Compose into multi-line G-code; the `lulz print FILE.gcode` subcommand streams these line-by-line.

**Cold check (printer alive?):**
```
M115
M105
M114
M119
```

**Heat hotend to 180 and wait:**
```
M104 S180
M109 S180
```

**Heat both, wait for both:**
```
M140 S60
M104 S180
M190 S60
M109 S180
```

**TPU retract before parking hot:**
```
G91
G1 E-30 F75
G90
```

**Safe shutdown:**
```
M400
M104 S0
M140 S0
M84
```
