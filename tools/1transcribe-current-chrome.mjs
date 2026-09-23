#!/usr/bin/env node

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";

const HOME_URL = "https://app.1transcribe.com/home";
const DEFAULT_INPUT = "/home/esteban/Sync/Backups/Android/VoiceRecorder";
const DEFAULT_OUTPUT = "/home/esteban/Sync/Projects/my-transcriver/protegendo-a-torre-brutos";
const DEFAULT_DOWNLOADS = path.join(os.homedir(), "Downloads");
const MEDIA_EXT = new Set([".m4a", ".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac", ".mp4", ".mov", ".webm", ".mkv"]);

function parseArgs(argv) {
  const cfg = {
    input: DEFAULT_INPUT,
    file: null,
    output: DEFAULT_OUTPUT,
    limit: 1,
    format: "txt",
    timeoutMinutes: 180,
    newestFirst: false,
  };

  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error("Faltou valor para " + a);
      return argv[++i];
    };
    if (a === "--input") cfg.input = next();
    else if (a === "--file") cfg.file = next();
    else if (a === "--output") cfg.output = next();
    else if (a === "--limit") cfg.limit = Number(next());
    else if (a === "--all") cfg.limit = 0;
    else if (a === "--format") cfg.format = next().toLowerCase();
    else if (a === "--timeout-minutes") cfg.timeoutMinutes = Number(next());
    else if (a === "--newest-first") cfg.newestFirst = true;
    else if (a === "-h" || a === "--help") {
      console.log(
        "Uso:\n" +
        "  node tools/1transcribe-current-chrome.mjs [opções]\n\n" +
        "Antes: no Chrome já logado, abra chrome://inspect/#remote-debugging e habilite Remote Debugging.\n\n" +
        "Opções:\n" +
        "  --input DIR             Pasta dos áudios\n" +
        "  --file ARQUIVO          Processa só este arquivo\n" +
        "  --output DIR            Pasta final das transcrições\n" +
        "  --limit N               Quantos processar (padrão: 1)\n" +
        "  --all                   Todos os pendentes\n" +
        "  --format txt|srt|docx|pdf\n" +
        "  --timeout-minutes N     Máximo por arquivo (padrão: 180)\n" +
        "  --newest-first          Mais recentes primeiro\n"
      );
      process.exit(0);
    } else {
      throw new Error("Opção desconhecida: " + a);
    }
  }

  if (!["txt", "srt", "docx", "pdf"].includes(cfg.format)) throw new Error("--format inválido");
  if (!Number.isFinite(cfg.limit) || cfg.limit < 0) throw new Error("--limit inválido");
  if (!Number.isFinite(cfg.timeoutMinutes) || cfg.timeoutMinutes <= 0) throw new Error("--timeout-minutes inválido");
  return cfg;
}

function textResult(result) {
  return (result?.content || [])
    .filter(x => x && x.type === "text")
    .map(x => x.text || "")
    .join("\n");
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
  const temp = file + ".tmp";
  await fsp.writeFile(temp, JSON.stringify(state, null, 2) + "\n", "utf8");
  await fsp.rename(temp, file);
}

async function listMedia(dir, newestFirst) {
  const rows = [];
  for (const name of await fsp.readdir(dir)) {
    const full = path.join(dir, name);
    const st = await fsp.stat(full);
    if (!st.isFile()) continue;
    if (!MEDIA_EXT.has(path.extname(name).toLowerCase())) continue;
    rows.push({ name, full, size: st.size, mtimeMs: st.mtimeMs });
  }
  rows.sort((a, b) => newestFirst ? b.mtimeMs - a.mtimeMs : a.mtimeMs - b.mtimeMs);
  return rows;
}

async function call(client, name, args = {}) {
  const result = await client.callTool({ name, arguments: args });
  if (result?.isError) throw new Error(name + " falhou: " + textResult(result));
  return result;
}

function parsePageId(pagesText) {
  const lines = pagesText.split(/\r?\n/);
  for (const line of lines) {
    if (!/1transcribe\.com/i.test(line)) continue;
    const m =
      line.match(/\bpageId[:= ]+(\d+)/i) ||
      line.match(/^\s*(\d+)\s*[:\-]/) ||
      line.match(/\[(\d+)\]/);
    if (m) return Number(m[1]);
  }
  return null;
}

function findUid(snapshotText, wanted) {
  const lines = snapshotText.split(/\r?\n/);
  for (const line of lines) {
    if (!wanted.test(line)) continue;
    const m = line.match(/\buid=([^\s]+)/i);
    if (m) return m[1].replace(/^["']|["']$/g, "");
  }
  return null;
}

async function snapshot(client, pageId, evidencePath = null) {
  const result = await call(client, "take_snapshot", { pageId, verbose: false });
  const text = textResult(result);
  if (evidencePath) {
    await fsp.mkdir(path.dirname(evidencePath), { recursive: true });
    await fsp.writeFile(evidencePath, text, "utf8");
  }
  return text;
}

async function getPageId(client) {
  const result = await call(client, "list_pages", {});
  const raw = textResult(result);
  let id = parsePageId(raw);
  if (id !== null) return { id, raw };

  // Se a aba não estiver aberta, abre dentro da sessão autenticada atual.
  const created = await call(client, "new_page", {
    url: HOME_URL,
    background: false,
    timeout: 120000,
  });
  const createdText = textResult(created);
  id = parsePageId(createdText);

  if (id === null) {
    const again = await call(client, "list_pages", {});
    const againText = textResult(again);
    id = parsePageId(againText);
    if (id === null) throw new Error("Não consegui identificar a aba do 1Transcribe.\n" + againText);
    return { id, raw: againText };
  }
  return { id, raw: createdText };
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

async function waitNewDownload(before, timeoutMs) {
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

  throw new Error("O navegador não produziu um download completo dentro do tempo esperado.");
}

async function processOne(client, uploadTool, pageId, cfg, item, evidenceDir, index) {
  await call(client, "navigate_page", {
    pageId,
    type: "url",
    url: HOME_URL,
    timeout: 120000,
  });

  let snap = await snapshot(client, pageId);
  const importUid = findUid(snap, /\bbutton\b.*["']Import["']/i) || findUid(snap, /\bImport\b/i);
  if (!importUid) throw new Error("Não encontrei o botão Import. A sessão pode não estar autenticada.");

  await call(client, "click", { pageId, uid: importUid });
  await call(client, "wait_for", { pageId, text: ["Transcribe Files"], timeout: 30000 });

  snap = await snapshot(client, pageId);

  if (!/Portugu[eê]s/i.test(snap)) {
    throw new Error("A janela de importação não está configurada para Português.");
  }

  const transcribeUid =
    findUid(snap, /\bbutton\b.*Transcribe Files/i) ||
    findUid(snap, /Transcribe Files/i);
  if (!transcribeUid) throw new Error("Não encontrei o botão Transcribe Files.");

  const uploadArgs = { pageId, uid: transcribeUid };
  if (uploadTool?.inputSchema?.properties?.filePaths) uploadArgs.filePaths = [item.full];
  else uploadArgs.filePath = item.full;

  await call(client, "upload_file", uploadArgs);

  console.log("    upload enviado; aguardando conclusão...");
  await call(client, "wait_for", {
    pageId,
    text: ["Download"],
    timeout: cfg.timeoutMinutes * 60 * 1000,
  });

  snap = await snapshot(
    client,
    pageId,
    path.join(evidenceDir, "finished-" + String(index).padStart(3, "0") + ".txt")
  );

  const downloadUid =
    findUid(snap, /\bbutton\b.*["']Download["']/i) ||
    findUid(snap, /\bDownload\b/i);
  if (!downloadUid) throw new Error("Transcrição terminou, mas não encontrei o botão Download.");

  const before = await downloadListing();
  await call(client, "click", { pageId, uid: downloadUid });

  // Se o primeiro clique abrir opções de formato, seleciona a desejada.
  await new Promise(r => setTimeout(r, 800));
  let menuSnap = await snapshot(client, pageId);
  const patterns = {
    txt: /\b(TXT|Text)\b/i,
    srt: /\bSRT\b/i,
    docx: /\b(DOCX|Word)\b/i,
    pdf: /\bPDF\b/i,
  };
  const formatUid = findUid(menuSnap, patterns[cfg.format]);
  if (formatUid) {
    await call(client, "click", { pageId, uid: formatUid });
  }

  const downloaded = await waitNewDownload(before, 120000);
  const ext = path.extname(downloaded) || "." + cfg.format;
  const base = path.basename(item.full, path.extname(item.full));
  const destination = path.join(cfg.output, base + ext.toLowerCase());

  await fsp.mkdir(cfg.output, { recursive: true });
  await fsp.rename(downloaded, destination).catch(async err => {
    if (err.code === "EXDEV") {
      await fsp.copyFile(downloaded, destination);
      await fsp.unlink(downloaded);
    } else throw err;
  });

  return destination;
}

async function main() {
  const cfg = parseArgs(process.argv);

  await fsp.mkdir(cfg.output, { recursive: true });
  const evidenceDir = path.join(process.cwd(), "evidence", "1transcribe-current-chrome", new Date().toISOString().replace(/[:.]/g, "-"));
  await fsp.mkdir(evidenceDir, { recursive: true });

  const stateFile = path.join(cfg.output, ".1transcribe-state.json");
  const state = await loadState(stateFile);

  let files;
  if (cfg.file) {
    const full = path.resolve(cfg.file);
    const st = await fsp.stat(full);
    if (!st.isFile()) throw new Error("--file não aponta para um arquivo: " + full);
    files = [{ name: path.basename(full), full, size: st.size, mtimeMs: st.mtimeMs }];
  } else {
    files = await listMedia(cfg.input, cfg.newestFirst);
  }

  const filesystemRoot = cfg.file
    ? path.dirname(path.resolve(cfg.file))
    : path.resolve(cfg.input);

  const transport = new StdioClientTransport({
    command: "npx",
    args: [
      "-y",
      "chrome-devtools-mcp@latest",
      "--autoConnect",
      "--filesystem-root",
      filesystemRoot,
    ],
    stderr: "inherit",
  });

  console.log("Pasta autorizada para upload:", filesystemRoot);

  const client = new Client(
    { name: "my-transcriver-1transcribe", version: "0.1.0" },
    { capabilities: {} }
  );

  console.log("Conectando à sessão Chrome já aberta...");
  console.log("Se o Chrome pedir autorização de depuração, clique em Allow.");
  await client.connect(transport);

  const tools = await client.listTools();
  const uploadTool = tools.tools.find(t => t.name === "upload_file");
  if (!uploadTool) throw new Error("Chrome DevTools MCP não expôs upload_file.");

  const page = await getPageId(client);
  const pageId = page.id;
  await fsp.writeFile(path.join(evidenceDir, "pages.txt"), page.raw, "utf8");

  console.log("Aba 1Transcribe encontrada. pageId:", pageId);
  console.log("Arquivos:", files.length);
  console.log("Saída:", cfg.output);

  let processed = 0;
  let failures = 0;
  const log = [];

  try {
    for (const item of files) {
      if (cfg.limit > 0 && processed >= cfg.limit) break;

      const hash = await sha256(item.full);
      const done = state.completed[hash];
      if (done?.output && fs.existsSync(done.output)) {
        console.log("[skip]", item.name);
        continue;
      }

      processed++;
      console.log("");
      console.log("[" + processed + "]", item.name);

      try {
        const output = await processOne(client, uploadTool, pageId, cfg, item, evidenceDir, processed);
        state.completed[hash] = {
          input: item.full,
          output,
          size: item.size,
          completedAt: new Date().toISOString(),
        };
        await saveState(stateFile, state);
        log.push({ input: item.full, output, status: "ok" });
        console.log("    OK ->", output);
      } catch (err) {
        failures++;
        const message = err?.stack || String(err);
        log.push({ input: item.full, status: "failed", error: message });
        console.error("    ERRO:", err?.message || err);
        try {
          await snapshot(client, pageId, path.join(evidenceDir, "failed-" + String(processed).padStart(3, "0") + ".txt"));
        } catch {}
      }

      await fsp.writeFile(
        path.join(evidenceDir, "run.json"),
        JSON.stringify({ config: cfg, processed, failures, log }, null, 2) + "\n",
        "utf8"
      );
    }
  } finally {
    await client.close().catch(() => {});
  }

  await fsp.writeFile(
    path.join(evidenceDir, "report.md"),
    [
      "# 1Transcribe — Chrome existente",
      "",
      "- Processados: " + processed,
      "- Falhas: " + failures,
      "- Output: " + cfg.output,
      "- Formato: " + cfg.format,
      "",
    ].join("\n"),
    "utf8"
  );

  console.log("");
  console.log("Concluído:", processed, "processado(s),", failures, "falha(s)");
  console.log("Evidências:", evidenceDir);
  process.exitCode = failures ? 2 : 0;
}

main().catch(err => {
  console.error(err?.stack || err);
  process.exit(1);
});
