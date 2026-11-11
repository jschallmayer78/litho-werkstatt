"""Lithophane-Werkstatt – Webservice.

Start lokal:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
import io
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, field_validator

from .litho.generate import run

MAX_FILES = int(os.getenv("MAX_FILES", "6"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "25"))
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
KEEP_MINUTES = int(os.getenv("KEEP_MINUTES", "60"))
def _job_dir():
    """Ablage für fertige Dateien; fällt auf das Temp-Verzeichnis zurück,
    wenn JOB_DIR nicht beschreibbar ist."""
    for base in (os.getenv("JOB_DIR"), tempfile.gettempdir()):
        if not base:
            continue
        d = Path(base) / "lithophane-jobs"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / ".probe").touch()
            return d
        except OSError:
            continue
    raise RuntimeError("Kein beschreibbares Verzeichnis für die Ausgabedateien gefunden.")


JOB_DIR = _job_dir()

Image.MAX_IMAGE_PIXELS = 60_000_000
SLOTS = threading.Semaphore(MAX_PARALLEL)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Lithophane-Werkstatt")


class Filament(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    color: str

    @field_validator("color")
    @classmethod
    def hex_color(cls, v):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("Farbe muss als #RRGGBB angegeben werden")
        return v


class Options(BaseModel):
    shape: Literal["cylinder", "flat"] = "cylinder"
    color_mode: Literal["mono", "multi"] = "mono"
    flat_method: Literal["cmy", "mosaic"] = "cmy"
    palette: List[Filament] = Field(default_factory=lambda: [Filament(name="Weiß", color="#FFFFFF")])
    quality: Literal["schnell", "normal", "fein"] = "normal"
    light_d: float = Field(83.0, ge=30, le=200)
    shade_d: Optional[float] = Field(None, ge=31, le=220)
    total_d: Optional[float] = Field(None, ge=35, le=260)
    pocket_depth: float = Field(3.0, ge=0, le=20)
    floor: float = Field(4.5, ge=1.5, le=25)
    thread_clearance: float = Field(0.4, ge=0.1, le=1.0)
    height: float = Field(120.0, ge=30, le=250)
    width: float = Field(120.0, ge=30, le=250)
    enclosure: bool = True
    cable: bool = True
    border: float = Field(3.0, ge=1, le=15)
    min_t: Optional[float] = Field(None, ge=0.3, le=3)
    max_t: Optional[float] = Field(None, ge=1, le=6)
    saturation: float = Field(1.2, ge=0.5, le=2.5)
    gamma: float = Field(1.0, ge=0.4, le=2.5)
    autocontrast: bool = False
    dither: bool = False
    color_max: float = Field(0.6, ge=0.2, le=1.5)
    yellow_max: Optional[float] = Field(None, ge=0.2, le=1.5)


def _cleanup():
    limit = time.time() - KEEP_MINUTES * 60
    for f in JOB_DIR.glob("*.3mf"):
        if f.stat().st_mtime < limit:
            f.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/generate")
def generate(files: List[UploadFile] = File(...), options: str = Form("{}")):
    _cleanup()
    try:
        opts = Options(**json.loads(options))
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(422, f"Ungültige Einstellungen: {e}")

    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(422, f"Bitte 1 bis {MAX_FILES} Bilder hochladen.")
    if opts.color_mode == "multi" and not (opts.shape == "flat" and opts.flat_method == "cmy"):
        if not 2 <= len(opts.palette) <= 8:
            raise HTTPException(422, "Die Palette braucht 2 bis 8 Filamente.")

    images = []
    for f in files:
        raw = f.file.read(MAX_FILE_MB * 1024 * 1024 + 1)
        if len(raw) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} ist größer als {MAX_FILE_MB} MB.")
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, f"{f.filename} ist kein lesbares Bild (JPG, PNG, WebP …).")
        images.append(img)

    o = opts.model_dump()
    o["palette"] = [p.model_dump() for p in opts.palette]
    if o["color_mode"] == "multi" and o["palette"]:
        o["palette"][0] = {"name": o["palette"][0]["name"], "color": "#FFFFFF"}

    if not SLOTS.acquire(timeout=300):
        raise HTTPException(503, "Gerade laufen zu viele Berechnungen. Bitte in einer Minute erneut versuchen.")
    try:
        result = run(images, o)
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        SLOTS.release()

    job = uuid.uuid4().hex
    (JOB_DIR / f"{job}.3mf").write_bytes(result.pop("data"))
    name = "lampenschirm" if opts.shape == "cylinder" else "platte"
    name += "_farbig" if opts.color_mode == "multi" else ""
    size = (JOB_DIR / f"{job}.3mf").stat().st_size
    return {"id": job, "filename": f"{name}.3mf", "size_mb": round(size / 1e6, 1), **result}


@app.get("/api/download/{job}")
def download(job: str, name: str = "lithophane.3mf"):
    if not re.fullmatch(r"[0-9a-f]{32}", job):
        raise HTTPException(404)
    path = JOB_DIR / f"{job}.3mf"
    if not path.exists():
        raise HTTPException(404, "Die Datei ist abgelaufen. Bitte das Modell neu erzeugen.")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:60] or "lithophane.3mf"
    return FileResponse(path, media_type="model/3mf", filename=safe)
