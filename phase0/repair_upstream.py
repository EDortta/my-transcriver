#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = os.getenv("WHISPER_SSH_TARGET", "esteban@whisper.inovacaosistemas.com.br")
DEFAULT_IMAGE = os.getenv("WHISPER_DOCKER_IMAGE", "ghcr.io/speaches-ai/speaches:latest-cpu")
CONTAINER = "whisper-speaches"
VOLUME = "whisper-hf-cache"
PORT = 8093


def remote(target: str, command: str, timeout: int = 120):
    quoted = "bash -lc " + shlex.quote(command)
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, quoted],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def save(out: Path, name: str, result) -> None:
    (out / (name + ".txt")).write_text(
        "exit_code=" + str(result.returncode) + "\n"
        + "--- stdout ---\n" + (result.stdout or "")
        + "--- stderr ---\n" + (result.stderr or ""),
        encoding="utf-8",
    )


def step(target: str, out: Path, name: str, command: str, timeout: int = 120):
    result = remote(target, command, timeout)
    save(out, name, result)
    return result


def fail(out: Path, message: str) -> int:
    print("Evidências:", out)
    print("ERRO:", message, file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="Restaura o Whisper remoto na porta 8093.")
    p.add_argument("--target", default=DEFAULT_TARGET)
    p.add_argument("--image", default=DEFAULT_IMAGE)
    args = p.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "evidence" / "whisper-repair" / stamp
    out.mkdir(parents=True, exist_ok=False)

    pre = step(
        args.target, out, "01-preflight",
        "hostname -f 2>/dev/null || hostname; "
        "echo PORT; ss -lntp 2>/dev/null | grep ':8093 ' || true; "
        "echo CONTAINER; docker inspect whisper-speaches 2>/dev/null || true",
        60,
    )
    if pre.returncode != 0:
        return fail(out, "preflight falhou")

    occupied = step(
        args.target, out, "02-port-check",
        "if ss -lnt 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:)8093$'; then "
        "docker ps --format '{{.Names}} {{.Ports}}' | grep -F whisper-speaches >/dev/null "
        "|| exit 42; fi",
        30,
    )
    if occupied.returncode == 42:
        return fail(out, "porta 8093 ocupada por outro serviço")
    if occupied.returncode != 0:
        return fail(out, "não foi possível validar a porta 8093")

    pulled = step(
        args.target, out, "03-pull",
        "docker pull " + shlex.quote(args.image),
        1200,
    )
    if pulled.returncode != 0:
        return fail(out, "docker pull falhou")

    volume = step(
        args.target, out, "04-volume",
        "docker volume inspect whisper-hf-cache >/dev/null 2>&1 || "
        "docker volume create whisper-hf-cache",
        60,
    )
    if volume.returncode != 0:
        return fail(out, "não foi possível preparar o cache de modelos")

    normalize = step(
        args.target, out, "05-normalize",
        "if docker inspect whisper-speaches >/dev/null 2>&1; then "
        "img=$(docker inspect -f '{{.Config.Image}}' whisper-speaches); "
        "bind=$(docker inspect -f '{{json .HostConfig.PortBindings}}' whisper-speaches); "
        "if [ \"$img\" != " + shlex.quote(args.image) + " ] || "
        "! printf '%s' \"$bind\" | grep -q '\"HostPort\":\"8093\"'; then "
        "docker rm -f whisper-speaches; fi; fi",
        60,
    )
    if normalize.returncode != 0:
        return fail(out, "falha ao normalizar container existente")

    launch = step(
        args.target, out, "06-launch",
        "if docker inspect whisper-speaches >/dev/null 2>&1; then "
        "docker start whisper-speaches >/dev/null; "
        "else docker run -d --name whisper-speaches --restart unless-stopped "
        "--label com.inovacaosistemas.service=whisper "
        "-p 127.0.0.1:8093:8000 "
        "-v whisper-hf-cache:/home/ubuntu/.cache/huggingface/hub "
        + shlex.quote(args.image) + " >/dev/null; fi; "
        "docker inspect -f '{{.State.Status}} {{.Config.Image}}' whisper-speaches",
        120,
    )
    if launch.returncode != 0:
        return fail(out, "container não iniciou")

    healthy = False
    for n in range(1, 41):
        probe = step(
            args.target, out, "07-health-%02d" % n,
            "curl -fsS --connect-timeout 3 --max-time 10 http://127.0.0.1:8093/health",
            20,
        )
        if probe.returncode == 0:
            healthy = True
            break
        time.sleep(3)

    if not healthy:
        step(
            args.target, out, "08-failed-logs",
            "docker logs --tail 300 whisper-speaches 2>&1; "
            "docker inspect whisper-speaches",
            60,
        )
        return fail(out, "health local não ficou disponível")

    models = step(
        args.target, out, "09-models",
        "curl -sS -i --connect-timeout 3 --max-time 20 "
        "http://127.0.0.1:8093/v1/models",
        30,
    )

    public = subprocess.run(
        ["curl", "-fsS", "--connect-timeout", "5", "--max-time", "20",
         "https://whisper.inovacaosistemas.com.br/health"],
        capture_output=True, text=True, check=False,
    )
    save(out, "10-public-health", public)

    final = step(
        args.target, out, "11-final",
        "ss -lntp 2>/dev/null | grep ':8093 ' || true; "
        "docker ps --filter name=whisper-speaches --no-trunc; "
        "docker logs --tail 100 whisper-speaches 2>&1",
        60,
    )

    report = [
        "# Restauração do Whisper remoto",
        "",
        "Data UTC: " + stamp,
        "Host: " + args.target,
        "Imagem: " + args.image,
        "Container: " + CONTAINER,
        "Bind: 127.0.0.1:8093 -> 8000",
        "Cache persistente: " + VOLUME,
        "Health local: ok",
        "Models local exit: " + str(models.returncode),
        "Health público exit: " + str(public.returncode),
        "",
        "O modelo de STT será carregado sob demanda e persistido no volume Docker.",
        "",
    ]
    (out / "report.md").write_text("\n".join(report), encoding="utf-8")

    print("Evidências:", out)
    print("Whisper upstream restaurado; health local OK.")
    if public.returncode != 0:
        print("AVISO: health público falhou; veja 10-public-health.txt.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
