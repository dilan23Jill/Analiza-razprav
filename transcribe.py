"""Prepis posnetka z ločevanjem govorcev."""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

from config_loader import get as cfg

logger = logging.getLogger(__name__)

# helpers

def _reencode_for_retry(source_path: Path) -> Path:
    """Rebuild the audio from scratch after the API rejected the first upload."""
    out_path = source_path.with_name(f"{source_path.stem}.retry.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source_path), "-vn",
         "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k",
         str(out_path)],
        check=True, capture_output=True, text=True,
    )
    if not out_path.exists() or out_path.stat().st_size <= 1024:
        raise ValueError(f"ffmpeg produced an invalid file: {out_path}")
    return out_path


def _prepare_audio_for_openai(source_path: Path, max_size: int) -> Path:
    """Re-encode to mono 16 kHz mp3 before upload, always."""
    logger.info(
        "   Re-encoding for upload (source %.1fMB)...",
        source_path.stat().st_size / 1024 / 1024,
    )

    valid_candidates: list[Path] = []
    for bitrate in ("32k", "24k"):
        candidate = source_path.with_name(f"{source_path.stem}.openai-{bitrate}.mp3")
        if candidate.exists():
            candidate.unlink()

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(source_path), "-vn",
                    "-ac", "1", "-ar", "16000",
                    "-c:a", "libmp3lame", "-b:a", bitrate,
                    str(candidate),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logger.warning("   ffmpeg is not on PATH, skipping re-encode")
            break
        if result.returncode != 0:
            logger.warning("   ffmpeg re-encode failed at %s: %s", bitrate, result.stderr[:300])
            continue

        if not candidate.exists() or candidate.stat().st_size <= 1024:
            logger.warning("   ffmpeg produced an invalid compressed file at %s", bitrate)
            continue

        valid_candidates.append(candidate)
        logger.info(
            "   Prepared OpenAI upload at %s: %.1fMB",
            bitrate,
            candidate.stat().st_size / 1024 / 1024,
        )
        if candidate.stat().st_size <= max_size:
            return candidate

    if valid_candidates:
        smallest = min(valid_candidates, key=lambda p: p.stat().st_size)
        raise ValueError(
            f"Audio too large after compression: {smallest.stat().st_size / 1024 / 1024:.1f}MB "
            "(max 25MB). Trim the audio range before analysing."
        )

    if source_path.stat().st_size <= max_size:
        logger.warning("   Re-encoding failed, uploading the original file instead")
        return source_path

    raise ValueError(
        f"Audio is {source_path.stat().st_size / 1024 / 1024:.1f}MB and could not be "
        "re-encoded (max 25MB). Trim the audio range before analysing."
    )


MAX_SPEAKER_LABEL = 32


def _compact_transcript(text: str) -> str:
    """Name the speaker only where the speaker CHANGES."""
    out, prev = [], None
    for line in text.split("\n"):
        m = re.match(r"^([^:]{1,%d}): (.*)$" % MAX_SPEAKER_LABEL, line)
        if not m:
            out.append(line)
            prev = None
            continue
        speaker, said = m.groups()
        out.append(f"{speaker}: {said}" if speaker != prev else said)
        prev = speaker
    return "\n".join(out)


# output writers

def _write_output(transcript, output_path: Path) -> None:
    """Write the transcript as "Speaker: text" lines."""
    with open(output_path, "w", encoding="utf-8") as f:
        segments = getattr(transcript, "segments", None) or []
        if segments:
            seen: Dict[str, str] = {}
            for segment in segments:
                raw = str(getattr(segment, "speaker", "") or "").strip()
                if raw not in seen:
                    seen[raw] = f"Speaker {len(seen) + 1}"
                text = getattr(segment, "text", "").strip()
                if text:
                    f.write(f"{seen[raw]}: {text}\n")
        else:
            f.write(getattr(transcript, "text", ""))


# main entry point

def transcribe_audio(
    audio_path: str = "data/audio.m4a",
    output_path: str = "data/transcript.txt",
) -> Path:
    """Transcribe audio with diarization."""
    load_dotenv()

    audio_path_obj = Path(audio_path).resolve()
    output_path_obj = Path(output_path).resolve()

    if not audio_path_obj.exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path_obj}")

    allowed_formats = {".m4a", ".mp3", ".wav", ".mp4", ".webm", ".mpeg", ".mpga"}
    if audio_path_obj.suffix.lower() not in allowed_formats:
        raise ValueError(f"Unsupported format: {audio_path_obj.suffix}")

    file_size = audio_path_obj.stat().st_size
    if file_size <= 1024:
        raise ValueError(f"Audio file too small or empty: {audio_path_obj}")

    output_path_obj.parent.mkdir(parents=True, exist_ok=True)

    max_size = 25 * 1024 * 1024
    upload_path = _prepare_audio_for_openai(audio_path_obj, max_size)

    logger.info("[2] Running OpenAI transcription and diarization...")
    logger.info("   Uploading %s (%.1f MB)",
                upload_path.name, upload_path.stat().st_size / 1024 / 1024)
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _request(path_to_upload: Path):
        with open(path_to_upload, "rb") as f:
            return client.audio.transcriptions.create(
                model=cfg("transcription.model", "gpt-4o-transcribe-diarize"),
                file=f,
                response_format=cfg("transcription.output_format", "diarized_json"),
                chunking_strategy="auto",
            )

    try:
        transcript = _request(upload_path)
    except BadRequestError as e:
        logger.warning("   Transcription API rejected %s (%.1f MB): %s",
                       upload_path.name,
                       upload_path.stat().st_size / 1024 / 1024,
                       str(e)[:400])
        retry_path = _reencode_for_retry(upload_path)
        logger.info("   Re-encoded and retrying: %s (%.1f MB)",
                    retry_path.name, retry_path.stat().st_size / 1024 / 1024)
        if retry_path.stat().st_size > max_size:
            raise ValueError(
                f"Audio is {retry_path.stat().st_size / 1024 / 1024:.1f} MB even after "
                f"re-encoding (max 25 MB). Trim the range before analysing. "
                f"Original API error: {str(e)[:300]}"
            ) from e
        transcript = _request(retry_path)

    try:
        audio_dur = 0.0
        if hasattr(transcript, "segments") and transcript.segments:
            last_seg = transcript.segments[-1]
            audio_dur = getattr(last_seg, "end", 0) or 0
        elif hasattr(transcript, "duration"):
            audio_dur = transcript.duration or 0
        if audio_dur > 0:
            logger.info("   Transcription: %.0f seconds of audio", audio_dur)
    except Exception:
        pass

    _write_output(transcript, output_path_obj)

    compacted = _compact_transcript(output_path_obj.read_text(encoding="utf-8"))
    before = output_path_obj.stat().st_size
    output_path_obj.write_text(compacted, encoding="utf-8")
    saved = before - len(compacted.encode("utf-8"))
    if saved > 0:
        logger.info("   Transcript compacted: %d chars saved (%.0f %%)",
                    saved, saved / before * 100)

    os.chmod(output_path_obj, 0o600)
    logger.info("Transcript saved: %s", output_path_obj)
    return output_path_obj
