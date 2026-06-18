"""One-shot faster-whisper transcription used by the Claude channel plugin.

The plugin shells to this script with a single audio file path. We print the
transcript on stdout and exit 0 on success. Any error (missing dependency,
unreadable file, decode failure) goes to stderr and exits non-zero so the
plugin can fall back to the "audio not transcribed" reply.

Reads the same AGENT_WHISPER_* env vars the gateway uses, so a single
install-time choice configures both backends.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--warm":
        return _warm()
    if len(argv) != 2:
        print(f"usage: {argv[0]} <audio-path>  |  {argv[0]} --warm", file=sys.stderr)
        return 2

    audio_path = Path(argv[1])
    if not audio_path.is_file():
        print(f"audio file not found: {audio_path}", file=sys.stderr)
        return 2

    model, language, beam_size = _load_model()
    if isinstance(model, int):
        return model

    try:
        segments, _info = model.transcribe(
            str(audio_path), language=language, beam_size=beam_size
        )
        text = " ".join(s.text.strip() for s in segments if s.text and s.text.strip())
    except Exception as exc:
        print(f"transcription failed: {exc}", file=sys.stderr)
        return 5

    text = text.strip()
    if not text:
        print("transcript is empty (no speech detected)", file=sys.stderr)
        return 6

    sys.stdout.write(text)
    return 0


def _load_model():
    """Returns (model, language, beam_size) or (exit_code, _, _) on failure."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "faster-whisper is not installed. Re-run "
            "`whatsapp-agent install --reconfigure` and enable voice transcription.",
            file=sys.stderr,
        )
        return 3, None, None

    model_name = os.getenv("AGENT_WHISPER_MODEL", "base")
    device = os.getenv("AGENT_WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("AGENT_WHISPER_COMPUTE_TYPE", "int8")
    language = os.getenv("AGENT_WHISPER_LANGUAGE", "").strip() or None
    try:
        beam_size = int(os.getenv("AGENT_WHISPER_BEAM_SIZE", "5"))
    except ValueError:
        beam_size = 5

    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:
        print(f"failed to load whisper model={model_name}: {exc}", file=sys.stderr)
        return 4, None, None
    return model, language, beam_size


def _warm() -> int:
    """Download (if needed) and load the configured model, then exit.

    Used to front-load the one-time model download + CTranslate2 init so the
    first real transcription doesn't pay that latency. Codex's gateway also
    benefits because the loaded model survives in the long-lived process.
    """
    result = _load_model()
    if isinstance(result[0], int):
        return result[0]
    print("warm: ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
