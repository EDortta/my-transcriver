#!/usr/bin/env python3
"""Batch audio/video transcription to Markdown using faster-whisper."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from faster_whisper import WhisperModel


VERSION = "0.1.0"

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


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcreve arquivos de áudio/vídeo para Markdown localmente."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Arquivos e/ou diretórios a transcrever.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Diretório de saída. Por padrão, grava o .md ao lado da mídia.",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="Modelo Whisper (padrão: small). Ex.: tiny, base, small, medium, large-v3.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Código do idioma, ex.: pt, en, es. Por padrão, detecta automaticamente.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Dispositivo do CTranslate2 (padrão: cpu). Ex.: cpu, cuda.",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="Precisão (padrão: int8). Em GPU, normalmente float16.",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=5,
        help="Beam size da decodificação (padrão: 5).",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Threads de CPU. Zero deixa a biblioteca decidir (padrão: 0).",
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Inclui timestamps em cada segmento do Markdown.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescreve arquivos Markdown existentes.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Não percorre subdiretórios.",
    )
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="Desabilita o filtro de detecção de voz/silêncio.",
    )
    parser.add_argument(
        "--download-root",
        type=Path,
        help="Diretório opcional para armazenar os modelos baixados.",
    )
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


def paragraphs(segments: Sequence[TranscriptSegment], target_size: int = 900) -> list[str]:
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
        sentence_break = current_size >= 350 and current and current[-1].endswith((".", "!", "?", "…"))
        size_break = current_size + len(text) >= target_size

        if current and (long_pause or sentence_break or size_break):
            result.append(" ".join(current))
            current = []
            current_size = 0

        current.append(text)
        current_size += len(text) + 1
        previous_end = segment.end

    if current:
        result.append(" ".join(current))

    return result


def render_markdown(
    source: Path,
    segments: Sequence[TranscriptSegment],
    *,
    language: str,
    language_probability: float | None,
    duration: float,
    model_name: str,
    timestamps: bool,
) -> str:
    lines = [
        f"# Transcrição — {source.name}",
        "",
        f"- Fonte: `{source.name}`",
        f"- Idioma: `{language}`",
        f"- Duração: `{format_time(duration)}`",
        f"- Modelo: `{model_name}`",
    ]

    if language_probability is not None:
        lines.append(f"- Confiança do idioma: `{language_probability:.1%}`")

    lines.extend(["", "## Transcrição", ""])

    if timestamps:
        for segment in segments:
            text = segment.text.strip()
            if text:
                lines.append(
                    f"[{format_time(segment.start)} → {format_time(segment.end)}] {text}"
                )
                lines.append("")
    else:
        blocks = paragraphs(segments)
        if blocks:
            for block in blocks:
                lines.extend([block, ""])
        else:
            lines.extend(["_Nenhuma fala detectada._", ""])

    return "\n".join(lines).rstrip() + "\n"


def create_model(args: argparse.Namespace) -> WhisperModel:
    kwargs: dict[str, object] = {
        "device": args.device,
        "compute_type": args.compute_type,
    }

    if args.threads > 0:
        kwargs["cpu_threads"] = args.threads

    if args.download_root is not None:
        kwargs["download_root"] = str(args.download_root.expanduser())

    return WhisperModel(args.model, **kwargs)


def transcribe_one(
    model: WhisperModel,
    source: Path,
    target: Path,
    args: argparse.Namespace,
) -> None:
    language = args.language
    if language and language.lower() == "auto":
        language = None

    generated_segments, info = model.transcribe(
        str(source),
        language=language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
    )

    segments = [
        TranscriptSegment(segment.start, segment.end, segment.text)
        for segment in generated_segments
    ]

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    detected_language = str(getattr(info, "language", None) or language or "desconhecido")
    probability = getattr(info, "language_probability", None)
    if probability is not None:
        probability = float(probability)

    markdown = render_markdown(
        source,
        segments,
        language=detected_language,
        language_probability=probability,
        duration=duration,
        model_name=args.model,
        timestamps=args.timestamps,
    )

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

    print(
        f"Carregando modelo {args.model!r} em {args.device} "
        f"({args.compute_type})..."
    )

    try:
        model = create_model(args)
    except Exception as exc:
        print(f"ERRO ao carregar o modelo: {exc}", file=sys.stderr)
        return 3

    failures = 0
    total = len(pending)

    for index, (source, target) in enumerate(pending, start=1):
        print(f"[{index}/{total}] Transcrevendo: {source}")
        try:
            transcribe_one(model, source, target, args)
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
