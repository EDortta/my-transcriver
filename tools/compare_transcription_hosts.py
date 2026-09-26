#!/usr/bin/env python3
"""Compare hosts for audio transcription workloads.

This is an orchestrator meant to be run from devel3.  It selects a medium-sized
audio file, copies it to each remote host, runs read-only inventory probes plus a
small audio/CPU benchmark, and writes durable evidence under the project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import requests


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIBE = ROOT / "transcribe.py"
DEFAULT_SOURCE = Path("/home/esteban/Sync/Backups/Android/VoiceRecorder")
DEFAULT_EVIDENCE = ROOT / "evidence" / "host-capacity"
DEFAULT_REMOTE_DIR = "/tmp/my-transcriver-host-benchmark"
DEFAULT_REMOTE_MODEL = "Systran/faster-whisper-medium"
DEFAULT_WHISPER_URL = "https://whisper.inovacaosistemas.com.br"
MEDIA_EXTENSIONS = {
    ".3gp", ".aac", ".aiff", ".avi", ".flac", ".m4a", ".m4v", ".mkv", ".mov",
    ".mp3", ".mp4", ".mpeg", ".mpg", ".oga", ".ogg", ".opus", ".wav", ".webm",
    ".wma",
}


@dataclass(frozen=True)
class Host:
    name: str
    target: str

    @property
    def is_local(self) -> bool:
        return self.target == "local"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compara devel3 e dom1 para carga de transcrição."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--audio", type=Path, help="Arquivo específico para o teste.")
    parser.add_argument(
        "--hosts",
        default="devel3=local,dom1=ssh-whisper.inovacaosistemas.com.br",
        help="Lista nome=alvo separada por vírgulas. Use alvo 'local' para esta máquina.",
    )
    parser.add_argument(
        "--transcription-targets",
        default=f"devel3-local=local:medium,whisper-incus={DEFAULT_WHISPER_URL}",
        help=(
            "Candidatos reais de transcrição separados por vírgula. "
            "Use nome=local[:modelo] ou nome=https://servidor."
        ),
    )
    parser.add_argument("--language", default="pt")
    parser.add_argument("--remote-model", default=DEFAULT_REMOTE_MODEL)
    parser.add_argument("--transcribe-timeout", type=float, default=1200)
    parser.add_argument("--skip-hosts", action="store_true")
    parser.add_argument("--skip-transcription", action="store_true")
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--run-id")
    parser.add_argument("--ssh-timeout", type=int, default=20)
    parser.add_argument("--cpu-seconds", type=float, default=8.0)
    parser.add_argument("--keep-remote", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Mostra seleção sem SSH/cópia.")
    return parser.parse_args(argv)


def parse_hosts(raw: str) -> list[Host]:
    hosts: list[Host] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SystemExit(f"Host inválido: {item}. Use nome=alvo.")
        name, target = item.split("=", 1)
        hosts.append(Host(name.strip(), target.strip()))
    if not hosts:
        raise SystemExit("Nenhum host informado.")
    return hosts


@dataclass(frozen=True)
class TranscriptionTarget:
    name: str
    kind: str
    value: str


def parse_transcription_targets(raw: str) -> list[TranscriptionTarget]:
    targets: list[TranscriptionTarget] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SystemExit(f"Candidato inválido: {item}. Use nome=local[:modelo] ou nome=https://...")
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value.startswith(("http://", "https://")):
            targets.append(TranscriptionTarget(name, "remote", value.rstrip("/")))
        elif value == "local" or value.startswith("local:"):
            model = value.split(":", 1)[1] if ":" in value else "medium"
            targets.append(TranscriptionTarget(name, "local", model))
        else:
            raise SystemExit(f"Candidato desconhecido: {item}")
    return targets


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_audio(source: Path) -> list[Path]:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"Diretório não encontrado: {source}")
    return sorted(
        (
            item.resolve()
            for item in source.rglob("*")
            if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
        ),
        key=lambda p: (p.stat().st_size, str(p).lower()),
    )


def choose_medium_audio(source: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        audio = explicit.expanduser().resolve()
        if not audio.is_file() or audio.suffix.lower() not in MEDIA_EXTENSIONS:
            raise SystemExit(f"Arquivo de áudio inválido: {audio}")
        return audio

    files = discover_audio(source)
    if not files:
        raise SystemExit(f"Nenhum áudio/vídeo encontrado em {source.expanduser()}")

    # Prefer a practical "medium" range; otherwise fall back to the size median.
    lower = 1 * 1024 * 1024
    upper = 50 * 1024 * 1024
    practical = [p for p in files if lower <= p.stat().st_size <= upper]
    pool = practical or files
    return pool[len(pool) // 2]


def run_cmd(
    cmd: Sequence[str],
    *,
    input_text: str | None = None,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def media_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    proc = run_cmd(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        timeout=30,
    )
    try:
        return float(proc.stdout.strip()) if proc.returncode == 0 else None
    except ValueError:
        return None


REMOTE_BENCH = r'''
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

audio = Path(os.environ["BENCH_AUDIO"])
cpu_seconds = float(os.environ.get("BENCH_CPU_SECONDS", "8"))

def read_first(path):
    try:
        return Path(path).read_text(errors="replace").splitlines()[0].strip()
    except Exception:
        return None

def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return read_first("/proc/cpuinfo")

def read_meminfo():
    result = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, value = line.split(":", 1)
                result[key] = int(value.strip().split()[0])
    except Exception:
        pass
    return result

def timed(cmd):
    started = time.perf_counter()
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }

def ffprobe_duration(path):
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        text=True, capture_output=True, check=False,
    )
    try:
        return round(float(proc.stdout.strip()), 3) if proc.returncode == 0 else None
    except ValueError:
        return None

def cpu_hash_bench(seconds):
    payload = b"my-transcriver-capacity" * 4096
    deadline = time.perf_counter() + seconds
    loops = 0
    digest = b""
    while time.perf_counter() < deadline:
        digest = hashlib.pbkdf2_hmac("sha256", payload, digest or b"seed", 1500)
        loops += 1
    elapsed = max(0.001, seconds)
    return {
        "loops": loops,
        "loops_per_second": round(loops / elapsed, 2),
        "seconds": seconds,
        "digest_prefix": digest.hex()[:12],
    }

def disk_free(path):
    usage = shutil.disk_usage(path)
    return {"total": usage.total, "used": usage.used, "free": usage.free}

ffmpeg = shutil.which("ffmpeg")
duration = ffprobe_duration(audio)
decode = None
if ffmpeg:
    decode = timed([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-i", str(audio), "-f", "null", "-"])
    if duration and decode["returncode"] == 0 and decode["wall_seconds"] > 0:
        decode["audio_seconds_per_wall_second"] = round(duration / decode["wall_seconds"], 2)

result = {
    "host": socket.gethostname(),
    "fqdn": socket.getfqdn(),
    "platform": platform.platform(),
    "python": sys.version.split()[0],
    "cpu_count": os.cpu_count(),
    "cpu_model": cpu_model(),
    "meminfo_kb": read_meminfo(),
    "disk_free": {
        "cwd": disk_free(Path.cwd()),
        "audio_parent": disk_free(audio.parent),
    },
    "tools": {
        "ffmpeg": ffmpeg,
        "ffprobe": shutil.which("ffprobe"),
        "whisper_cli": shutil.which("whisper-cli") or shutil.which("whisper.cpp"),
        "faster_whisper_python": importlib.util.find_spec("faster_whisper") is not None,
        "vosk_python": importlib.util.find_spec("vosk") is not None,
    },
    "audio": {
        "path": str(audio),
        "size_bytes": audio.stat().st_size,
        "duration_seconds": duration,
    },
    "benchmarks": {
        "cpu_hash": cpu_hash_bench(cpu_seconds),
        "ffmpeg_decode": decode,
    },
}

print(json.dumps(result, ensure_ascii=False, indent=2))
'''


def remote_quote(value: str) -> str:
    return shlex.quote(value)


def run_local(host: Host, audio: Path, cpu_seconds: float) -> dict:
    env = dict(os.environ)
    env["BENCH_AUDIO"] = str(audio)
    env["BENCH_CPU_SECONDS"] = str(cpu_seconds)
    proc = subprocess.run(
        [sys.executable, "-c", REMOTE_BENCH],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return parse_host_result(host, proc)


def run_remote(host: Host, audio: Path, remote_dir: str, cpu_seconds: float, ssh_timeout: int, keep: bool) -> dict:
    remote_audio = f"{remote_dir.rstrip('/')}/sample{audio.suffix.lower()}"
    mkdir = ["ssh", "-o", f"ConnectTimeout={ssh_timeout}", host.target, "mkdir", "-p", remote_dir]
    mk = run_cmd(mkdir, timeout=ssh_timeout + 10)
    if mk.returncode != 0:
        return failure(host, "mkdir", mk)

    cp = run_cmd(
        ["scp", "-q", "-o", f"ConnectTimeout={ssh_timeout}", str(audio), f"{host.target}:{remote_quote(remote_audio)}"],
        timeout=max(60, ssh_timeout + 60),
    )
    if cp.returncode != 0:
        return failure(host, "scp", cp)

    remote_cmd = (
        f"BENCH_AUDIO={remote_quote(remote_audio)} "
        f"BENCH_CPU_SECONDS={remote_quote(str(cpu_seconds))} "
        "python3 -"
    )
    proc = run_cmd(
        ["ssh", "-o", f"ConnectTimeout={ssh_timeout}", host.target, remote_cmd],
        input_text=REMOTE_BENCH,
        timeout=max(120, int(cpu_seconds) + ssh_timeout + 90),
    )
    result = parse_host_result(host, proc)

    if not keep:
        run_cmd(
            ["ssh", "-o", f"ConnectTimeout={ssh_timeout}", host.target,
             f"rm -f -- {remote_quote(remote_audio)}"],
            timeout=ssh_timeout + 10,
        )
    return result


def failure(host: Host, stage: str, proc: subprocess.CompletedProcess[str]) -> dict:
    return {
        "name": host.name,
        "target": host.target,
        "status": "failed",
        "stage": stage,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def parse_host_result(host: Host, proc: subprocess.CompletedProcess[str]) -> dict:
    if proc.returncode != 0:
        return failure(host, "benchmark", proc)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "name": host.name,
            "target": host.target,
            "status": "failed",
            "stage": "parse-json",
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-1000:],
        }
    payload["name"] = host.name
    payload["target"] = host.target
    payload["status"] = "ok"
    return payload


def transcription_metrics(audio: Path, wall: float) -> dict:
    duration = media_duration(audio)
    data = {
        "audio_duration_seconds": round(duration, 3) if duration else None,
        "wall_seconds": round(wall, 3),
    }
    if duration and wall > 0:
        data["realtime_factor"] = round(wall / duration, 4)
        data["audio_seconds_per_wall_second"] = round(duration / wall, 4)
        data["projected_60min_minutes"] = round(60 * wall / duration, 2)
    return data


def run_local_transcription(target: TranscriptionTarget, audio: Path, run_dir: Path, args: argparse.Namespace) -> dict:
    work = run_dir / "transcription" / target.name
    output = work / "output"
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(TRANSCRIBE), str(audio),
        "--backend", "local",
        "--language", args.language,
        "--overwrite",
        "-o", str(output),
        "--model", target.value,
    ]
    env = dict(os.environ)
    env.setdefault("HF_HUB_OFFLINE", "1")
    started = time.perf_counter()
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env, check=False)
    wall = time.perf_counter() - started
    (work / "stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (work / "stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    md = output / f"{audio.stem}.md"
    result = {
        "name": target.name,
        "kind": target.kind,
        "model": target.value,
        "status": "ok" if proc.returncode == 0 and md.exists() else "failed",
        "returncode": proc.returncode,
        "command": cmd,
        **transcription_metrics(audio, wall),
    }
    if md.exists():
        result["output"] = str(md.relative_to(run_dir))
        result["output_chars"] = len(md.read_text(encoding="utf-8", errors="replace"))
    if result["status"] != "ok":
        result["note"] = "ver stderr.txt"
    return result


def run_remote_transcription(target: TranscriptionTarget, audio: Path, run_dir: Path, args: argparse.Namespace) -> dict:
    work = run_dir / "transcription" / target.name
    work.mkdir(parents=True, exist_ok=True)
    url = f"{target.value}/v1/audio/transcriptions"
    started = time.perf_counter()
    response_text = ""
    status_code = None
    error = None
    text = ""
    try:
        with audio.open("rb") as handle:
            files = {
                "file": (
                    audio.name,
                    handle,
                    mimetypes.guess_type(audio.name)[0] or "application/octet-stream",
                )
            }
            data = {"model": args.remote_model, "language": args.language}
            response = requests.post(url, files=files, data=data, timeout=args.transcribe_timeout)
        status_code = response.status_code
        response_text = response.text
        (work / "response-body.txt").write_text(response_text, encoding="utf-8", errors="replace")
        if response.ok:
            payload = response.json()
            text = payload.get("text", "") if isinstance(payload, dict) else ""
            (work / "transcript.txt").write_text(text, encoding="utf-8")
        else:
            error = response_text[:1000]
    except Exception as exc:  # noqa: BLE001 - recorded as benchmark evidence.
        error = repr(exc)
        (work / "error.txt").write_text(error, encoding="utf-8")
    wall = time.perf_counter() - started
    status = "ok" if status_code and 200 <= status_code < 300 and text.strip() else "failed"
    result = {
        "name": target.name,
        "kind": target.kind,
        "url": url,
        "model": args.remote_model,
        "status": status,
        "http_status": status_code,
        "error": error,
        "text_chars": len(text),
        **transcription_metrics(audio, wall),
    }
    return result


def run_transcription_target(target: TranscriptionTarget, audio: Path, run_dir: Path, args: argparse.Namespace) -> dict:
    if target.kind == "local":
        return run_local_transcription(target, audio, run_dir, args)
    return run_remote_transcription(target, audio, run_dir, args)


def score(result: dict) -> float:
    if result.get("status") != "ok":
        return 0.0
    cpu = result.get("benchmarks", {}).get("cpu_hash", {}).get("loops_per_second") or 0
    decode = result.get("benchmarks", {}).get("ffmpeg_decode") or {}
    realtime = decode.get("audio_seconds_per_wall_second") or 0
    mem_avail = result.get("meminfo_kb", {}).get("MemAvailable", 0) / 1024 / 1024
    return round(cpu * 0.65 + realtime * 8 + mem_avail * 2, 3)


def transcription_sort_key(result: dict) -> tuple[int, float]:
    if result.get("status") != "ok":
        return (1, float("inf"))
    return (0, float(result.get("projected_60min_minutes") or result.get("wall_seconds") or 999999))


def write_reports(
    run_dir: Path,
    selected_audio: Path,
    host_results: list[dict],
    transcription_results: list[dict],
    args: argparse.Namespace,
) -> None:
    ranked_hosts = sorted(host_results, key=score, reverse=True)
    ranked_transcriptions = sorted(transcription_results, key=transcription_sort_key)
    data = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_audio": {
            "path": str(selected_audio),
            "size_bytes": selected_audio.stat().st_size,
            "sha256": sha256(selected_audio),
        },
        "settings": {
            "cpu_seconds": args.cpu_seconds,
            "remote_dir": args.remote_dir,
            "keep_remote": args.keep_remote,
            "language": args.language,
            "remote_model": args.remote_model,
            "transcribe_timeout": args.transcribe_timeout,
        },
        "host_ranking": [
            {"name": item["name"], "score": score(item), "status": item.get("status")}
            for item in ranked_hosts
        ],
        "transcription_ranking": [
            {
                "name": item["name"],
                "status": item.get("status"),
                "projected_60min_minutes": item.get("projected_60min_minutes"),
            }
            for item in ranked_transcriptions
        ],
        "host_results": host_results,
        "transcription_results": transcription_results,
    }
    (run_dir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Comparação de hosts para transcrição",
        "",
        f"- Rodada: `{run_dir.name}`",
        f"- Áudio: `{selected_audio}`",
        f"- Bytes: `{selected_audio.stat().st_size}`",
        f"- SHA-256: `{sha256(selected_audio)}`",
        "",
        "## Candidatos de transcrição",
        "",
    ]
    if ranked_transcriptions:
        lines += [
            "| Posição | Candidato | Tipo | Estado | Parede (s) | Fator tempo real | Projeção 60min (min) | Saída/erro |",
            "|---:|---|---|---|---:|---:|---:|---|",
        ]
        for index, item in enumerate(ranked_transcriptions, 1):
            note = item.get("output") or item.get("error") or item.get("note") or ""
            if note and len(str(note)) > 120:
                note = str(note)[:117] + "..."
            lines.append(
                f"| {index} | {item.get('name')} | {item.get('kind')} | {item.get('status')} | "
                f"{item.get('wall_seconds', 'n/d')} | {item.get('realtime_factor', 'n/d')} | "
                f"{item.get('projected_60min_minutes', 'n/d')} | {note} |"
            )
    else:
        lines.append("Transcrição não executada nesta rodada.")

    lines += [
        "",
        "## Capacidade bruta dos hosts",
        "",
        "| Posição | Host | Estado | Score | CPU hash/s | Decode x tempo real | CPU cores | Mem disponível GiB | Observação |",
        "|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, item in enumerate(ranked_hosts, 1):
        bench = item.get("benchmarks", {})
        decode = bench.get("ffmpeg_decode") or {}
        mem_gib = (item.get("meminfo_kb", {}).get("MemAvailable", 0) / 1024 / 1024)
        note = item.get("stage") or ""
        lines.append(
            f"| {index} | {item.get('name')} | {item.get('status')} | {score(item)} | "
            f"{bench.get('cpu_hash', {}).get('loops_per_second', 'n/d')} | "
            f"{decode.get('audio_seconds_per_wall_second', 'n/d')} | "
            f"{item.get('cpu_count', 'n/d')} | {mem_gib:.2f} | {note} |"
        )

    lines += ["", "## Ferramentas detectadas", ""]
    for item in ranked_hosts:
        tools = item.get("tools") or {}
        lines.append(
            f"- `{item.get('name')}`: ffmpeg=`{tools.get('ffmpeg')}`, "
            f"faster_whisper=`{tools.get('faster_whisper_python')}`, "
            f"vosk=`{tools.get('vosk_python')}`, whisper_cli=`{tools.get('whisper_cli')}`"
        )

    lines += [
        "",
        "## Interpretação",
        "",
        "A seção de transcrição mede o fluxo real que interessa para lote de áudio. "
        "A seção de hosts mede apenas capacidade bruta, útil para escolher onde instalar "
        "ou mover serviços, mas não substitui o benchmark real de `/v1/audio/transcriptions`.",
        "",
    ]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    hosts = parse_hosts(args.hosts)
    transcription_targets = parse_transcription_targets(args.transcription_targets)
    audio = choose_medium_audio(args.source, args.audio)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.evidence_dir.expanduser().resolve() / run_id

    print(f"Áudio selecionado: {audio} ({audio.stat().st_size} bytes)")
    print("Hosts:", ", ".join(f"{h.name}={h.target}" for h in hosts))
    print("Transcrição:", ", ".join(f"{t.name}={t.kind}:{t.value}" for t in transcription_targets))
    if args.dry_run:
        print("Dry-run: nenhum SSH, SCP ou benchmark executado.")
        return 0

    if run_dir.exists():
        print(f"ERRO: rodada já existe: {run_dir}", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True)

    host_results: list[dict] = []
    if not args.skip_hosts:
        for host in hosts:
            print(f"[host:{host.name}] iniciando")
            started = time.perf_counter()
            if host.is_local:
                result = run_local(host, audio, args.cpu_seconds)
            else:
                result = run_remote(host, audio, args.remote_dir, args.cpu_seconds, args.ssh_timeout, args.keep_remote)
            result["orchestrator_wall_seconds"] = round(time.perf_counter() - started, 3)
            host_results.append(result)
            print(f"[host:{host.name}] {result.get('status')} score={score(result)}")

    transcription_results: list[dict] = []
    if not args.skip_transcription:
        for target in transcription_targets:
            print(f"[transcrição:{target.name}] iniciando")
            result = run_transcription_target(target, audio, run_dir, args)
            transcription_results.append(result)
            print(
                f"[transcrição:{target.name}] {result.get('status')} "
                f"projeção60={result.get('projected_60min_minutes', 'n/d')}"
            )

    write_reports(run_dir, audio, host_results, transcription_results, args)
    print(f"Evidências: {run_dir}")
    failures = [item for item in [*host_results, *transcription_results] if item.get("status") != "ok"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
