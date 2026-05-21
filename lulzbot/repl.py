"""
Single-terminal REPL for the Lulzbot session.

Tails /tmp/lulzbot_log AND lets you send commands. You and the
assistant both write to the same /tmp/lulzbot_cmd file, so both of you
control the printer from any number of REPLs at once.

Shortcuts (translated to G-code on send):
    up N / down N         Z by N mm (default 5)
    left N / right N      X by N mm
    fwd N / back N        Y by N mm
    home                  G28 home all
    where                 M114 position
    endstops              M119 endstop / probe state
    stop                  M410 quickstop
    estop                 M112 EMERGENCY (needs power cycle)
    EXIT                  shut down the session

  Macros (multi-step routines):
    clean [TEMP]          Heat to TEMP (default 150) and park head
                          forward-center for manual nozzle scrubbing.
    extrude [MM]          Extrude MM mm of filament slowly (default 10,
                          F50 — safe for TPU).
    probe                 Full TPU bed-leveling test: heat to wipe temp,
                          retract 30mm, cool to probe temp, G29.
    heat TEMP             Set hotend target (no wait).
    cool                  Set both hotend and bed targets to 0.

Anything else is sent raw as G-code, e.g. M105, G30, G28 X.

Ctrl-D / Ctrl-C exits this REPL. The session keeps running.

Usage:
    python3 repl.py
"""

import os
import sys
import threading
import time

CMD = "/tmp/lulzbot_cmd"
LOG = "/tmp/lulzbot_log"


def tail():
    try:
        f = open(LOG)
    except FileNotFoundError:
        sys.stderr.write(f"[!] {LOG} not found — is session.py running?\n")
        return
    f.seek(0, 2)
    while True:
        line = f.readline()
        if line:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            time.sleep(0.1)


def translate(s):
    parts = s.split()
    if not parts:
        return None
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else "5"
    moves = {
        "up":      ("Z", "",  "600"),
        "down":    ("Z", "-", "300"),
        "left":    ("X", "-", "2000"),
        "right":   ("X", "",  "2000"),
        "fwd":     ("Y", "-", "2000"),
        "forward": ("Y", "-", "2000"),
        "back":    ("Y", "",  "2000"),
    }
    if cmd in moves:
        axis, sign, feed = moves[cmd]
        return f"G91\nG1 {axis}{sign}{arg} F{feed}\nG90"
    aliases = {
        "home":     "G28",
        "where":    "M114",
        "endstops": "M119",
        "stop":     "M410",
        "estop":    "M112",
        "exit":     "EXIT",
    }
    if cmd in aliases:
        return aliases[cmd]

    # Macros (multi-line G-code routines).
    if cmd in ("wipe", "clean"):
        t = parts[1] if len(parts) > 1 else "180"
        return "\n".join([
            f"M104 S{t}",
            "G28",
            f"M109 R{t}",
            "G91", "G1 E-30 F75", "G90",
            "G1 X42 Y173 F11520",
            "G1 Z0 F1200",
            "G1 X42 Y173 Z-0.5 F4000",
            "G1 X52 Y171 Z-0.5 F4000",
            "G1 X42 Y173 Z0 F4000",
            "G1 X52 Y171 F4000",
            "G1 X42 Y173 F4000",
            "G1 X52 Y171 F4000",
            "G1 X57 Y173 F4000",
            "G1 X77 Y171 F4000",
            "G1 X87 Y171 F4000",
            "G1 X97 Y171 F4000",
            "G1 X107 Y173 F4000",
            "G1 X112 Y171 Z-0.5 F1000",
            "G1 Z10",
            "G28 X Y",
            "M104 S0",
        ])
    if cmd == "park":
        t = parts[1] if len(parts) > 1 else "150"
        return "\n".join([
            f"M104 S{t}",
            "G28",
            "G1 Z100 F1000",
            "G1 X80 Y20 F4000",
            "M400",
            f"M109 S{t}",
        ])
    if cmd == "extrude":
        mm = parts[1] if len(parts) > 1 else "10"
        return "\n".join(["M83", f"G1 E{mm} F50"])
    if cmd == "heat":
        return f"M104 S{parts[1] if len(parts) > 1 else 200}"
    if cmd == "heatwait":
        # Set hotend target AND wait until reached. Default 225 for TPU.
        t = parts[1] if len(parts) > 1 else "225"
        return f"M109 S{t}"
    if cmd == "cool":
        return "M104 S0\nM140 S0"
    if cmd == "manlevel":
        # Skip Z homing (broken probe). Home XY only, assume head
        # is near the top of Z travel, move to bed center, drop to
        # 20mm. User then uses `down N` with a paper between nozzle
        # and bed until paper just catches, then `setzero`.
        return "\n".join([
            "G28 X Y",
            "G92 Z150",
            "M211 S0",
            "G0 X75 Y75 F4000",
            "G0 Z20 F600",
        ])
    if cmd == "setzero":
        # Declare current Z as bed surface. Then lift safely.
        return "G92 Z0\nG91\nG0 Z10 F600\nG90"
    if cmd == "probe":
        return "\n".join([
            "M104 S180",
            "G28",
            "M109 R180",
            "G91", "G1 E-30 F75", "G90",
            "G28 X Y",
            "G0 X0 Y187 F200",
            "M109 R160",
            "M204 S300",
            "M211 S0",
            "G29",
            "M420 V",
            "M104 S0",
        ])

    # Raw G-code passthrough.
    return s


def main():
    sys.stdout.write(
        "Lulzbot REPL. Type 'help' for shortcuts. Ctrl-D to exit.\n"
        "Log lines from the session will stream in here.\n\n"
    )
    sys.stdout.flush()

    threading.Thread(target=tail, daemon=True).start()

    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("help", "?"):
            sys.stdout.write(__doc__.split("Usage:")[0])
            sys.stdout.flush()
            continue
        if line.lower() in ("clear", "cls"):
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
            continue
        if line.lower() in ("panic", "abort"):
            sys.stdout.write("[PANIC] killing all printer scripts...\n")
            sys.stdout.flush()
            import os as _os
            _os.system("pkill -9 -f 'test_object.py|test_print.py|"
                       "test_corners.py|heat_and_hold.py|"
                       "extrude_test.py|stream_gcode.py|session.py'")
            time.sleep(2)
            sys.stdout.write("[PANIC] sending heat-off / steppers-off "
                             "directly to printer...\n")
            sys.stdout.flush()
            try:
                from printer import Printer as _Printer
                _p = _Printer()
                _p.send("M410")
                _p.send("M104 S0")
                _p.send("M140 S0")
                _p.send("M84")
                _p.close()
                sys.stdout.write("[PANIC] complete. Printer safe.\n")
            except Exception as e:
                sys.stdout.write(f"[PANIC] direct connect failed: {e}\n")
                sys.stdout.write("[PANIC] manually power-cycle the "
                                 "printer if heaters stay on.\n")
            sys.stdout.flush()
            continue
        gcode = translate(line)
        if gcode is None:
            continue
        with open(CMD, "a") as fh:
            fh.write(gcode + "\n")


if __name__ == "__main__":
    main()
