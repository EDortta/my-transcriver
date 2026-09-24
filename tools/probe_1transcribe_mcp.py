#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = os.getenv("ONE_TRANSCRIBE_MCP_URL", "https://api.1transcribe.com/mcp")
DEFAULT_TOKEN = os.getenv("ONE_TRANSCRIBE_TOKEN")


def dump(path: Path, value: Any) -> None:
    if isinstance(value, (dict, list)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(str(value), encoding="utf-8")


def parse_sse(body: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    data: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line.strip() and data:
            raw = "\n".join(data)
            try:
                items.append(json.loads(raw))
            except Exception:
                items.append({"raw": raw})
            data = []
    if data:
        raw = "\n".join(data)
        try:
            items.append(json.loads(raw))
        except Exception:
            items.append({"raw": raw})
    return items


def decode(resp: requests.Response) -> Any:
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}
    if "text/event-stream" in ctype:
        return {"sse": parse_sse(resp.text), "raw": resp.text}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def rpc_response(decoded: Any, request_id: int) -> dict[str, Any] | None:
    if isinstance(decoded, dict):
        if decoded.get("jsonrpc") == "2.0" and decoded.get("id") == request_id:
            return decoded
        sse = decoded.get("sse")
        if isinstance(sse, list):
            for item in sse:
                if isinstance(item, dict) and item.get("jsonrpc") == "2.0" and item.get("id") == request_id:
                    return item
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspeciona o MCP do 1Transcribe e lista ferramentas.")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--token", default=DEFAULT_TOKEN, help="Bearer token; padrão ONE_TRANSCRIBE_TOKEN.")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "evidence" / "1transcribe-mcp" / stamp
    out.mkdir(parents=True, exist_ok=False)

    session = requests.Session()
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": "my-transcriver/1transcribe-probe",
    }
    if args.token:
        headers["Authorization"] = "Bearer " + args.token

    match = re.match(r"^(https?://[^/]+)", args.url)
    origin = match.group(1) if match else args.url.rstrip("/")

    discovery: dict[str, Any] = {}
    for suffix in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ):
        try:
            h = {k: v for k, v in headers.items() if k != "Content-Type"}
            resp = session.get(origin + suffix, headers=h, timeout=args.timeout)
            discovery[suffix] = {
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": decode(resp),
            }
        except Exception as exc:
            discovery[suffix] = {"error": repr(exc)}
    dump(out / "oauth-discovery.json", discovery)

    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "my-transcriver", "version": "0.1.0"},
        },
    }

    try:
        resp = session.post(args.url, headers=headers, json=init_request, timeout=args.timeout)
    except Exception as exc:
        dump(out / "report.md", "# 1Transcribe MCP probe\n\nFalha de conexão: " + repr(exc) + "\n")
        print("Evidências:", out)
        return 1

    init_body = decode(resp)
    dump(out / "initialize-response.json", {
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body": init_body,
    })

    if resp.status_code in (401, 403):
        auth = resp.headers.get("www-authenticate", "")
        dump(out / "report.md",
             "# 1Transcribe MCP probe\n\n"
             + "- URL: " + args.url + "\n"
             + "- Initialize HTTP: " + str(resp.status_code) + "\n"
             + "- WWW-Authenticate: " + auth + "\n"
             + "- Resultado: autenticação necessária.\n")
        print("Evidências:", out)
        print("Autenticação necessária:", resp.status_code)
        return 2

    if resp.status_code >= 400:
        dump(out / "report.md",
             "# 1Transcribe MCP probe\n\n"
             + "- URL: " + args.url + "\n"
             + "- Initialize HTTP: " + str(resp.status_code) + "\n"
             + "- Resultado: endpoint respondeu com erro.\n")
        print("Evidências:", out)
        return 1

    init_rpc = rpc_response(init_body, 1)
    if not init_rpc or "result" not in init_rpc:
        dump(out / "report.md",
             "# 1Transcribe MCP probe\n\n"
             + "- URL: " + args.url + "\n"
             + "- Initialize HTTP: " + str(resp.status_code) + "\n"
             + "- Resultado: initialize não pôde ser interpretado.\n")
        print("Evidências:", out)
        return 1

    session_id = resp.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    try:
        session.post(
            args.url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=args.timeout,
        )
    except Exception:
        pass

    tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    try:
        tr = session.post(args.url, headers=headers, json=tools_request, timeout=args.timeout)
    except Exception as exc:
        dump(out / "report.md",
             "# 1Transcribe MCP probe\n\nInitialize OK; tools/list falhou: " + repr(exc) + "\n")
        print("Evidências:", out)
        return 1

    tools_body = decode(tr)
    dump(out / "tools-response.json", {
        "status": tr.status_code,
        "headers": dict(tr.headers),
        "body": tools_body,
    })

    trpc = rpc_response(tools_body, 2)
    tools: list[dict[str, Any]] = []
    if trpc and isinstance(trpc.get("result"), dict):
        raw_tools = trpc["result"].get("tools")
        if isinstance(raw_tools, list):
            tools = [x for x in raw_tools if isinstance(x, dict)]
    dump(out / "tools.json", tools)

    names = [str(x.get("name", "")) for x in tools]
    useful = [
        name for name in names
        if any(word in name.lower() for word in ("transcrib", "upload", "file", "audio", "export", "download", "job"))
    ]

    lines = [
        "# 1Transcribe MCP probe",
        "",
        "- URL: " + args.url,
        "- Initialize HTTP: " + str(resp.status_code),
        "- Session ID: " + ("sim" if session_id else "não"),
        "- tools/list HTTP: " + str(tr.status_code),
        "- Ferramentas encontradas: " + str(len(tools)),
        "",
        "## Ferramentas potencialmente úteis",
        "",
    ]
    if useful:
        lines.extend("- " + name for name in useful)
    else:
        lines.append("- Nenhuma identificada pelo nome.")
    lines.extend(["", "Veja tools.json para os schemas completos.", ""])
    dump(out / "report.md", "\n".join(lines))

    print("Evidências:", out)
    print("Ferramentas:", len(tools))
    if useful:
        print("Relevantes:", ", ".join(useful))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
