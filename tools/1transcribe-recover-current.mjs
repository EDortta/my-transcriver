#!/usr/bin/env node

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";

const DEFAULT_OUTPUT = "/home/esteban/Sync/Projects/my-transcriver/protegendo-a-torre-brutos";
const DEFAULT_DOWNLOADS = path.join(os.homedir(), "Downloads");

function textResult(result) {
  return (result?.content || [])
    .filter(x => x && x.type === "text")
    .map(x => x.text || "")
    .join("\n");
}

async function call(client, name, args = {}) {
  const result = await client.callTool({ name, arguments: args });
  if (result?.isError) throw new Error(name + " falhou: " + textResult(result));
  return result;
}

function parsePageId(raw) {
  for (const line of raw.split(/\r?\n/)) {
    if (!/1transcribe\.com/i.test(line)) continue;
    const m = line.match(/\bpageId[:= ]+(\d+)/i) || line.match(/^\s*(\d+)\s*[:\-]/) || line.match(/\[(\d+)\]/);
    if (m) return Number(m[1]);
  }
  return null;
}

async function clickVisibleDomText(client, pageId, labels) {
  const wantedJson = JSON.stringify(labels.map(x => String(x).toLowerCase()));
  const fn = [
    "() => {",
    "  const wanted = " + wantedJson + ";",
    "  const selectors = \"button,a,[role=button],[role=menuitem],[role=option]\";",
    "  const visible = el => {",
    "    const s = getComputedStyle(el);",
    "    const r = el.getBoundingClientRect();",
    "    return s.display !== \"none\" && s.visibility !== \"hidden\" && r.width > 0 && r.height > 0;",
    "  };",
    "  const items = [...document.querySelectorAll(selectors)].filter(visible);",
    "  const hit = items.find(el => wanted.includes((el.innerText || el.textContent || \"\").trim().toLowerCase()));",
    "  if (!hit) return { clicked: false, candidates: items.map(el => (el.innerText || el.textContent || \"\").trim()).filter(Boolean).slice(0, 100) };",
    "  hit.click();",
    "  return { clicked: true, tag: hit.tagName, text: (hit.innerText || hit.textContent || \"\").trim() };",
    "}"
  ].join("\n");
  const result = await call(client, "evaluate_script", { pageId, function: fn });
  return textResult(result);
}

async function downloadListing() {
  const map = new Map();
  try {
    for (const name of await fsp.readdir(DEFAULT_DOWNLOADS)) {
      const full = path.join(DEFAULT_DOWNLOADS, name);
      const st = await fsp.stat(full).catch(() => null);
      if (st?.isFile()) map.set(name, { full, size: st.size, mtimeMs: st.mtimeMs });
    }
  } catch {}
  return map;
}

async function waitNewDownload(before, timeoutMs = 120000) {
  const started = Date.now();
  let candidate = null;
  while (Date.now() - started < timeoutMs) {
    const now = await downloadListing();
    for (const [name, info] of now) {
      if (name.endsWith(".crdownload")) continue;
      const old = before.get(name);
      if (!old || info.mtimeMs > old.mtimeMs || info.size !== old.size) {
        if (info.mtimeMs >= started - 5000) candidate = info.full;
      }
    }
    const partials = [...now.keys()].filter(x => x.endsWith(".crdownload"));
    if (candidate && partials.length === 0) return candidate;
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error("Nenhum download novo terminou em até 120 segundos.");
}

async function sha256(file) {
  return await new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const stream = fs.createReadStream(file);
    stream.on("data", chunk => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", () => resolve(hash.digest("hex")));
  });
}

async function loadState(file) {
  try { return JSON.parse(await fsp.readFile(file, "utf8")); }
  catch { return { schema: 1, completed: {} }; }
}

async function saveState(file, state) {
  const tmp = file + ".tmp";
  await fsp.writeFile(tmp, JSON.stringify(state, null, 2) + "\n", "utf8");
  await fsp.rename(tmp, file);
}

function parseArgs(argv) {
  const out = { source: null, output: DEFAULT_OUTPUT, format: "txt" };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    const next = () => { if (i + 1 >= argv.length) throw new Error("Faltou valor para " + a); return argv[++i]; };
    if (a === "--source") out.source = next();
    else if (a === "--output") out.output = next();
    else if (a === "--format") out.format = next().toLowerCase();
    else throw new Error("Opção desconhecida: " + a);
  }
  if (!["txt", "srt", "docx", "pdf"].includes(out.format)) throw new Error("--format inválido");
  return out;
}

async function main() {
  const cfg = parseArgs(process.argv);
  await fsp.mkdir(cfg.output, { recursive: true });

  const transport = new StdioClientTransport({
    command: "npx",
    args: ["-y", "chrome-devtools-mcp@latest", "--autoConnect"],
    stderr: "inherit",
  });

  const client = new Client({ name: "my-transcriver-recover", version: "0.1.0" }, { capabilities: {} });
  console.log("Conectando ao Chrome existente...");
  await client.connect(transport);

  try {
    const pages = await call(client, "list_pages", {});
    const pageId = parsePageId(textResult(pages));
    if (pageId === null) throw new Error("Não encontrei uma aba do 1Transcribe.");
    console.log("Aba encontrada. pageId:", pageId);

    const before = await downloadListing();
    const click = await clickVisibleDomText(client, pageId, ["Download"]);
    console.log("Clique Download:", click);
    if (!/clicked[^a-z]*[:=]?[^a-z]*true/i.test(click)) throw new Error("Não encontrei um Download visível na página atual.");

    await new Promise(r => setTimeout(r, 800));
    const labels = { txt: ["TXT", "Text"], srt: ["SRT"], docx: ["DOCX", "Word"], pdf: ["PDF"] };
    await clickVisibleDomText(client, pageId, labels[cfg.format]).catch(() => {});

    const downloaded = await waitNewDownload(before);
    const base = cfg.source ? path.basename(cfg.source, path.extname(cfg.source)) : path.basename(downloaded, path.extname(downloaded));
    const ext = path.extname(downloaded) || "." + cfg.format;
    const destination = path.join(cfg.output, base + ext.toLowerCase());

    await fsp.rename(downloaded, destination).catch(async err => {
      if (err.code === "EXDEV") {
        await fsp.copyFile(downloaded, destination);
        await fsp.unlink(downloaded);
      } else throw err;
    });
    console.log("Salvo em:", destination);

    if (cfg.source) {
      const source = path.resolve(cfg.source);
      const st = await fsp.stat(source);
      const hash = await sha256(source);
      const stateFile = path.join(cfg.output, ".1transcribe-state.json");
      const state = await loadState(stateFile);
      state.completed[hash] = { input: source, output: destination, size: st.size, completedAt: new Date().toISOString(), recoveredFromOpenPage: true };
      await saveState(stateFile, state);
      console.log("Marcado como concluído no estado do lote.");
    }
  } finally {
    await client.close().catch(() => {});
  }
}

main().catch(err => { console.error(err?.stack || err); process.exit(1); });
