"""
Stream a G-code file to the printer line-by-line, waiting for `ok` on
each line. Logs every command and reply with timestamps so we can see
exactly where things go wrong.

Usage:
    python3 -u stream_gcode.py path/to/file.gcode
"""

import sys
import time

from printer import Printer


def strip_comment(line):
    # Drop ; comments and trim whitespace.
    semi = line.find(";")
    if semi >= 0:
        line = line[:semi]
    return line.strip()


def main():
    if len(sys.argv) < 2:
        print("usage: stream_gcode.py FILE.gcode", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    with open(path) as fh:
        lines = fh.readlines()

    p = Printer()

    sent = 0
    start = time.time()
    try:
        for raw in lines:
            cmd = strip_comment(raw)
            if not cmd:
                continue
            elapsed = time.time() - start
            print(f"[{elapsed:7.1f}s] >> {cmd}", flush=True)
            replies = p.send(cmd, echo=False)
            for r in replies:
                print(f"[{elapsed:7.1f}s] << {r}", flush=True)
                if r.lower().startswith(("error", "!!")):
                    print(f"[{elapsed:7.1f}s] ABORTING on error", flush=True)
                    return
            sent += 1
        print(f"[stream] done. {sent} commands sent in "
              f"{time.time() - start:.1f}s.", flush=True)
    finally:
        p.close()


if __name__ == "__main__":
    main()
