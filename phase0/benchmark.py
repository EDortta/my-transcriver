#!/usr/bin/env python3
"""Phase 0 benchmark: reproducible transcription baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIBE = ROOT / "transcribe.py"
DEFAULT_SOURCE = Path("/home/esteban/Sync/Backups/Android/VoiceRecorder")
DEFAULT_EVIDENCE = ROOT / "evidence" / "phase0"
EXTENSIONS = {".3gp",".aac",".aiff",".avi",".flac",".m4a",".m4v",".mkv",".mov",
              ".mp3",".mp4",".mpeg",".mpg",".oga",".ogg",".opus",".wav",".webm",".wma"}

def args():
    p = argparse.ArgumentParser(description="Fase 0: benchmark de transcrição.")
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--file", type=Path, help="Arquivo específico para smoke/comparação.")
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--seed", type=int, default=20260923)
    p.add_argument("--engines", default="remote,local")
    p.add_argument("--language", default="pt")
    p.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    p.add_argument("--run-id")
    p.add_argument("--local-model", default="medium")
    p.add_argument("--remote-chunk-seconds", type=int, default=60)
    p.add_argument("--allow-model-download", action="store_true")
    p.add_argument("--max-file-mb", type=float, default=0)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def duration(path: Path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        return round(float(r.stdout.strip()), 3) if r.returncode == 0 else None
    except ValueError:
        return None

def discover(source: Path, max_mb: float):
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"diretório não encontrado: {source}")
    limit = int(max_mb * 1024 * 1024) if max_mb > 0 else 0
    files = []
    for f in source.rglob("*"):
        if f.is_file() and f.suffix.lower() in EXTENSIONS:
            if not limit or f.stat().st_size <= limit:
                files.append(f.resolve())
    return sorted(files, key=lambda x: str(x).lower())

def select(files, count, seed):
    if count < 1:
        raise RuntimeError("--samples precisa ser >= 1")
    rng = random.Random(seed)
    return rng.sample(files, min(count, len(files)))

def parse_engines(raw):
    allowed = {"remote", "local"}
    engines = []
    for name in raw.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name not in allowed:
            raise RuntimeError(f"motor desconhecido: {name}")
        if name not in engines:
            engines.append(name)
    if not engines:
        raise RuntimeError("nenhum motor selecionado")
    return engines

def inventory():
    return {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "gnu_time": "/usr/bin/time" if Path("/usr/bin/time").exists() else None,
        "whisper_cpp": shutil.which("whisper-cli") or shutil.which("whisper.cpp"),
        "faster_whisper_python": importlib.util.find_spec("faster_whisper") is not None,
    }

def time_metrics(path: Path):
    out = {}
    if not path.exists():
        return out
    fields = {
        "User time (seconds)": ("user_seconds", float),
        "System time (seconds)": ("system_seconds", float),
        "Maximum resident set size (kbytes)": ("max_rss_kb", int),
        "Percent of CPU this job got": ("cpu_percent", str),
    }
    for line in path.read_text(errors="replace").splitlines():
        s = line.strip()
        for label, (key, cast) in fields.items():
            if s.startswith(label + ":"):
                value = s[len(label)+1:].strip()
                try:
                    out[key] = cast(value)
                except ValueError:
                    pass
    return out

def run_engine(engine, sample_id, source, run_dir, a):
    work = run_dir / "runs" / sample_id / engine
    output = work / "output"
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    if engine == "local" and importlib.util.find_spec("faster_whisper") is None:
        return {"sample": sample_id, "engine": engine, "status": "skipped",
                "note": "faster-whisper não instalado"}

    cmd = [sys.executable, str(TRANSCRIBE), str(source), "--backend", engine,
           "--language", a.language, "--overwrite", "-o", str(output)]
    if engine == "local":
        cmd += ["--model", a.local_model]
    elif engine == "remote":
        cmd += ["--remote-chunk-seconds", str(a.remote_chunk_seconds)]

    env = dict(os.environ)
    if engine == "local" and not a.allow_model_download:
        env["HF_HUB_OFFLINE"] = "1"

    metric_file = work / "time.txt"
    timed = cmd
    if Path("/usr/bin/time").exists():
        timed = ["/usr/bin/time", "-v", "-o", str(metric_file), *cmd]

    base = {"sample": sample_id, "engine": engine, "command": cmd}
    if a.dry_run:
        return {**base, "status": "dry-run"}

    started = time.perf_counter()
    r = subprocess.run(timed, capture_output=True, text=True, env=env, check=False)
    wall = round(time.perf_counter() - started, 3)
    (work / "stdout.txt").write_text(r.stdout or "", encoding="utf-8")
    (work / "stderr.txt").write_text(r.stderr or "", encoding="utf-8")

    md = output / f"{source.stem}.md"
    status = "ok" if r.returncode == 0 and md.exists() else "failed"
    result = {**base, "status": status, "returncode": r.returncode,
              "wall_seconds": wall, **time_metrics(metric_file)}
    if md.exists():
        result["output"] = str(md.relative_to(run_dir))
        result["output_chars"] = len(md.read_text(encoding="utf-8", errors="replace"))
    if status == "failed":
        result["note"] = "ver stderr.txt"
    return result

def write_reports(run_dir, a, samples, results, inv):
    data = {
        "schema_version": 1,
        "phase": 0,
        "scope": "transcription-baseline",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(a.source.expanduser()),
        "seed": a.seed,
        "remote_chunk_seconds": a.remote_chunk_seconds,
        "local_model": a.local_model,
        "inventory": inv,
        "samples": samples,
        "results": results,
    }
    (run_dir / "results.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Fase 0 — Benchmark de transcrição", "",
        f"- Rodada: `{run_dir.name}`",
        f"- Seed: `{a.seed}`",
        f"- Fonte: `{a.source.expanduser()}`",
        f"- Modelo local: `{a.local_model}`",
        f"- Chunk remoto: `{a.remote_chunk_seconds}s`", "",
        "## Ambiente", "",
    ]
    for k, v in inv.items():
        lines.append(f"- {k}: `{v}`")
    lines += ["", "## Amostras", "",
              "| ID | Arquivo | SHA-256 | Bytes | Duração (s) |",
              "|---|---|---|---:|---:|"]
    for s in samples:
        lines.append(f"| {s['id']} | {Path(s['path']).name} | `{s['sha256'][:16]}…` | "
                     f"{s['size_bytes']} | {s['duration_seconds'] or 'n/d'} |")
    lines += ["", "## Execuções", "",
              "| Amostra | Motor | Estado | Parede (s) | CPU user | CPU sys | RSS máx. KiB | Saída/nota |",
              "|---|---|---|---:|---:|---:|---:|---|"]
    for r in results:
        lines.append(
            f"| {r['sample']} | {r['engine']} | {r['status']} | "
            f"{r.get('wall_seconds','n/d')} | {r.get('user_seconds','n/d')} | "
            f"{r.get('system_seconds','n/d')} | {r.get('max_rss_kb','n/d')} | "
            f"{r.get('output', r.get('note','—'))} |")
    lines += ["", "## Observação", "",
              "Esta primeira rodada mede execução e produz transcrições lado a lado. "
              "Qualidade linguística será revisada manualmente porque ainda não existe "
              "uma transcrição de referência confiável. Diarização e identificação de "
              "voz entram nas próximas iterações da Fase 0.", ""]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    review = ["# Fase 0 — Revisão humana", "",
              "Escala sugerida: 1 = ruim, 5 = excelente.", ""]
    engines = parse_engines(a.engines)
    for s in samples:
        review += [f"## {s['id']} — {Path(s['path']).name}", "",
                   "| Motor | Fidelidade | Português | Omissões/alucinações | Observações |",
                   "|---|---:|---:|---:|---|"]
        review += [f"| {e} |  |  |  |  |" for e in engines]
        review.append("")
    (run_dir / "review-template.md").write_text("\n".join(review), encoding="utf-8")

def main():
    a = args()
    try:
        engines = parse_engines(a.engines)
        if a.file is not None:
            candidate = a.file.expanduser().resolve()
            if not candidate.is_file() or candidate.suffix.lower() not in EXTENSIONS:
                raise RuntimeError(f"arquivo de mídia inválido: {candidate}")
            chosen = [candidate]
        else:
            files = discover(a.source, a.max_file_mb)
            chosen = select(files, a.samples, a.seed)
    except RuntimeError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    if not chosen:
        print("ERRO: nenhum áudio/vídeo encontrado.", file=sys.stderr)
        return 2

    run_id = a.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = a.evidence_dir.expanduser().resolve() / run_id
    if run_dir.exists():
        print(f"ERRO: rodada já existe: {run_dir}", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True)

    inv = inventory()
    samples = []
    for i, path in enumerate(chosen, 1):
        print(f"[amostra {i}/{len(chosen)}] {path}")
        samples.append({"id": f"S{i:02d}", "path": str(path), "sha256": sha256(path),
                        "size_bytes": path.stat().st_size, "duration_seconds": duration(path)})

    results = []
    total = len(samples) * len(engines)
    n = 0
    for sample in samples:
        for engine in engines:
            n += 1
            print(f"[{n}/{total}] {sample['id']} / {engine}")
            r = run_engine(engine, sample["id"], Path(sample["path"]), run_dir, a)
            results.append(r)
            print(f"    {r['status']}" + (f": {r['note']}" if r.get("note") else ""))

    write_reports(run_dir, a, samples, results, inv)
    failed = sum(r["status"] == "failed" for r in results)
    print(f"Evidências: {run_dir}")
    print(f"Falhas: {failed}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
