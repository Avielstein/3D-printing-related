"""
FastAPI app for the in-session browser UI.

Exposes the same set of operations the CLI session offers, against the
session's live `Printer` instance. The session calls `make_app(printer,
session)` and runs the returned app in a uvicorn worker thread.

Thread-safety: `Printer.send` is internally locked, so a request handler
calling it concurrently with the session's key loop serializes
naturally. Endpoints are sync functions — Starlette runs them in a
thread pool, which is what we want (sync serial I/O).

No auth, no CORS — UI is meant to be served on 127.0.0.1 only.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import printer as printer_mod
from printer import safety_lift


HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
LULZBOT_DIR = HERE.parent  # …/lulzbot/


# ---------- request models ----------

class JogBody(BaseModel):
    axis: str
    mm: float


class MoveBody(BaseModel):
    x: float
    y: float
    z: Optional[float] = None


class HeatBody(BaseModel):
    temp: int
    wait: bool = False


class PapertestStartBody(BaseModel):
    use_g28: bool = False


class PrintStartBody(BaseModel):
    file: str
    zcal: bool = False


class RawBody(BaseModel):
    gcode: str


# ---------- app factory ----------

def make_app(printer, session):
    """Build the FastAPI app bound to a specific Printer and Session.

    `session` carries the paper-test reference state (mode, post_ref_z)
    and the last recorded offset, so the UI wizard reads/writes the same
    fields the CLI uses.
    """
    app = FastAPI(title="lulz UI", docs_url=None, redoc_url=None)
    # Print state is owned by the Session and shared with the UI here.
    # A print started from `lulz>` is visible to the browser, and vice
    # versa — single source of truth.
    job = session.print_job

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    def index():
        idx = STATIC / "index.html"
        if not idx.is_file():
            raise HTTPException(500, "index.html missing")
        return FileResponse(str(idx))

    @app.get("/api/status")
    def status():
        temps = printer_mod.parse_temps(printer.send("M105"))
        pos = printer_mod.parse_position(printer.send("M114"))
        z_state = printer_mod.parse_z_min(printer.send("M119"))
        z_word = {True: "TRIGGERED", False: "open", None: "unknown"}[z_state]
        return {
            "temps": {"T": temps.get("T"), "B": temps.get("B")},
            "position": {
                "X": pos.get("X"), "Y": pos.get("Y"),
                "Z": pos.get("Z"), "E": pos.get("E"),
            },
            "z_min": z_word,
            "step": session.step,
            "papertest_armed": session.papertest_mode is not None,
            "last_offset": session.last_offset,
            "print": job.snapshot(),
            "ts": time.time(),
        }

    @app.post("/api/jog")
    def jog(body: JogBody):
        try:
            printer_mod.jog_one(printer, body.axis, body.mm)
        except ValueError as e:
            raise HTTPException(400, str(e))
        pos = printer_mod.parse_position(printer.send("M114"))
        return {"position": pos}

    @app.post("/api/move")
    def move(body: MoveBody):
        parts = [f"X{body.x}", f"Y{body.y}"]
        if body.z is not None:
            parts.append(f"Z{body.z}")
        printer.send("G90")
        printer.send("G1 " + " ".join(parts) + " F4000")
        return {"ok": True}

    @app.post("/api/home")
    def home():
        printer.send("G28 X Y")
        return {"ok": True}

    @app.post("/api/heat")
    def heat(body: HeatBody):
        printer.send(f"M104 S{body.temp}")
        if body.wait:
            printer_mod.wait_for_temp(printer, body.temp, axis="T")
        return {"ok": True, "temp": body.temp, "waited": body.wait}

    @app.post("/api/cool")
    def cool():
        printer.send("M104 S0")
        printer.send("M140 S0")
        return {"ok": True}

    @app.post("/api/zcal")
    def zcal():
        contact = printer_mod.z_calibrate(printer)
        if contact is None:
            raise HTTPException(500, "no z_min contact within descent range")
        return {"ok": True, "contact_z": contact}

    @app.post("/api/papertest/start")
    def papertest_start(body: PapertestStartBody):
        try:
            mode, post_ref_z = printer_mod.papertest_setup(
                printer, use_g28=body.use_g28)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        session.papertest_mode = mode
        session.papertest_post_ref_z = post_ref_z
        return {"ok": True, "mode": mode, "post_ref_z": post_ref_z}

    @app.post("/api/papertest/done")
    def papertest_done():
        if session.papertest_mode is None:
            raise HTTPException(400, "no papertest reference; call /start first")
        try:
            z_sweet, offset = printer_mod.papertest_offset(
                printer,
                mode=session.papertest_mode,
                post_ref_z=session.papertest_post_ref_z,
            )
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        printer_mod.papertest_lift(printer, 10)
        session.last_offset = offset
        session.papertest_mode = None
        session.papertest_post_ref_z = None
        return {
            "ok": True,
            "z_sweet": z_sweet,
            "offset": offset,
            "gcode": f"G92 Z{offset:.3f}",
        }

    @app.post("/api/papertest/write")
    def papertest_write():
        if session.last_offset is None:
            raise HTTPException(400, "no offset recorded yet")
        # Re-use the patch helper from session.py so CLI and UI write
        # exactly the same way.
        from session import patch_g92_z
        ini = LULZBOT_DIR / "gcode" / "lulzbot_mini_tpu.ini"
        if not ini.is_file():
            raise HTTPException(500, f"slicer config missing: {ini}")
        patch_g92_z(ini, session.last_offset)
        return {"ok": True, "patched": str(ini), "offset": session.last_offset}

    @app.post("/api/print/start")
    def print_start(body: PrintStartBody):
        if job.running:
            raise HTTPException(409, "a print is already running")
        # Resolve relative paths against the repo root (parent of lulzbot/)
        # so the browser's file picker — which lists paths relative to
        # that — works without further translation.
        if Path(body.file).is_absolute():
            file_arg = body.file
        else:
            file_arg = str((LULZBOT_DIR.parent / body.file).resolve())
        ok = session.start_print(file_arg, zcal=body.zcal, foreground=False)
        if not ok:
            raise HTTPException(400, "could not start print "
                                "(file not found, or another print running)")
        return {"ok": True, "file": file_arg}

    @app.get("/api/print/status")
    def print_status():
        return job.snapshot()

    @app.post("/api/print/cancel")
    def print_cancel():
        if not job.running:
            return {"ok": True, "noop": True}
        job.cancel_event.set()
        return {"ok": True}

    @app.post("/api/raw")
    def raw(body: RawBody):
        replies = printer.send(body.gcode)
        return {"replies": replies}

    @app.post("/api/panic")
    def panic():
        printer.send("M410")
        printer.send("M104 S0")
        printer.send("M140 S0")
        # Don't M84 here — UI panic should leave steppers powered so the
        # head holds position. CLI `p` does the same; only OS-shell
        # `lulz panic` (one-shot) cuts steppers, since that path ends
        # with a close anyway.
        if job.running:
            job.cancel_event.set()
        return {"ok": True}

    @app.get("/api/gcode-files")
    def gcode_files():
        """List .gcode files in the repo root and lulzbot/ — picker source."""
        roots = [LULZBOT_DIR.parent, LULZBOT_DIR, LULZBOT_DIR / "gcode"]
        files = []
        seen = set()
        for root in roots:
            if not root.is_dir():
                continue
            for f in sorted(root.glob("*.gcode")):
                key = str(f.resolve())
                if key in seen:
                    continue
                seen.add(key)
                try:
                    rel = f.relative_to(LULZBOT_DIR.parent)
                except ValueError:
                    rel = f
                files.append({"name": f.name, "path": str(rel),
                              "size": f.stat().st_size})
        return {"files": files}

    return app
