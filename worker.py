"""
Kick.com Live Stream Clipper — Background Worker
=================================================
FastAPI server that captures a Kick live stream via streamlink + ffmpeg,
maintains a rolling 5-minute segment buffer, and exposes an API to slice
clips on demand.
"""

import asyncio
import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests as http_requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

import imageio_ffmpeg
from curl_cffi import requests as cffi_requests

# Use environment vars set by app.py when running as frozen exe, else use file location
BASE_DIR = Path(os.environ.get("CLIPPER_BASE_DIR", Path(__file__).resolve().parent))
BUNDLE_DIR = Path(os.environ.get("CLIPPER_BUNDLE_DIR", Path(__file__).resolve().parent))
BUFFER_DIR = BASE_DIR / "buffer"
CONFIG_FILE = BASE_DIR / "config.json"
SEGMENT_DURATION = 10          # seconds per .ts segment
MAX_BUFFER_AGE = 310           # delete segments older than this (300 + grace)
CLEANUP_INTERVAL = 10          # run cleanup every N seconds

# Resolve the bundled FFmpeg executable path
FFMPEG_CMD = imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Ensure directories exist
# ---------------------------------------------------------------------------

BUFFER_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Config Persistence
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load saved config from disk."""
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def get_clips_dir() -> Path | None:
    """Get the current clips saving directory from configuration."""
    config = load_config()
    saved = config.get("clips_dir")
    if saved:
        p = Path(saved)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    return None


def save_config(data: dict):
    """Save config to disk."""
    try:
        existing = load_config()
        existing.update(data)
        CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to save config: %s", e)
log.info("Resolved ffmpeg:     %s", FFMPEG_CMD)

# ---------------------------------------------------------------------------
# App State
# ---------------------------------------------------------------------------

class AppState:
    """Mutable singleton holding runtime state."""

    def __init__(self):
        self.channel: str | None = None
        self.platform: str = "kick"
        self.recording: bool = False
        self.connecting: bool = False
        self.error: str | None = None
        self.started_at: float | None = None
        self._capture_proc: subprocess.Popen | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._capture_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self.custom_recording_started_at: float | None = None

state = AppState()

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class SetChannelRequest(BaseModel):
    channel: str = Field(..., min_length=1, max_length=100)
    platform: str = Field("kick", min_length=1, max_length=20)

class SetClipsDirRequest(BaseModel):
    path: str = Field(..., min_length=1)

class CreateClipRequest(BaseModel):
    duration: int = Field(..., ge=10, le=300)
    title: str = Field(..., min_length=1, max_length=200)

class CustomRecordStopRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

from version import VERSION, GITHUB_REPO, GITHUB_API_LATEST, ASSET_NAME

app = FastAPI(title="Kick Clipper Worker", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Dashboard Serving
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the dashboard HTML from the bundle directory."""
    dashboard_path = BUNDLE_DIR / "dashboard.html"
    if not dashboard_path.exists():
        # Fallback: try BASE_DIR
        dashboard_path = BASE_DIR / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:120] or "clip"


def get_sorted_segments() -> list[Path]:
    """Return buffer segment files sorted by modification time (oldest first)."""
    segments = list(BUFFER_DIR.glob("segment_*.ts"))
    segments.sort(key=lambda p: p.stat().st_mtime)
    return segments


def get_buffer_seconds() -> float:
    """Estimate total buffered seconds from segment count."""
    return len(get_sorted_segments()) * SEGMENT_DURATION

# ---------------------------------------------------------------------------
# Stream Capture
# ---------------------------------------------------------------------------

def _resolve_kick_hls(channel: str) -> str | None:
    """
    Resolve the HLS .m3u8 playback URL for a Kick channel using the Kick API
    directly via curl_cffi (bypasses Cloudflare). Returns the URL or None.
    """
    # Try the livestream-specific endpoint first
    api_urls = [
        f"https://kick.com/api/v2/channels/{channel}/livestream",
        f"https://kick.com/api/v2/channels/{channel}",
    ]

    for api_url in api_urls:
        try:
            log.info("Querying Kick API: %s", api_url)
            resp = cffi_requests.get(
                api_url,
                impersonate="chrome",
                timeout=15,
                headers={
                    "Accept": "application/json",
                    "Referer": f"https://kick.com/{channel}",
                },
            )

            if resp.status_code == 404:
                log.warning("Channel '%s' not found (404)", channel)
                return None
            if resp.status_code == 403:
                log.warning("Kick API returned 403 for %s, trying next endpoint...", api_url)
                continue
            if resp.status_code != 200:
                log.warning("Kick API returned %d for %s", resp.status_code, api_url)
                continue

            data = resp.json()

            # Livestream endpoint returns {"data": {"playback_url": "..."}}
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                playback = data["data"].get("playback_url")
                if playback and ".m3u8" in playback:
                    return playback
                # data is None means channel is offline
                if data["data"] is None or not playback:
                    log.info("Channel '%s' is offline (livestream data is empty)", channel)
                    return None

            # Channel endpoint returns {"playback_url": "...", "livestream": {...}}
            playback = data.get("playback_url", "")
            livestream = data.get("livestream")

            if livestream and isinstance(livestream, dict):
                is_live = livestream.get("is_live", False)
                if not is_live:
                    log.info("Channel '%s' is offline (is_live=False)", channel)
                    return None
                # Some responses have the URL inside livestream
                ls_playback = livestream.get("playback_url", "")
                if ls_playback and ".m3u8" in ls_playback:
                    return ls_playback

            if playback and ".m3u8" in playback:
                # Channel has a playback URL but might not be live
                # Verify the stream is actually live
                if livestream is None:
                    log.info("Channel '%s' is offline (no livestream data)", channel)
                    return None
                return playback

        except Exception as e:
            log.warning("Kick API request failed for %s: %s", api_url, e)
            continue

    return None


def _resolve_tiktok_hls(channel: str) -> str | None:
    """
    Resolve the HLS .m3u8 playback URL for a TikTok live channel using the TikTok API
    directly via curl_cffi. Returns the URL or None.
    """
    url = "https://www.tiktok.com/api-live/user/room"
    params = {
        "aid": 1988,
        "sourceType": 54,
        "uniqueId": channel,
    }
    headers = {
        "Referer": f"https://www.tiktok.com/@{channel}/live",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        log.info("Querying TikTok API for channel: %s", channel)
        resp = cffi_requests.get(
            url,
            params=params,
            headers=headers,
            impersonate="chrome",
            timeout=15
        )
        if resp.status_code != 200:
            log.warning("TikTok API returned HTTP %d for %s", resp.status_code, channel)
            return None

        json_data = resp.json()
        if json_data.get("statusCode") != 0:
            log.warning("TikTok API status error: %s", json_data.get("message"))
            return None

        data = json_data.get("data", {})
        user_data = data.get("user", {})
        room_id = user_data.get("roomId")

        live_room = data.get("liveRoom", {})
        if not live_room:
            log.warning("No liveRoom data in TikTok response for %s", channel)
            return None

        status = live_room.get("status")
        if not status:
            status = user_data.get("status")

        if status == 4:
            log.info("TikTok Channel '%s' is offline (status=4)", channel)
            return None

        pull_data = live_room.get("streamData", {}).get("pull_data", {})
        stream_data_str = pull_data.get("stream_data")

        # Fallback to webcast API if stream_data is missing but we have a room_id
        if not stream_data_str and room_id:
            log.info("Primary stream_data missing for %s. Querying webcast API for roomId %s...", channel, room_id)
            try:
                webcast_url = "https://webcast.tiktok.com/webcast/room/info"
                webcast_params = {
                    "aid": 1988,
                    "room_id": room_id,
                }
                webcast_resp = cffi_requests.get(
                    webcast_url,
                    params=webcast_params,
                    headers=headers,
                    impersonate="chrome",
                    timeout=15
                )
                if webcast_resp.status_code == 200:
                    webcast_json = webcast_resp.json()
                    webcast_room_data = webcast_json.get("data", {})
                    webcast_status = webcast_room_data.get("status")
                    if webcast_status == 4:
                        log.info("TikTok Channel '%s' is offline via webcast API", channel)
                        return None

                    webcast_stream_url = webcast_room_data.get("stream_url", {})
                    webcast_sdk_data = webcast_stream_url.get("live_core_sdk_data", {})
                    webcast_pull_data = webcast_sdk_data.get("pull_data", {})
                    stream_data_str = webcast_pull_data.get("stream_data")
                else:
                    log.warning("Webcast API returned HTTP %d", webcast_resp.status_code)
            except Exception as e:
                log.warning("Failed to fetch webcast data for %s: %s", channel, e)

        if not stream_data_str:
            log.warning("No stream_data string in TikTok responses for %s", channel)
            return None

        stream_data = json.loads(stream_data_str)
        stream_profiles = stream_data.get("data", {})

        # Priority order of qualities: origin -> uhd -> hd -> sd -> ld
        for quality in ["origin", "uhd", "hd", "sd", "ld"]:
            profile = stream_profiles.get(quality, {})
            # Check main HLS
            hls_url = profile.get("main", {}).get("hls")
            if hls_url:
                log.info("Resolved TikTok HLS URL (quality: %s)", quality)
                return hls_url
            # Check backup HLS
            hls_url_backup = profile.get("backup", {}).get("hls")
            if hls_url_backup:
                log.info("Resolved TikTok backup HLS URL (quality: %s)", quality)
                return hls_url_backup

        # Fallback to FLV if no HLS is available
        for quality in ["origin", "uhd", "hd", "sd", "ld"]:
            profile = stream_profiles.get(quality, {})
            flv_url = profile.get("main", {}).get("flv")
            if flv_url:
                log.info("Resolved TikTok FLV URL (quality: %s)", quality)
                return flv_url

    except Exception as e:
        log.warning("TikTok API request failed for %s: %s", channel, e)

    return None


async def _run_capture(channel: str, platform: str):
    """
    Resolve HLS stream URL using appropriate API directly via curl_cffi
    and launch ffmpeg to capture it in a background thread.
    """
    state.error = None
    state.recording = False
    state.connecting = True

    log.info("Resolving stream for channel: %s (%s)", channel, platform)

    try:
        # Resolve HLS URL (blocking call wrapped in thread)
        try:
            if platform == "tiktok":
                hls_url = await asyncio.wait_for(
                    asyncio.to_thread(_resolve_tiktok_hls, channel),
                    timeout=20.0,
                )
            else:
                hls_url = await asyncio.wait_for(
                    asyncio.to_thread(_resolve_kick_hls, channel),
                    timeout=20.0,
                )
        except asyncio.TimeoutError:
            state.error = "Connection timed out. Please check your internet or try again."
            log.error(state.error)
            return
        finally:
            state.connecting = False

        if not hls_url:
            state.error = "Channel is offline or not found"
            log.error(state.error)
            return

        log.info("Resolved HLS URL: %s", hls_url[:80] + "...")

        # Launch ffmpeg
        segment_pattern = str(BUFFER_DIR / "segment_%06d.ts")
        ffmpeg_args = [
            FFMPEG_CMD,
            "-hide_banner",
            "-loglevel", "warning",
            "-i", hls_url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(SEGMENT_DURATION),
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            "-break_non_keyframes", "1",
            segment_pattern,
        ]

        log.info("Starting ffmpeg capture from stream URL")

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = 0x08000000  # CREATE_NO_WINDOW

        ff_proc = subprocess.Popen(
            ffmpeg_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
        )

        state._ffmpeg_proc = ff_proc
        state.recording = True
        state.started_at = time.time()
        log.info("Recording started for channel: %s (%s)", channel, platform)

        # Wait for ffmpeg to exit
        await asyncio.to_thread(ff_proc.wait)

        state.recording = False
        if ff_proc.returncode not in (0, 1, -15):  # ignore normal shutdown signals
            stderr_out = ff_proc.stderr.read().decode(errors="replace")[:500]
            state.error = f"ffmpeg exited with code {ff_proc.returncode}"
            log.error("%s: %s", state.error, stderr_out)
        else:
            log.info("ffmpeg exited cleanly")

    except asyncio.CancelledError:
        log.info("Capture task cancelled")
        state.recording = False
        raise
    except Exception as exc:
        state.recording = False
        state.error = str(exc)
        log.exception("Capture error")
    finally:
        state.recording = False
        state.connecting = False
        state._ffmpeg_proc = None


def _kill_procs():
    """Terminate any running ffmpeg processes."""
    proc = state._ffmpeg_proc
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    state._ffmpeg_proc = None


async def _stop_capture():
    """Stop any active capture gracefully."""
    if state._capture_task and not state._capture_task.done():
        state._capture_task.cancel()
        try:
            await state._capture_task
        except asyncio.CancelledError:
            pass
    _kill_procs()
    state.recording = False
    state.connecting = False
    state.started_at = None
    state.custom_recording_started_at = None


def _clear_buffer():
    """Delete all segments from the buffer directory."""
    for f in BUFFER_DIR.glob("segment_*.ts"):
        try:
            f.unlink()
        except OSError:
            pass

# ---------------------------------------------------------------------------
# Buffer Cleanup
# ---------------------------------------------------------------------------

async def _cleanup_loop():
    """Periodically remove buffer segments older than MAX_BUFFER_AGE."""
    while True:
        try:
            now = time.time()
            removed = 0
            
            # If custom recording is active, do not delete any segments created since it started (minus a 5s grace margin)
            limit = now - MAX_BUFFER_AGE
            if state.custom_recording_started_at is not None:
                limit = min(limit, state.custom_recording_started_at - 5)
                
            for f in BUFFER_DIR.glob("segment_*.ts"):
                try:
                    if f.stat().st_mtime < limit:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
            if removed:
                log.debug("Cleaned up %d old segment(s)", removed)
        except Exception:
            log.exception("Cleanup error")
        await asyncio.sleep(CLEANUP_INTERVAL)

# ---------------------------------------------------------------------------
# Clip Creation
# ---------------------------------------------------------------------------

async def create_clip(duration: int, title: str) -> Path:
    """
    Slice the last `duration` seconds from the rolling buffer into a single
    MP4 file. Uses ffmpeg concat demuxer with -c copy (no re-encoding).
    """
    segments = get_sorted_segments()
    if not segments:
        raise HTTPException(status_code=409, detail="Buffer is empty — no segments available.")

    # Determine how many segments we need
    needed = max(1, (duration + SEGMENT_DURATION - 1) // SEGMENT_DURATION)
    selected = segments[-needed:]

    if len(selected) * SEGMENT_DURATION < duration * 0.5:
        raise HTTPException(
            status_code=409,
            detail=f"Not enough buffer. Have ~{len(selected) * SEGMENT_DURATION}s, need {duration}s.",
        )

    # Build concat file list
    concat_file = BUFFER_DIR / "_concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for seg in selected:
            # Use forward slashes and escape single quotes for ffmpeg
            safe_path = str(seg.resolve()).replace("\\", "/")
            f.write(f"file '{safe_path}'\n")

    # Output filename
    safe_title = sanitize_filename(title)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_name = f"{safe_title}_{timestamp}.mp4"
    
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not set. Please select a clips folder.")
    output_path = clips_dir / output_name

    # Run ffmpeg concat
    ffmpeg_args = [
        FFMPEG_CMD,
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]

    log.info("Creating clip: %s (%d segments, ~%ds)", output_name, len(selected), duration)

    kwargs = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_args,
        **kwargs
    )
    _, stderr = await proc.communicate()

    # Clean up concat list
    try:
        concat_file.unlink()
    except OSError:
        pass

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()[:500]
        log.error("Clip creation failed: %s", err_msg)
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {err_msg}")

    file_size = output_path.stat().st_size
    log.info("Clip saved: %s (%.2f MB)", output_path.name, file_size / 1024 / 1024)
    return output_path

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def api_status():
    """Return current worker status."""
    segments = get_sorted_segments()
    buffered_seconds = len(segments) * SEGMENT_DURATION
    uptime = round(time.time() - state.started_at, 1) if state.started_at else 0
    clips_dir = get_clips_dir()

    return {
        "channel": state.channel,
        "platform": state.platform,
        "recording": state.recording,
        "connecting": state.connecting,
        "error": state.error,
        "buffer_segments": len(segments),
        "buffer_seconds": buffered_seconds,
        "uptime_seconds": uptime,
        "clips_dir": str(clips_dir.resolve()) if clips_dir else None,
        "custom_recording_started_at": state.custom_recording_started_at,
        "custom_recording_active": state.custom_recording_started_at is not None,
    }


@app.post("/api/set-clips-dir")
async def api_set_clips_dir(req: SetClipsDirRequest):
    """Set the clips saving directory."""
    path_str = req.path.strip()
    if not path_str:
        raise HTTPException(status_code=400, detail="Path cannot be empty.")
    p = Path(path_str)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid folder path or permission denied: {e}")
    
    save_config({"clips_dir": str(p.resolve())})
    return {"ok": True, "clips_dir": str(p.resolve())}


@app.post("/api/set-channel")
async def api_set_channel(req: SetChannelRequest):
    """Set or change the target channel and platform."""
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not set. Please select a clips folder first.")

    channel = req.channel.strip().lower()
    platform = req.platform.strip().lower()

    if platform == "tiktok" and channel.startswith("@"):
        channel = channel[1:]

    # Stop any existing capture
    await _stop_capture()
    _clear_buffer()

    state.channel = channel
    state.platform = platform
    state.error = None

    # Save channel and platform to config for auto-reconnect
    save_config({"last_channel": channel, "last_platform": platform})

    # Start new capture in background
    state._capture_task = asyncio.create_task(_run_capture(channel, platform))

    return {"ok": True, "channel": channel, "platform": platform, "message": f"Connecting to {channel} ({platform})..."}


@app.post("/api/stop")
async def api_stop_capture_endpoint():
    """Stop capture and clear buffer."""
    await _stop_capture()
    _clear_buffer()
    state.channel = None
    state.platform = "kick"
    return {"ok": True}


@app.post("/api/custom-record/start")
async def api_custom_record_start():
    if not state.recording:
        raise HTTPException(status_code=409, detail="Not currently recording. Connect to a channel first.")
    if state.custom_recording_started_at is not None:
        raise HTTPException(status_code=409, detail="Custom recording is already active.")
    
    state.custom_recording_started_at = time.time()
    log.info("Custom recording started at %s", state.custom_recording_started_at)
    return {"ok": True, "started_at": state.custom_recording_started_at}


@app.post("/api/custom-record/stop")
async def api_custom_record_stop(req: CustomRecordStopRequest):
    if not state.recording:
        raise HTTPException(status_code=409, detail="Not currently recording. Connect to a channel first.")
    if state.custom_recording_started_at is None:
        raise HTTPException(status_code=409, detail="No custom recording is active.")
        
    start_time = state.custom_recording_started_at
    state.custom_recording_started_at = None  # Reset state early
    
    # Get segments modified after start_time (with a 5-second grace margin to capture the first segment fully)
    segments = get_sorted_segments()
    selected = [seg for seg in segments if seg.stat().st_mtime >= start_time - 5]
    
    if not selected:
        raise HTTPException(status_code=409, detail="No video segments captured during the recording duration.")
        
    # Build concat file list
    concat_file = BUFFER_DIR / "_concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for seg in selected:
            safe_path = str(seg.resolve()).replace("\\", "/")
            f.write(f"file '{safe_path}'\n")
            
    # Output filename
    safe_title = sanitize_filename(req.title)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_name = f"{safe_title}_{timestamp}.mp4"
    
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not set.")
    output_path = clips_dir / output_name
    
    # Run ffmpeg concat
    ffmpeg_args = [
        FFMPEG_CMD,
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    
    log.info("Creating custom record clip: %s (%d segments)", output_name, len(selected))
    
    kwargs = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_args,
        **kwargs
    )
    _, stderr = await proc.communicate()
    
    try:
        concat_file.unlink()
    except OSError:
        pass
        
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()[:500]
        log.error("Custom record save failed: %s", err_msg)
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {err_msg}")
        
    file_size = output_path.stat().st_size
    log.info("Custom record saved: %s (%.2f MB)", output_path.name, file_size / 1024 / 1024)
    
    return {
        "ok": True,
        "filename": output_path.name,
        "path": str(output_path),
        "size_bytes": file_size,
    }


@app.post("/api/custom-record/cancel")
async def api_custom_record_cancel():
    state.custom_recording_started_at = None
    return {"ok": True}


@app.post("/api/create-clip")
async def api_create_clip(req: CreateClipRequest):
    """Create a clip from the rolling buffer."""
    if not state.recording:
        raise HTTPException(status_code=409, detail="Not currently recording. Connect to a channel first.")

    output_path = await create_clip(req.duration, req.title)

    return {
        "ok": True,
        "filename": output_path.name,
        "path": str(output_path),
        "size_bytes": output_path.stat().st_size,
    }


@app.get("/api/version")
async def api_version():
    """Return current app version."""
    return {"version": VERSION, "repo": GITHUB_REPO}


@app.get("/api/config")
async def api_get_config():
    """Return saved configuration."""
    return load_config()


@app.get("/api/check-update")
async def api_check_update():
    """Check GitHub Releases for a newer version."""
    try:
        resp = http_requests.get(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
        if resp.status_code == 404:
            return {"update_available": False, "message": "No releases found"}
        resp.raise_for_status()
        data = resp.json()

        latest_tag = data.get("tag_name", "").lstrip("v")
        current = VERSION

        # Simple version comparison
        update_available = latest_tag != current and latest_tag > current

        # Find the .exe asset download URL
        download_url = None
        asset_size = 0
        for asset in data.get("assets", []):
            if asset["name"].lower() == ASSET_NAME.lower():
                download_url = asset["browser_download_url"]
                asset_size = asset["size"]
                break

        return {
            "update_available": update_available,
            "current_version": current,
            "latest_version": latest_tag,
            "release_name": data.get("name", ""),
            "release_notes": data.get("body", ""),
            "download_url": download_url,
            "asset_size": asset_size,
            "html_url": data.get("html_url", ""),
        }
    except Exception as e:
        log.warning("Update check failed: %s", e)
        return {"update_available": False, "error": str(e)}


@app.post("/api/update")
async def api_apply_update():
    """
    Download the latest release .exe and launch the updater to replace
    the current executable. Only works when running as a frozen .exe.
    """
    if not getattr(sys, "frozen", False):
        raise HTTPException(
            status_code=400,
            detail="Auto-update only works when running as a compiled .exe. "
                   "For development, pull the latest code from GitHub."
        )

    # Check for update info
    check = await api_check_update()
    if not check.get("update_available"):
        raise HTTPException(status_code=409, detail="No update available.")

    download_url = check.get("download_url")
    if not download_url:
        raise HTTPException(status_code=409, detail="No downloadable .exe found in the latest release.")

    try:
        # Download the new exe
        log.info("Downloading update from: %s", download_url)
        resp = http_requests.get(download_url, stream=True, timeout=120)
        resp.raise_for_status()

        temp_dir = Path(tempfile.mkdtemp(prefix="kickclipper_update_"))
        new_exe_path = temp_dir / ASSET_NAME

        with open(new_exe_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        log.info("Update downloaded: %s (%.2f MB)", new_exe_path,
                 new_exe_path.stat().st_size / 1024 / 1024)

        # Find the updater executable (bundled inside)
        current_exe = Path(sys.executable)
        bundled_updater = BUNDLE_DIR / "updater.exe"

        if not bundled_updater.exists():
            raise HTTPException(
                status_code=500,
                detail="Bundled updater.exe not found inside the application package."
            )

        # Copy updater to the temp directory so it runs outside the app bundle
        temp_updater_path = temp_dir / "updater.exe"
        try:
            shutil.copy2(bundled_updater, temp_updater_path)
            log.info("Copied updater to temp path: %s", temp_updater_path)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to copy updater to temp directory: {e}"
            )

        # Launch the updater as a detached process from the temp directory
        log.info("Launching updater: %s", temp_updater_path)
        subprocess.Popen(
            [str(temp_updater_path), str(new_exe_path), str(current_exe)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        # Schedule shutdown after a brief delay
        async def _delayed_shutdown():
            await asyncio.sleep(1)
            os._exit(0)

        asyncio.create_task(_delayed_shutdown())

        return {"ok": True, "message": "Update downloaded. Restarting..."}

    except HTTPException:
        raise
    except Exception as e:
        log.exception("Update failed")
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")

# ---------------------------------------------------------------------------
# Clips Library Endpoints
# ---------------------------------------------------------------------------

class ClipActionRequest(BaseModel):
    filename: str


def ensure_thumbnail(clip_path: Path) -> Path | None:
    """Generate a 320x180 JPG thumbnail for an MP4 clip using FFmpeg."""
    clips_dir = clip_path.parent
    thumb_dir = clips_dir / ".thumbnails"
    thumb_dir.mkdir(exist_ok=True)
    thumb_path = thumb_dir / f"{clip_path.stem}.jpg"

    if thumb_path.exists():
        return thumb_path

    try:
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = 0x08000000  # CREATE_NO_WINDOW

        # Extract frame at 1-second mark and scale to 320px width
        subprocess.run([
            FFMPEG_CMD,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(clip_path),
            "-ss", "1",
            "-vframes", "1",
            "-vf", "scale=320:-1",
            str(thumb_path)
        ], check=True, creationflags=creation_flags, timeout=8)
        return thumb_path
    except Exception as e:
        log.warning("Failed to generate thumbnail for %s: %s", clip_path.name, e)
        return None


@app.get("/api/clips")
async def api_list_clips():
    """List all clips (.mp4) in the clips directory."""
    clips_dir = get_clips_dir()
    if not clips_dir:
        return []

    clips = []
    # Search for mp4 files
    for f in clips_dir.glob("*.mp4"):
        try:
            stat = f.stat()
            clips.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "size_bytes": stat.st_size,
                "created_at": stat.st_mtime,
            })
        except OSError:
            pass

    # Sort by modification time, newest first
    clips.sort(key=lambda x: x["created_at"], reverse=True)
    return clips


@app.get("/api/clips/file")
async def api_get_clip_file(filename: str):
    """Serve a specific clip file for HTML5 video playback."""
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not configured.")

    # Avoid path traversal
    safe_path = (clips_dir / filename).resolve()
    if not safe_path.exists() or not safe_path.is_file() or clips_dir.resolve() not in safe_path.parents:
        raise HTTPException(status_code=404, detail="Clip file not found or access denied.")

    return FileResponse(
        str(safe_path),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"}
    )


@app.get("/api/clips/thumbnail")
async def api_get_clip_thumbnail(filename: str):
    """Serve (and generate if needed) the thumbnail JPG image for a clip."""
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not configured.")

    clip_path = clips_dir / filename
    thumb_path = clips_dir / ".thumbnails" / f"{clip_path.stem}.jpg"

    if not thumb_path.exists():
        # Generate on the fly
        if clip_path.exists() and clips_dir.resolve() in clip_path.resolve().parents:
            await asyncio.to_thread(ensure_thumbnail, clip_path)

    if thumb_path.exists() and clips_dir.resolve() in thumb_path.resolve().parents:
        return FileResponse(str(thumb_path), media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="Thumbnail not found.")


@app.post("/api/clips/open-folder")
async def api_open_clip_folder(req: ClipActionRequest):
    """Open the clips folder in Windows Explorer and select the file."""
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not configured.")

    safe_path = (clips_dir / req.filename).resolve()
    if not safe_path.exists() or clips_dir.resolve() not in safe_path.parents:
        raise HTTPException(status_code=404, detail="Clip file not found or access denied.")

    try:
        if sys.platform == "win32":
            creation_flags = 0x08000000  # CREATE_NO_WINDOW
            subprocess.Popen(
                f'explorer.exe /select,"{str(safe_path)}"',
                creationflags=creation_flags
            )
        else:
            # Fallback for other OS
            import webbrowser
            webbrowser.open(str(safe_path.parent))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")


@app.delete("/api/clips/delete")
async def api_delete_clip(filename: str):
    """Delete a clip file and its thumbnail from disk."""
    clips_dir = get_clips_dir()
    if not clips_dir:
        raise HTTPException(status_code=400, detail="Clips directory is not configured.")

    safe_path = (clips_dir / filename).resolve()
    if not safe_path.exists() or clips_dir.resolve() not in safe_path.parents:
        raise HTTPException(status_code=404, detail="Clip file not found or access denied.")

    try:
        # Delete clip file
        safe_path.unlink()

        # Try to delete associated thumbnail
        thumb_path = clips_dir / ".thumbnails" / f"{safe_path.stem}.jpg"
        try:
            if thumb_path.exists():
                thumb_path.unlink()
        except Exception:
            pass

        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")


# ---------------------------------------------------------------------------
# Lifecycle Events
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    """Start the buffer cleanup loop on server boot."""
    log.info("Clipper worker starting — buffer: %s, clips: %s", BUFFER_DIR, get_clips_dir())
    state._cleanup_task = asyncio.create_task(_cleanup_loop())


@app.on_event("shutdown")
async def on_shutdown():
    """Gracefully stop everything on server shutdown."""
    log.info("Shutting down...")
    await _stop_capture()
    if state._cleanup_task:
        state._cleanup_task.cancel()
        try:
            await state._cleanup_task
        except asyncio.CancelledError:
            pass
    log.info("Shutdown complete.")
