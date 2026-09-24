#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = os.getenv("WHISPER_URL", "https://whisper.inovacaosistemas.com.br")
DEFAULT_MODEL = os.getenv("WHISPER_MODEL", "Systran/faster-whisper-medium")


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def probe_get(session: requests.Session, url: str, out: Path, name: str) -> dict:
    started = time.perf_counter()
    try:
        response = session.get(url, timeout=(10, 30))
        elapsed = round(time.perf_counter() - started, 3)
        write_text(out / f"{name}.body.txt", response.text[:20000])
        write_text(out / f"{name}.headers.json", json.dumps(dict(response.headers), indent=2))
        result = {"ok": response.ok, "status": response.status_code, "seconds": elapsed}
    except Exception as exc:
        result = {"ok": False, "error": repr(exc), "seconds": round(time.perf_counter() - started, 3)}
    write_text(out / f"{name}.result.json", json.dumps(result, indent=2) + "\n")
    return result


def make_test_audio(source: Path | None, target: Path) -> None:
    if source is not None:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "0", "-t", "20", "-i", str(source),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "64k", str(target),
        ]
    else:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "2",
            "-c:a", "libmp3lame", "-b:a", "64k", str(target),
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg: " + (result.stderr or result.stdout).strip())


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnostica o Whisper remoto e grava evidências.")
    p.add_argument("audio", nargs="?", type=Path)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--language", default="pt")
    args = p.parse_args()

    if args.audio is not None:
        args.audio = args.audio.expanduser().resolve()
        if not args.audio.is_file():
            print(f"ERRO: arquivo não encontrado: {args.audio}", file=sys.stderr)
            return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "evidence" / "remote-diagnostics" / stamp
    out.mkdir(parents=True, exist_ok=False)

    parsed = urlparse(args.url)
    host = parsed.hostname or ""
    try:
        dns = sorted({item[4][0] for item in socket.getaddrinfo(host, None)})
    except Exception as exc:
        dns = [f"ERRO: {exc!r}"]
    write_text(out / "dns.json", json.dumps({"host": host, "addresses": dns}, indent=2) + "\n")

    session = requests.Session()
    root_result = probe_get(session, args.url.rstrip("/") + "/", out, "root")
    models_result = probe_get(session, args.url.rstrip("/") + "/v1/models", out, "models")

    test_audio = out / "test-audio.mp3"
    try:
        make_test_audio(args.audio, test_audio)
    except Exception as exc:
        write_text(out / "report.md", f"# Diagnóstico Whisper remoto\n\nFalha ao criar áudio de teste: {exc}\n")
        print(f"Evidências: {out}")
        return 2

    endpoint = args.url.rstrip("/") + "/v1/audio/transcriptions"
    started = time.perf_counter()
    try:
        with test_audio.open("rb") as handle:
            response = session.post(
                endpoint,
                files={"file": ("test-audio.mp3", handle, "audio/mpeg")},
                data={"model": args.model, "language": args.language},
                timeout=(10, 180),
            )
        elapsed = round(time.perf_counter() - started, 3)
        write_text(out / "transcription.body.txt", response.text[:50000])
        write_text(out / "transcription.headers.json", json.dumps(dict(response.headers), indent=2))
        transcription_result = {
            "ok": response.ok,
            "status": response.status_code,
            "seconds": elapsed,
            "upload_bytes": test_audio.stat().st_size,
        }
    except Exception as exc:
        transcription_result = {
            "ok": False,
            "error": repr(exc),
            "seconds": round(time.perf_counter() - started, 3),
            "upload_bytes": test_audio.stat().st_size,
        }

    write_text(
        out / "transcription.result.json",
        json.dumps(transcription_result, ensure_ascii=False, indent=2) + "\n",
    )

    report = [
        "# Diagnóstico Whisper remoto",
        "",
        f"- Data UTC: {stamp}",
        f"- URL: {args.url}",
        f"- Modelo: {args.model}",
        f"- DNS: {', '.join(dns)}",
        f"- GET /: {root_result}",
        f"- GET /v1/models: {models_result}",
        f"- POST /v1/audio/transcriptions: {transcription_result}",
        "",
        "## Resposta da transcrição",
        "",
        (out / "transcription.body.txt").read_text(encoding="utf-8", errors="replace")
        if (out / "transcription.body.txt").exists() else "(sem corpo)",
        "",
    ]
    write_text(out / "report.md", "\n".join(report))

    print(f"Evidências: {out}")
    print(f"Transcrição: {transcription_result}")
    return 0 if transcription_result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
