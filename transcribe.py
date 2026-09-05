"""Prepis posnetka z ločevanjem govorcev.

Ena datoteka, en klic. Zvok se pred pošiljanjem pretvori v mono mp3, prepis pa
se nato skrči: časovni žigi odpadejo, oznaka govorca se izpiše le ob menjavi.
Govorci ostanejo `Speaker 1`, `Speaker 2` in tako naprej, poimenuje jih
uporabnik.
"""

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

# ── helpers ────────────────────────────────────────────────────────────────

def _reencode_for_retry(source_path: Path) -> Path:
    """Rebuild the audio from scratch after the API rejected the first upload.

    The point is to discard whatever the API objected to in the container and
    hand it a plain, freshly written stream. This used to produce an
    uncompressed wav, which is larger than any compressed source: past roughly
    thirteen minutes it exceeded the 25 MB upload limit by construction, so the
    retry could never succeed on the recordings this system accepts. It now
    writes mp3, which fixes a broken container just as well and stays small.
    """
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
    """Re-encode to mono 16 kHz mp3 before upload, always.

    Speech recognition reads mono 16 kHz; everything above that is bandwidth the
    model discards. Downloaded audio is stereo at roughly 128 kbit/s, which
    crosses the API's 25 MB limit after about 25 minutes and used to force the
    recording to be split. At 32 kbit/s the same limit is reached at about 96
    minutes, well past the longest recording the system accepts, so the file
    size can no longer decide how the recording is transcribed.
    """
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

    # Re-encoding is a saving, not a requirement: an accepted recording is short
    # enough that the downloaded track usually fits the limit on its own. If
    # ffmpeg is missing or fails, send the original rather than lose the run.
    if source_path.stat().st_size <= max_size:
        logger.warning("   Re-encoding failed, uploading the original file instead")
        return source_path

    raise ValueError(
        f"Audio is {source_path.stat().st_size / 1024 / 1024:.1f}MB and could not be "
        "re-encoded (max 25MB). Trim the audio range before analysing."
    )


MAX_SPEAKER_LABEL = 32


def _compact_transcript(text: str) -> str:
    """Name the speaker only where the speaker CHANGES.

    Consecutive segments from one speaker repeat the same label for no gain:
    the reader — and the model — carry it over, exactly as a screenplay does.
    Measured across stored transcripts, the label alone accounted for between a
    sixth and a half of the file. Removing the repetition costs no speech and
    no timestamp, and it is the difference between a recording being analysable
    in one piece and being refused.
    """
    out, prev = [], None
    for line in text.split("\n"):
        m = re.match(r"^([^:]{1,%d}): (.*)$" % MAX_SPEAKER_LABEL, line)
        if not m:
            out.append(line)
            prev = None          # unparsable line breaks the run, stay explicit
            continue
        speaker, said = m.groups()
        out.append(f"{speaker}: {said}" if speaker != prev else said)
        prev = speaker
    return "\n".join(out)


# ── output writers ──────────────────────────────────────────────────────────

def _write_output(transcript, output_path: Path) -> None:
    """Write the transcript as "Speaker: text" lines.

    No timestamps. Nothing downstream reads them: no analysis prompt refers to
    time, the order of the exchange comes from the order of the lines, and the
    fallacy evidence is a part of the argument rather than a quotation located
    in the recording. They cost roughly a fifth of the transcript — twelve
    characters against a median segment of twenty-three — and that fifth is
    subtracted from what the analysis is able to read in one piece.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        segments = getattr(transcript, "segments", None) or []
        if segments:
            # Same normalisation as the chunked path, so both produce labels in
            # one shape without ever merging two voices into one.
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


# ── main entry point ────────────────────────────────────────────────────────

def transcribe_audio(
    audio_path: str = "data/audio.m4a",
    output_path: str = "data/transcript.txt",
) -> Path:
    """Transcribe audio with diarization.

    Speakers keep the neutral labels the transcription returns, `Speaker 1`,
    `Speaker 2` and so on. Naming them is left to the user, who can type the
    names in before the analysis or rename a speaker afterwards. The automatic
    guess from the transcript was the least reliable step in the pipeline: it
    read the whole thing off self-introductions, and on a recording without any
    it returned a different role description on every run.
    """
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

    # One recording, one transcription call. The accepted length is capped in
    # `pipeline.max_recording_minutes` so that the re-encoded audio always fits
    # under the API's per-call limit, which is why there is no chunked path:
    # splitting the audio meant transcribing each piece with its own speaker
    # labels and then asking a model to decide which of them were the same
    # person, and that decision was the largest single source of speaker errors.
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
        # Whatever the API objected to, say it out loud: this message is the
        # only evidence of why the upload was refused, and it used to be
        # swallowed by the retry.
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

    # Log how much audio was transcribed (estimated from the response)
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

    # Collapse repeated speaker labels LAST: every step above parses lines of
    # the form "[ts] Speaker: text", so compaction must not run before them.
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
