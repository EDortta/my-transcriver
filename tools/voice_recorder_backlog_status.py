#!/usr/bin/env python3
"""Show the current Android Voice Recorder transcription backlog status."""

from __future__ import annotations

import argparse
import json
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/home/esteban/Sync/Backups/Android/VoiceRecorder")
DEFAULT_OUTPUT = ROOT / "protegendo-a-torre-brutos"
DEFAULT_EXISTING = ROOT / "protegendo-a-torre-1transcribe-existentes"
DEFAULT_STATE = DEFAULT_OUTPUT / ".voice-recorder-backlog-state.json"
DEFAULT_LOG = ROOT / "logs" / "voice-recorder-backlog.log"

MEDIA_EXTENSIONS = {
    ".3gp",
    ".aac",
    ".aiff",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}
TRANSCRIPT_EXTENSIONS = {".md", ".txt"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mostra estatística atual do backlog de transcrição do Voice Recorder."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--existing-dir", type=Path, default=DEFAULT_EXISTING)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--json", action="store_true", help="Imprime JSON em vez de texto.")
    return parser.parse_args(argv)


def transcript_key(path: Path) -> str:
    stem = path.stem
    marker = "--transcript_"
    if marker in stem:
        stem = stem.split(marker, 1)[0]
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


def discover_media(source_dir: Path) -> list[Path]:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"Diretório de origem não encontrado: {source_dir}")
    return sorted(
        (
            item.resolve()
            for item in source_dir.rglob("*")
            if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
        ),
        key=lambda item: str(item).casefold(),
    )


def discover_transcripts(*directories: Path) -> dict[str, Path]:
    transcripts: dict[str, Path] = {}
    for directory in directories:
        directory = directory.expanduser().resolve()
        if not directory.exists():
            continue
        for item in directory.rglob("*"):
            if item.is_file() and item.suffix.lower() in TRANSCRIPT_EXTENSIONS:
                transcripts.setdefault(transcript_key(item), item.resolve())
    return transcripts


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed": {}, "failed": {}, "running": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"completed": {}, "failed": {}, "running": None, "stateError": "invalid-json"}
    if not isinstance(data, dict):
        return {"completed": {}, "failed": {}, "running": None, "stateError": "not-object"}
    data.setdefault("completed", {})
    data.setdefault("failed", {})
    data.setdefault("running", None)
    return data


def parse_started_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def process_count() -> int:
    proc = subprocess.run(
        ["pgrep", "-fc", "transcribe_voice_recorder_backlog.py"],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        return int((proc.stdout or "0").strip())
    except ValueError:
        return 0


def last_log_line(path: Path) -> str | None:
    path = path.expanduser().resolve()
    if not path.exists():
        return None
    proc = subprocess.run(
        ["tail", "-n", "1", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    line = proc.stdout.strip()
    return line or None


def build_status(args: argparse.Namespace) -> dict[str, Any]:
    sources = discover_media(args.source)
    transcripts = discover_transcripts(args.output_dir, args.existing_dir)
    state = load_state(args.state.expanduser().resolve())
    running = state.get("running") if isinstance(state.get("running"), dict) else None
    running_key = transcript_key(Path(str(running.get("input")))) if running else None

    missing = [source for source in sources if transcript_key(source) not in transcripts]
    running_is_missing = bool(running_key and any(transcript_key(source) == running_key for source in missing))
    queued = max(0, len(missing) - (1 if running_is_missing else 0))

    started_at = parse_started_at(str(running.get("startedAt"))) if running else None
    running_elapsed = None
    if started_at is not None:
        running_elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceAudioTotal": len(sources),
        "transcriptsRecognized": len(transcripts),
        "completedInState": len(state.get("completed", {})),
        "failedInState": len(state.get("failed", {})),
        "missingTotal": len(missing),
        "running": running,
        "runningElapsedSeconds": round(running_elapsed, 1) if running_elapsed is not None else None,
        "queuedAfterRunning": queued,
        "backlogProcessCount": process_count(),
        "lastLogLine": last_log_line(args.log),
    }


def print_text(status: dict[str, Any]) -> None:
    running = status["running"]
    print(f"Áudios na origem: {status['sourceAudioTotal']}")
    print(f"Transcrições reconhecidas: {status['transcriptsRecognized']}")
    print(f"Concluídos pelo lote atual: {status['completedInState']}")
    print(f"Falhas registradas: {status['failedInState']}")
    print(f"Faltam no total: {status['missingTotal']}")
    if running:
        elapsed = status.get("runningElapsedSeconds")
        elapsed_text = f" há {human_duration(elapsed)}" if isinstance(elapsed, (int, float)) else ""
        print(f"Em transcrição agora: {Path(str(running.get('input'))).name}{elapsed_text}")
        print(f"Fila depois do atual: {status['queuedAfterRunning']}")
    else:
        print("Em transcrição agora: nenhum")
        print(f"Fila: {status['queuedAfterRunning']}")
    print(f"Processos do lote: {status['backlogProcessCount']}")
    if status.get("lastLogLine"):
        print(f"Último log: {status['lastLogLine']}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    status = build_status(args)
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print_text(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
