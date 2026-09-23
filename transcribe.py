#!/usr/bin/env python3
"""Batch audio/video transcription to Markdown.

Remote Whisper is the default backend. Local faster-whisper remains available as an
explicit fallback for offline use.
"""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import requests


VERSION = "0.3.0"
DEFAULT_WHISPER_URL = "https://whisper.inovacaosistemas.com.br"
DEFAULT_REMOTE_MODEL = "Systran/faster-whisper-medium"
DEFAULT_LOCAL_MODEL = "medium"

MEDIA_EXTENSIONS = {
    ".3gp", ".aac", ".aiff", ".avi", ".flac", ".m4a", ".m4v", ".mkv",
    ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".oga", ".ogg", ".opus",
    ".wav", ".webm", ".wma",
}


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    model: str
    backend: str
    duration: float | None = None
    language_probability: float | None = None
    segments: tuple[TranscriptSegment, ...] = ()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcreve lotes de áudio/vídeo para Markdown."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Arquivos e/ou diretórios.")
    parser.add_argument(
        "-o", "--output-dir", type=Path,
        help="Diretório de saída. Por padrão grava o .md ao lado da mídia.",
    )
    parser.add_argument(
        "--backend", choices=("remote", "local"),
        default=os.getenv("TRANSCRIVER_BACKEND", "remote"),
        help="Backend de transcrição (padrão: remote).",
    )
    parser.add_argument(
        "--server-url", default=os.getenv("WHISPER_URL", DEFAULT_WHISPER_URL),
        help=f"Servidor Whisper remoto (padrão: {DEFAULT_WHISPER_URL}).",
    )
    parser.add_argument(
        "--api-key", default=os.getenv("WHISPER_API_KEY"),
        help="Bearer token opcional. Também pode vir de WHISPER_API_KEY.",
    )
    parser.add_argument(
        "--model", default=None,
        help=("Modelo. Remote padrão: Systran/faster-whisper-medium; "
              "local padrão: medium."),
    )
    parser.add_argument(
        "--language", default="auto",
        help="Idioma: pt, en, es etc. 'auto' deixa o Whisper detectar (padrão: auto).",
    )
    parser.add_argument(
        "--timeout", type=float, default=3600,
        help="Timeout por chunk remoto em segundos (padrão: 3600).",
    )
    parser.add_argument(
        "--remote-chunk-seconds",
        type=int,
        default=int(os.getenv("TRANSCRIVER_REMOTE_CHUNK_SECONDS", "300")),
        help="Duração dos chunks enviados ao Whisper remoto (padrão: 300s; 0 desabilita).",
    )
    parser.add_argument(
        "--remote-retries",
        type=int,
        default=int(os.getenv("TRANSCRIVER_REMOTE_RETRIES", "2")),
        help="Tentativas extras para erros transitórios do backend remoto (padrão: 2).",
    )
    parser.add_argument(
        "--timestamps", action="store_true",
        help="Inclui timestamps. Atualmente disponível no backend local.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Sobrescreve .md existentes.")
    parser.add_argument("--no-recursive", action="store_true", help="Não percorre subdiretórios.")

    # Opções exclusivas do backend local.
    parser.add_argument("--device", default="cpu", help="Backend local: cpu ou cuda.")
    parser.add_argument("--compute-type", default="int8", help="Backend local: int8, float16 etc.")
    parser.add_argument("--beam-size", type=int, default=5, help="Backend local: beam size.")
    parser.add_argument("--threads", type=int, default=0, help="Backend local: threads de CPU.")
    parser.add_argument("--no-vad", action="store_true", help="Backend local: desabilita VAD.")
    parser.add_argument("--download-root", type=Path, help="Backend local: cache dos modelos.")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args(argv)


def is_media(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS


def discover_media(inputs: Iterable[Path], recursive: bool) -> tuple[list[Path], list[str]]:
    files: set[Path] = set()
    warnings: list[str] = []

    for raw_path in inputs:
        path = raw_path.expanduser()
        if not path.exists():
            warnings.append(f"Entrada inexistente: {path}")
            continue

        if path.is_file():
            if is_media(path):
                files.add(path.resolve())
            else:
                warnings.append(f"Formato não reconhecido: {path}")
            continue

        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            files.update(item.resolve() for item in iterator if is_media(item))
            continue

        warnings.append(f"Entrada ignorada: {path}")

    return sorted(files, key=lambda item: str(item).lower()), warnings


def build_output_paths(media_files: Sequence[Path], output_dir: Path | None) -> dict[Path, Path]:
    proposed: dict[Path, Path] = {}
    collisions: dict[Path, list[Path]] = {}

    if output_dir is not None:
        output_dir = output_dir.expanduser().resolve()

    for media in media_files:
        base = output_dir if output_dir is not None else media.parent
        target = base / f"{media.stem}.md"
        proposed[media] = target
        collisions.setdefault(target, []).append(media)

    for target, sources in collisions.items():
        if len(sources) < 2:
            continue
        for media in sources:
            digest = hashlib.sha1(str(media).encode("utf-8")).hexdigest()[:8]
            proposed[media] = target.with_name(f"{media.stem}-{digest}.md")

    return proposed


def format_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def text_paragraphs(text: str, target_size: int = 900) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    sentences = re.split(r"(?<=[.!?…])\s+", text)
    result: list[str] = []
    current: list[str] = []
    size = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and size + len(sentence) + 1 > target_size:
            result.append(" ".join(current))
            current = []
            size = 0
        current.append(sentence)
        size += len(sentence) + 1

    if current:
        result.append(" ".join(current))
    return result


def segment_paragraphs(segments: Sequence[TranscriptSegment], target_size: int = 900) -> list[str]:
    if not segments:
        return []

    result: list[str] = []
    current: list[str] = []
    current_size = 0
    previous_end: float | None = None

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue

        long_pause = previous_end is not None and segment.start - previous_end >= 2.0
        size_break = current_size + len(text) >= target_size
        if current and (long_pause or size_break):
            result.append(" ".join(current))
            current = []
            current_size = 0

        current.append(text)
        current_size += len(text) + 1
        previous_end = segment.end

    if current:
        result.append(" ".join(current))
    return result


def render_markdown(source: Path, transcript: Transcript, timestamps: bool) -> str:
    lines = [
        f"# Transcrição — {source.name}",
        "",
        f"- Fonte: `{source.name}`",
        f"- Backend: `{transcript.backend}`",
        f"- Idioma: `{transcript.language}`",
        f"- Modelo: `{transcript.model}`",
    ]

    if transcript.duration is not None:
        lines.append(f"- Duração: `{format_time(transcript.duration)}`")
    if transcript.language_probability is not None:
        lines.append(f"- Confiança do idioma: `{transcript.language_probability:.1%}`")

    lines.extend(["", "## Transcrição", ""])

    if timestamps and transcript.segments:
        for segment in transcript.segments:
            text = segment.text.strip()
            if text:
                lines.extend([
                    f"[{format_time(segment.start)} → {format_time(segment.end)}] {text}",
                    "",
                ])
    else:
        blocks = (
            segment_paragraphs(transcript.segments)
            if transcript.segments
            else text_paragraphs(transcript.text)
        )
        if blocks:
            for block in blocks:
                lines.extend([block, ""])
        else:
            lines.extend(["_Nenhuma fala detectada._", ""])

    return "\n".join(lines).rstrip() + "\n"


def remote_model_name(args: argparse.Namespace) -> str:
    return args.model or DEFAULT_REMOTE_MODEL


def local_model_name(args: argparse.Namespace) -> str:
    return args.model or DEFAULT_LOCAL_MODEL


def _post_remote_file(
    source: Path,
    args: argparse.Namespace,
    *,
    model: str,
    language: str | None,
) -> dict[str, object]:
    url = args.server_url.rstrip("/") + "/v1/audio/transcriptions"
    mimetype = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    data = {"model": model}
    if language:
        data["language"] = language

    headers: dict[str, str] = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    attempts = max(1, int(args.remote_retries) + 1)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with source.open("rb") as handle:
                response = requests.post(
                    url,
                    files={"file": (source.name, handle, mimetype)},
                    data=data,
                    headers=headers,
                    timeout=args.timeout,
                )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 5))
                continue
            raise RuntimeError(f"Falha de rede ao chamar Whisper: {exc}") from exc

        if response.ok:
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("Whisper retornou resposta que não é JSON") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Whisper retornou JSON em formato inesperado")
            return payload

        detail = response.text.strip().replace("\n", " ")[:500]
        if response.status_code >= 500 and attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 5))
            continue
        raise RuntimeError(f"Whisper HTTP {response.status_code}: {detail}")

    raise RuntimeError(f"Whisper remoto falhou: {last_error or 'erro desconhecido'}")


def _remote_chunks(source: Path, chunk_dir: Path, seconds: int) -> list[Path]:
    if seconds <= 0:
        return [source]

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg é necessário para chunking remoto; use --remote-chunk-seconds 0 "
            "para enviar o arquivo inteiro"
        )

    pattern = chunk_dir / "chunk-%05d.mp3"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "96k",
        "-f",
        "segment",
        "-segment_time",
        str(seconds),
        "-reset_timestamps",
        "1",
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-1000:]
        raise RuntimeError(f"ffmpeg falhou ao dividir o áudio: {detail}")

    chunks = sorted(chunk_dir.glob("chunk-*.mp3"))
    if not chunks:
        raise RuntimeError("ffmpeg não gerou chunks de áudio")
    return chunks


def transcribe_remote(source: Path, args: argparse.Namespace) -> Transcript:
    model = remote_model_name(args)
    language = None if args.language.lower() == "auto" else args.language

    if args.remote_chunk_seconds <= 0:
        payload = _post_remote_file(source, args, model=model, language=language)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise RuntimeError("Whisper retornou transcrição vazia")
        return Transcript(
            text=text,
            language=str(payload.get("language") or language or "auto"),
            model=model,
            backend="remote",
        )

    with tempfile.TemporaryDirectory(prefix="my-transcriver-") as tmp:
        chunk_dir = Path(tmp)
        chunks = _remote_chunks(source, chunk_dir, args.remote_chunk_seconds)
        texts: list[str] = []
        detected_language = language or "auto"

        for index, chunk in enumerate(chunks, start=1):
            print(
                f"    [chunk {index}/{len(chunks)}] enviando {chunk.name} "
                f"({chunk.stat().st_size / 1024 / 1024:.1f} MiB)"
            )
            payload = _post_remote_file(chunk, args, model=model, language=language)
            chunk_text = str(payload.get("text") or "").strip()
            if not chunk_text:
                raise RuntimeError(f"Whisper retornou transcrição vazia no chunk {index}")
            texts.append(chunk_text)
            if payload.get("language"):
                detected_language = str(payload["language"])

    return Transcript(
        text="\n\n".join(texts),
        language=detected_language,
        model=model,
        backend="remote",
    )


def transcribe_local(source: Path, args: argparse.Namespace) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Backend local não instalado. Execute: pip install -r requirements-local.txt"
        ) from exc

    model_name = local_model_name(args)
    kwargs: dict[str, object] = {
        "device": args.device,
        "compute_type": args.compute_type,
    }
    if args.threads > 0:
        kwargs["cpu_threads"] = args.threads
    if args.download_root is not None:
        kwargs["download_root"] = str(args.download_root.expanduser())

    model = WhisperModel(model_name, **kwargs)
    language = None if args.language.lower() == "auto" else args.language
    generated_segments, info = model.transcribe(
        str(source),
        language=language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
    )
    segments = tuple(
        TranscriptSegment(float(segment.start), float(segment.end), str(segment.text))
        for segment in generated_segments
    )
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    probability = getattr(info, "language_probability", None)

    return Transcript(
        text=text,
        language=str(getattr(info, "language", None) or language or "auto"),
        model=model_name,
        backend="local",
        duration=float(getattr(info, "duration", 0.0) or 0.0),
        language_probability=float(probability) if probability is not None else None,
        segments=segments,
    )


def transcribe_one(source: Path, target: Path, args: argparse.Namespace) -> None:
    if args.backend == "remote":
        transcript = transcribe_remote(source, args)
    else:
        transcript = transcribe_local(source, args)

    if args.timestamps and args.backend == "remote":
        print(
            "AVISO: --timestamps ainda não é suportado pelo backend remoto; "
            "gerando texto sem timestamps.",
            file=sys.stderr,
        )

    markdown = render_markdown(source, transcript, timestamps=args.timestamps)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    media_files, warnings = discover_media(args.inputs, recursive=not args.no_recursive)

    for warning in warnings:
        print(f"AVISO: {warning}", file=sys.stderr)

    if not media_files:
        print("Nenhum arquivo de áudio/vídeo encontrado.", file=sys.stderr)
        return 2

    output_paths = build_output_paths(media_files, args.output_dir)
    pending: list[tuple[Path, Path]] = []
    for source in media_files:
        target = output_paths[source]
        if target.exists() and not args.overwrite:
            print(f"PULANDO: {source} -> {target} já existe")
        else:
            pending.append((source, target))

    if not pending:
        print("Nada para transcrever.")
        return 0

    if args.backend == "remote":
        print(f"Whisper remoto: {args.server_url} ({remote_model_name(args)})")
    else:
        print(f"Whisper local: {local_model_name(args)} em {args.device} ({args.compute_type})")

    failures = 0
    total = len(pending)
    for index, (source, target) in enumerate(pending, start=1):
        print(f"[{index}/{total}] Transcrevendo: {source}")
        try:
            transcribe_one(source, target, args)
            print(f"[{index}/{total}] Gravado: {target}")
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.", file=sys.stderr)
            return 130
        except Exception as exc:
            failures += 1
            print(f"[{index}/{total}] ERRO em {source}: {exc}", file=sys.stderr)

    succeeded = total - failures
    print(f"Concluído: {succeeded} sucesso(s), {failures} falha(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
