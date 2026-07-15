import os
import uuid
import asyncio
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import yt_dlp

app = FastAPI(title="MediaDown API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from frontend build in production
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

DOWNLOAD_DIR = Path("/tmp/mediadown")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# In-memory job store (use Redis in production)
jobs: dict = {}


class AnalyzeRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    format_id: str
    audio_only: bool = False
    audio_format: str = "mp3"


def cleanup_old_files():
    """Remove files older than 30 minutes."""
    cutoff = datetime.now() - timedelta(minutes=30)
    for f in DOWNLOAD_DIR.iterdir():
        if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink(missing_ok=True)


def get_ydl_opts(output_path: str):
    return {
        "outtmpl": output_path,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze_url(req: AnalyzeRequest):
    """Extract metadata and available formats from a URL."""
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "socket_timeout": 20,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)

        if not info:
            raise HTTPException(status_code=404, detail="No media found at this URL")

        # Build format list
        formats = []
        seen = set()
        raw_formats = info.get("formats", [])

        for f in raw_formats:
            ext = f.get("ext", "")
            height = f.get("height")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            fid = f.get("format_id", "")
            filesize = f.get("filesize") or f.get("filesize_approx")
            tbr = f.get("tbr")

            # Video formats
            if vcodec != "none" and height:
                label = f"{height}p"
                if label not in seen:
                    seen.add(label)
                    formats.append({
                        "id": fid,
                        "label": label,
                        "ext": ext,
                        "type": "video",
                        "height": height,
                        "filesize": filesize,
                        "note": f.get("format_note", ""),
                    })

        # Sort video formats by resolution descending
        formats.sort(key=lambda x: x.get("height", 0), reverse=True)

        # Add audio extraction options
        formats.append({"id": "bestaudio/best", "label": "MP3 (Audio)", "ext": "mp3", "type": "audio", "height": 0})
        formats.append({"id": "bestaudio/best", "label": "M4A (Audio)", "ext": "m4a", "type": "audio", "height": 0})

        # If no video formats found, add best available
        if not any(f["type"] == "video" for f in formats):
            formats.insert(0, {"id": "best", "label": "Best available", "ext": "mp4", "type": "video", "height": 9999})

        duration = info.get("duration")
        duration_str = None
        if duration:
            mins, secs = divmod(int(duration), 60)
            hrs, mins = divmod(mins, 60)
            duration_str = f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}"

        return {
            "title": info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail"),
            "duration": duration_str,
            "uploader": info.get("uploader"),
            "view_count": info.get("view_count"),
            "platform": info.get("extractor_key", "Unknown"),
            "formats": formats,
        }

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "not supported" in msg.lower() or "unsupported url" in msg.lower():
            raise HTTPException(status_code=400, detail="This URL is not supported. Try a YouTube, Vimeo, or SoundCloud link.")
        raise HTTPException(status_code=400, detail=f"Could not analyze URL: {msg[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


def do_download(job_id: str, url: str, format_id: str, audio_only: bool, audio_format: str):
    """Run download in a background thread."""
    cleanup_old_files()
    output_template = str(DOWNLOAD_DIR / f"{job_id}_%(title).80s.%(ext)s")

    try:
        jobs[job_id]["status"] = "downloading"
        jobs[job_id]["progress"] = 0

        def progress_hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                if total:
                    jobs[job_id]["progress"] = round((downloaded / total) * 100, 1)
                    jobs[job_id]["speed"] = d.get("_speed_str", "")
                    jobs[job_id]["eta"] = d.get("_eta_str", "")
            elif d["status"] == "finished":
                jobs[job_id]["progress"] = 95

        if audio_only:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_template,
                "quiet": True,
                "progress_hooks": [progress_hook],
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "192",
                }],
            }
        else:
            ydl_opts = {
                "format": f"{format_id}+bestaudio/best[height<={format_id.split('p')[0] if 'p' in format_id else 9999}]/best",
                "outtmpl": output_template,
                "quiet": True,
                "progress_hooks": [progress_hook],
                "merge_output_format": "mp4",
            }
            if format_id in ("best", "bestaudio/best"):
                ydl_opts["format"] = format_id

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the output file
        files = list(DOWNLOAD_DIR.glob(f"{job_id}_*"))
        if not files:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Download completed but file not found"
            return

        filepath = files[0]
        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["filename"] = filepath.name
        jobs[job_id]["filepath"] = str(filepath)

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)[:300]


@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    """Start an async download job."""
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "progress": 0, "created": datetime.now().isoformat()}

    # Determine if audio-only from format
    audio_only = req.audio_only or req.format_id in ("bestaudio/best",)
    audio_fmt = req.audio_format if req.audio_only else "mp3"

    thread = threading.Thread(
        target=do_download,
        args=(job_id, req.url, req.format_id, audio_only, audio_fmt),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@app.get("/api/job/{job_id}")
def get_job_status(job_id: str):
    """Poll job status."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/api/download/{job_id}")
def download_file(job_id: str):
    """Stream the completed file to the client."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail="File not ready")

    filepath = Path(job["filepath"])
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File expired or not found")

    return FileResponse(
        path=str(filepath),
        filename=job["filename"],
        media_type="application/octet-stream",
    )
