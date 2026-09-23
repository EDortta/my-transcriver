#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = os.getenv("WHISPER_SSH_TARGET", "esteban@whisper.inovacaosistemas.com.br")

COMMANDS = [
    ("identity", "date -u --iso-8601=seconds; hostname -f 2>/dev/null || hostname; uname -a"),
    ("resources", "free -h; df -h /"),
    ("listeners", "ss -lntp 2>&1 | grep -E '(:8093|whisper|python|uvicorn|docker-proxy)' || true"),
    ("http-root", "curl -sS -i --connect-timeout 3 --max-time 10 http://127.0.0.1:8093/ 2>&1 || true"),
    ("http-models", "curl -sS -i --connect-timeout 3 --max-time 10 http://127.0.0.1:8093/v1/models 2>&1 || true"),
    ("processes", "ps auxww | grep -Ei 'whisper|speaches|uvicorn|8093' | grep -v grep || true"),
    ("docker-ps", "docker ps --no-trunc 2>&1 || true"),
    ("docker-all", "docker ps -a --no-trunc 2>&1 | grep -Ei 'whisper|speaches|8093' || true"),
    ("docker-logs", "for c in $(docker ps -aq 2>/dev/null); do n=$(docker inspect -f '{{.Name}} {{.Config.Image}}' $c 2>/dev/null); echo $n | grep -Eqi 'whisper|speaches' || continue; echo === $c $n ===; docker logs --tail 200 $c 2>&1 || true; done"),
    ("systemd", "systemctl --no-pager --all --type=service 2>&1 | grep -Ei 'whisper|speaches' || true"),
    ("journal", "sudo -n journalctl --no-pager -n 300 2>&1 | grep -Ei 'whisper|speaches|8093|oom|killed process' || journalctl --user --no-pager -n 300 2>&1 | grep -Ei 'whisper|speaches|8093|oom|killed process' || true"),
    ("nginx-vhost", "sudo -n cat /etc/nginx/sites-enabled/006-whisper.conf 2>&1 || cat /etc/nginx/sites-enabled/006-whisper.conf 2>&1 || true"),
    ("nginx-errors", "sudo -n tail -n 400 /var/log/nginx/error.log 2>&1 | grep -Ei 'whisper|8093|upstream|connect.. failed' || tail -n 400 /var/log/nginx/error.log 2>&1 | grep -Ei 'whisper|8093|upstream|connect.. failed' || true"),
]


def run_remote(target: str, command: str, timeout: int) -> tuple[int, str, str]:
    remote_command = "bash -lc " + shlex.quote(command)
    p = subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            target,
            remote_command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostica por SSH o upstream Whisper na porta 8093."
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "evidence" / "whisper-upstream" / stamp
    out.mkdir(parents=True, exist_ok=False)

    report_lines = [
        "# Diagnóstico upstream Whisper",
        "",
        f"Data UTC: {stamp}",
        f"Alvo SSH: {args.target}",
        "",
    ]

    ssh_failed = False

    for name, command in COMMANDS:
        try:
            rc, stdout, stderr = run_remote(args.target, command, args.timeout)
        except subprocess.TimeoutExpired as exc:
            rc = 124
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "") + "\nTIMEOUT"
        if rc == 255:
            ssh_failed = True

        text = (
            f"===== {name} =====\n"
            f"exit_code={rc}\n"
            f"--- stdout ---\n{stdout}"
            f"--- stderr ---\n{stderr}\n"
        )
        (out / f"{name}.txt").write_text(text, encoding="utf-8")
        report_lines.append(f"- {name}: exit {rc}")

        if ssh_failed:
            break

    (out / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Evidências: {out}")
    if ssh_failed:
        print("ERRO: não foi possível conectar por SSH ao alvo.", file=sys.stderr)
        return 1

    print("Diagnóstico concluído.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
