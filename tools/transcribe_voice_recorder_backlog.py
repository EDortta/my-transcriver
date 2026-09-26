#!/usr/bin/env python3
"""Transcribe the Android Voice Recorder backlog idempotently.

The script is designed to survive the nightly reboot on devel3: every completed
file is recorded in durable state, the current file is rendered in a work
directory first, and the final transcript is promoted only after success.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIBE = ROOT / "transcribe.py"
DEFAULT_SOURCE = Path("/home/esteban/Sync/Backups/Android/VoiceRecorder")
DEFAULT_OUTPUT = ROOT / "protegendo-a-torre-brutos"
DEFAULT_EXISTING = ROOT / "protegendo-a-torre-1transcribe-existentes"
DEFAULT_STATE = DEFAULT_OUTPUT / ".voice-recorder-backlog-state.json"
DEFAULT_LOCK = DEFAULT_OUTPUT / ".voice-recorder-backlog.lock"
DEFAULT_WORK = DEFAULT_OUTPUT / ".voice-recorder-backlog-work"

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


@dataclass(frozen=True)
class Candidate:
    source: Path
    sha256: str
    size: int
    mtime_ns: int
    target: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcreve áudios do Android Voice Recorder que ainda não aparecem "
            "em protegendo-a-torre-brutos nem em protegendo-a-torre-1transcribe-existentes."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--existing-dir", type=Path, default=DEFAULT_EXISTING)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--backend", choices=("local", "remote"), default="local")
    parser.add_argument("--model", default="medium")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--server-url", default="https://whisper.inovacaosistemas.com.br")
    parser.add_argument("--remote-chunk-seconds", type=int, default=30)
    parser.add_argument("--remote-retries", type=int, default=1)
    parser.add_argument("--max-files", type=int, default=0, help="0 significa sem limite.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": 1, "completed": {}, "failed": {}, "running": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        shutil.copy2(path, backup)
        return {"schema": 1, "completed": {}, "failed": {}, "running": None}
    if not isinstance(data, dict):
        return {"schema": 1, "completed": {}, "failed": {}, "running": None}
    data.setdefault("schema", 1)
    data.setdefault("completed", {})
    data.setdefault("failed", {})
    data.setdefault("running", None)
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transcript_key(path: Path) -> str:
    stem = path.stem
    marker = "--transcript_"
    if marker in stem:
        stem = stem.split(marker, 1)[0]
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


def discover_sources(source_dir: Path) -> list[Path]:
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


def discover_transcript_keys(*directories: Path) -> set[str]:
    keys: set[str] = set()
    for directory in directories:
        directory = directory.expanduser().resolve()
        if not directory.exists():
            continue
        for item in directory.rglob("*"):
            if item.is_file() and item.suffix.lower() in TRANSCRIPT_EXTENSIONS:
                keys.add(transcript_key(item))
    return keys


def build_candidates(args: argparse.Namespace, state: dict[str, Any]) -> list[Candidate]:
    output_dir = args.output_dir.expanduser().resolve()
    existing_dir = args.existing_dir.expanduser().resolve()
    transcript_keys = discover_transcript_keys(output_dir, existing_dir)
    completed = state.get("completed", {})
    candidates: list[Candidate] = []

    for source in discover_sources(args.source):
        key = transcript_key(source)
        if key in transcript_keys:
            continue

        digest = sha256(source)
        if digest in completed:
            completed_output = Path(str(completed[digest].get("output", ""))).expanduser()
            if completed_output.is_file():
                continue

        stat = source.stat()
        target = output_dir / f"{source.stem}.md"
        candidates.append(
            Candidate(
                source=source,
                sha256=digest,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                target=target,
            )
        )

    return sorted(candidates, key=lambda item: (item.size, str(item.source).casefold()))


def command_for(candidate: Candidate, work_dir: Path, args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(TRANSCRIBE),
        str(candidate.source),
        "--backend",
        args.backend,
        "--model",
        args.model,
        "--language",
        args.language,
        "--output-dir",
        str(work_dir),
        "--overwrite",
    ]
    if args.backend == "local":
        cmd.extend(["--device", args.device, "--compute-type", args.compute_type])
        if args.threads > 0:
            cmd.extend(["--threads", str(args.threads)])
    else:
        cmd.extend(
            [
                "--server-url",
                args.server_url,
                "--timeout",
                str(args.timeout),
                "--remote-chunk-seconds",
                str(args.remote_chunk_seconds),
                "--remote-retries",
                str(args.remote_retries),
            ]
        )
    return cmd


def promote_transcript(candidate: Candidate, work_dir: Path) -> Path:
    produced = work_dir / f"{candidate.source.stem}.md"
    if not produced.is_file():
        matches = sorted(work_dir.glob("*.md"))
        if len(matches) == 1:
            produced = matches[0]
        else:
            raise RuntimeError(f"transcribe.py não gerou saída esperada em {work_dir}")

    candidate.target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = candidate.target.with_name(candidate.target.name + ".tmp")
    shutil.copy2(produced, tmp_target)
    os.replace(tmp_target, candidate.target)
    return candidate.target


def transcribe_candidate(candidate: Candidate, args: argparse.Namespace) -> tuple[Path, float]:
    run_work_dir = (
        args.work_dir.expanduser().resolve()
        / f"{int(time.time())}-{candidate.sha256[:12]}"
    )
    if run_work_dir.exists():
        shutil.rmtree(run_work_dir)
    run_work_dir.mkdir(parents=True)

    start = time.monotonic()
    cmd = command_for(candidate, run_work_dir, args)
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    elapsed = time.monotonic() - start
    if args.verbose or proc.returncode != 0:
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"transcribe.py retornou {proc.returncode}")

    target = promote_transcript(candidate, run_work_dir)
    shutil.rmtree(run_work_dir, ignore_errors=True)
    return target, elapsed


def acquire_lock(lock_file: Path):
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit(f"Já existe outra execução ativa: {lock_file}") from exc
    handle.write(f"pid={os.getpid()} started={utc_now()}\n")
    handle.flush()
    return handle


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not TRANSCRIBE.is_file():
        raise SystemExit(f"transcribe.py não encontrado: {TRANSCRIBE}")

    lock_handle = acquire_lock(args.lock_file.expanduser().resolve())

    state_path = args.state.expanduser().resolve()
    state = load_state(state_path)
    candidates = build_candidates(args, state)
    if args.max_files > 0:
        candidates = candidates[: args.max_files]

    log(f"Origem: {args.source.expanduser().resolve()}")
    log(f"Destino: {args.output_dir.expanduser().resolve()}")
    log(f"Já existentes: {args.existing_dir.expanduser().resolve()}")
    log(f"Pendentes: {len(candidates)}")
    if args.verbose:
        log(f"Lock ativo: {lock_handle.name}")

    if args.dry_run:
        for candidate in candidates:
            print(f"PENDENTE {candidate.source} -> {candidate.target}")
        return 0

    failures = 0
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        log(f"[{index}/{total}] Transcrevendo: {candidate.source.name}")
        state["running"] = {
            "sha256": candidate.sha256,
            "input": str(candidate.source),
            "output": str(candidate.target),
            "startedAt": utc_now(),
        }
        save_state(state_path, state)

        try:
            target, elapsed = transcribe_candidate(candidate, args)
        except KeyboardInterrupt:
            log("Interrompido pelo operador.")
            save_state(state_path, state)
            return 130
        except Exception as exc:
            failures += 1
            state["failed"][candidate.sha256] = {
                "input": str(candidate.source),
                "output": str(candidate.target),
                "size": candidate.size,
                "mtimeNs": candidate.mtime_ns,
                "failedAt": utc_now(),
                "error": str(exc),
            }
            state["running"] = None
            save_state(state_path, state)
            log(f"[{index}/{total}] ERRO: {candidate.source.name}: {exc}")
            continue

        state["completed"][candidate.sha256] = {
            "input": str(candidate.source),
            "output": str(target),
            "size": candidate.size,
            "mtimeNs": candidate.mtime_ns,
            "completedAt": utc_now(),
            "elapsedSeconds": round(elapsed, 3),
        }
        state.get("failed", {}).pop(candidate.sha256, None)
        state["running"] = None
        save_state(state_path, state)
        log(f"[{index}/{total}] Gravado: {target} ({elapsed / 60:.1f} min)")

    succeeded = total - failures
    log(f"Concluído: {succeeded} sucesso(s), {failures} falha(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
