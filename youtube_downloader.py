"""
YouTube audio downloader.
"""

import importlib.util
import json as _json
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def is_valid_youtube_url(url: str) -> bool:
    youtube_regex = r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$"
    return bool(re.match(youtube_regex, url))


def get_youtube_metadata(url: str, timeout: int = 25) -> dict:
    """Probe a YouTube URL for metadata WITHOUT downloading the media.

    Returns a dict with keys: duration (seconds, int), title, uploader,
    thumbnail, is_live (bool). Empty/zero values indicate the field
    couldn't be retrieved.

    Raises ValueError for invalid URL, RuntimeError for network/yt-dlp errors.
    """
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    if importlib.util.find_spec("yt_dlp") is None:
        raise RuntimeError("yt-dlp is not installed. Install with: pip install yt-dlp")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--skip-download",
        "--no-warnings",
        "--dump-single-json",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp metadata probe timed out")

    if result.returncode != 0:
        raise RuntimeError(_friendly_download_error(result.stderr or "", result.stdout or ""))

    try:
        data = _json.loads(result.stdout)
    except _json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse yt-dlp metadata JSON: {e}")

    return {
        "duration":   int(data.get("duration") or 0),
        "title":      data.get("title") or "",
        "uploader":   data.get("uploader") or data.get("channel") or "",
        "thumbnail":  data.get("thumbnail") or "",
        "is_live":    bool(data.get("is_live") or data.get("was_live") or False),
        "video_id":   data.get("id") or "",
    }


def _ensure_workspace_path(path: Path) -> None:
    workspace = Path.cwd().resolve()
    if not path.is_relative_to(workspace):
        raise ValueError("Invalid output path - security violation")


def _cleanup_existing_outputs(output_path: Path) -> None:
    for candidate in output_path.parent.glob(f"{output_path.stem}.*"):
        if candidate.is_file():
            logger.info("Removing existing audio artifact: %s", candidate)
            candidate.unlink()


def _build_yt_dlp_command(url: str, output_template: Path) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--force-overwrites",
        "-f",
        "bestaudio/best",
        "--extract-audio",
        "--audio-format",
        "m4a",
        "--audio-quality",
        "0",
        "-o",
        str(output_template),
    ]
    cmd.append(url)
    return cmd


def _extract_error_line(stderr: str, stdout: str) -> str:
    combined = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part).strip()
    if not combined:
        return "Unknown yt-dlp error."

    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line.removeprefix("ERROR:").strip()
    return lines[-1]


def _friendly_download_error(stderr: str, stdout: str) -> str:
    combined = "\n".join(part for part in (stderr, stdout) if part)
    lowered = combined.lower()
    detail = _extract_error_line(stderr, stdout)

    if any(token in lowered for token in (
        "failed to establish a new connection",
        "unable to download api page",
        "temporarily failure in name resolution",
        "temporary failure in name resolution",
        "name or service not known",
        "network is unreachable",
        "winerror 10013",
    )):
        return (
            "yt-dlp could not reach YouTube. Check outbound HTTPS access, "
            "firewall, VPN, or proxy settings."
        )

    if "this video is unavailable" in lowered or "video unavailable" in lowered:
        return "YouTube reports that this video is unavailable."

    if "private video" in lowered:
        return "This video is private and cannot be downloaded without authentication."

    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "YouTube zahteva prijavo (bot detection). Namesto YouTube linka naložite video datoteko ročno."

    if "sign in to confirm your age" in lowered or "age-restricted" in lowered:
        return "This video is age-restricted. yt-dlp needs authenticated cookies to access it."

    if "requested format is not available" in lowered:
        return "No compatible audio format was available for this video."

    if "no module named yt_dlp" in lowered:
        return "yt-dlp is not installed in the active Python environment."

    if "403" in lowered and "forbidden" in lowered:
        # Deliberately points at the NIGHTLY channel, not at `pip install -U`.
        # YouTube changes URL signing more often than yt-dlp cuts a stable
        # release, so the newest stable can be weeks old and still broken —
        # which is exactly the case this message was first written for.
        return (
            "YouTube je zavrnil prenos (HTTP 403). Najpogostejši vzrok je "
            "zastarel yt-dlp: YouTube občasno spremeni podpisovanje naslovov "
            "in stara različica sestavi naslov, ki ga strežnik zavrne. "
            "Stabilna izdaja je lahko več tednov stara in že neuporabna, zato "
            "posodobite z nočnega kanala: "
            "pip install -U --pre \"yt-dlp[default]\""
        )

    return f"YouTube download failed: {detail}"


def download_youtube_audio(url: str, output_path: str = "data/audio.m4a") -> Path:
    if not is_valid_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    if importlib.util.find_spec("yt_dlp") is None:
        raise FileNotFoundError(
            "yt-dlp is not installed. Install with: pip install yt-dlp"
        )

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_workspace_path(output_path)
    _cleanup_existing_outputs(output_path)

    logger.info("[1] Downloading audio from YouTube...")
    output_template = output_path.with_suffix(".%(ext)s")
    command = _build_yt_dlp_command(url, output_template)

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.error("yt-dlp stdout:\n%s", (exc.stdout or "").strip())
        logger.error("yt-dlp stderr:\n%s", (exc.stderr or "").strip())
        raise RuntimeError(_friendly_download_error(exc.stderr or "", exc.stdout or "")) from exc

    if not output_path.exists():
        candidates = sorted(
            output_path.parent.glob(f"{output_path.stem}.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError("yt-dlp did not produce an audio file.")
        output_path = candidates[0]

    if output_path.stat().st_size <= 1024:
        raise ValueError(f"Downloaded audio is too small or invalid: {output_path}")

    logger.info(
        "Audio downloaded: %s (%.1f MB)",
        output_path,
        output_path.stat().st_size / 1024 / 1024,
    )
    return output_path
