# OctoPrint on macOS — Lulzbot Mini setup

OctoPrint is the host program that owns the USB connection during a
print. You slice in PrusaSlicer (or CuraLE), upload the `.gcode` to
OctoPrint, click Print, and OctoPrint streams the file over USB.
This replaces Cura LE's flaky USB-printing role.

## Install (one-time)

OctoPrint runs inside its own Python venv. The venv lives at
`~/.octoprint-venv/`; OctoPrint's state (uploaded files, settings)
lives at `~/.octoprint/`.

```bash
/usr/local/bin/python3 -m venv ~/.octoprint-venv
~/.octoprint-venv/bin/pip install --upgrade pip wheel
~/.octoprint-venv/bin/pip install OctoPrint
```

## Start

```bash
~/.octoprint-venv/bin/octoprint serve
# or, after `source ~/.octoprint-venv/bin/activate`:
# octoprint serve
```

Open <http://localhost:5001> in any browser. First-launch shows the
setup wizard.

**Port note:** OctoPrint defaults to port 5000, but macOS's AirPlay
Receiver (Control Center) already binds to 5000. We use 5001 instead.
This is set in `~/.octoprint/config.yaml`:

```yaml
server:
  host: 127.0.0.1
  port: 5001
```

(Alternative: disable AirPlay Receiver in System Settings → General →
AirDrop & Handoff. We use 5001 to leave AirPlay alone.)

To run it in the background and keep a shell:
```bash
nohup ~/.octoprint-venv/bin/octoprint serve > ~/.octoprint/serve.log 2>&1 &
```

Stop it:
```bash
pkill -f "octoprint serve"
```

## Setup-wizard answers for our Lulzbot Mini

The wizard walks you through several screens. Use these:

### Access control
Enable access control and create a username/password. (Required — don't
expose OctoPrint to your network without one.)

### Online connectivity check
Enable.

### Plugin Blacklist & Anonymous Usage Tracking
Either choice is fine.

### Printer profile

| Field | Value |
|---|---|
| Name | Lulzbot Mini |
| Model | Lulzbot Mini |
| Form factor | Rectangular |
| Origin | Lower-left |
| Heated bed | Yes |
| Heated chamber | No |
| Width (X) | 152 |
| Depth (Y) | 152 |
| Height (Z) | 158 |
| Custom bounding box | No (use print volume) |
| Extruder count | 1 |
| Nozzle diameter | 0.5 mm |
| Offset | 0, 0 |
| Filament diameter | 2.85 mm |

### Server commands (optional)
Leave blank — OctoPrint runs as a regular Mac process.

### Webcam (optional)
Skip unless you have a USB webcam pointed at the printer.

## Connect to the printer

After the wizard, in the OctoPrint UI:

1. Top-left "Connection" panel:
   - **Serial Port**: `/dev/cu.usbmodem144101` (or whatever index shows)
   - **Baudrate**: `250000` (you may have to pick "Other" and type it
     manually — OctoPrint's dropdown stops at 115200 by default; in
     `~/.octoprint/config.yaml` you can add `additionalBaudrates: [250000]`
     under `serial:` to make it always appear)
   - **Printer Profile**: Lulzbot Mini
2. **Important:** quit any other program holding the port — CuraLE,
   our `lulz` CLI, `screen`, Pronterface — only one program can own
   `/dev/cu.usbmodem144101` at a time.
3. Click "Connect". Status should change to "Operational".

## Adding 250000 baud to the dropdown (recommended)

OctoPrint's default Connect dropdown doesn't list 250000. Add it once:

Edit `~/.octoprint/config.yaml`, append (or merge into existing `serial:`):

```yaml
serial:
  additionalBaudrates:
    - 250000
```

Restart OctoPrint. The dropdown will now include 250000.

## Print the calibration cube

1. Slice in PrusaSlicer (or use `python3 lulzbot/lulz.py slice
   xyzCalibration_cube.stl`) → produces `xyzCalibration_cube.gcode`.
2. In OctoPrint: drag the `.gcode` into the Files panel.
3. Click the print icon next to the file.
4. Watch the temperature graph and terminal log in the UI.

If G29 halts mid-startup (the known probe issue), abort from the UI's
big red **Cancel** button. Then see [README.md](README.md#troubleshooting-log)
for the nozzle-cleaning steps before retrying.

## How OctoPrint and `lulz` coexist

Only one program can hold the serial port at a time:

- **OctoPrint connected (printing or idle-but-connected)**: `lulz`
  commands will fail with "Resource busy". Disconnect in OctoPrint's
  UI ("Disconnect" button in the Connection panel) before using
  `lulz`, then reconnect when done.
- **OctoPrint disconnected**: `lulz` works as normal.

When to use which:

| Job | Tool |
|---|---|
| Day-to-day printing | OctoPrint (upload `.gcode`, click Print) |
| Probe troubleshooting / deep-descent Z cal | `lulz zcal` |
| Quick one-shot G-code probing | `lulz raw "M119"` |
| Heating / wiping the nozzle for cleaning | `lulz heat 150` then physical work |
| Slicing | PrusaSlicer (`lulz slice`) or CuraLE GUI |

## Useful OctoPrint plugins (install from the Plugin Manager later)

- **Bed Visualizer** — renders the `G29` mesh as a 3D heatmap. Will
  help diagnose whether the probe is producing sensible data once the
  wiring is fixed.
- **PrintTimeGenius** — better time estimates than the default.
- **OctoPrint-Cancelobject** — cancel a single object mid-print
  without aborting the whole job.

Skip these for now; the stock install is enough to start.
