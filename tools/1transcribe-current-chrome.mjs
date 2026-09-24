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
    adaptiveDelay: true,
    downloadExisting: false,
    ensureSpeakers: true,
    pauseSeconds: 3,
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
    else if (a === "--no-delay") cfg.adaptiveDelay = false;
    else if (a === "--download-existing") cfg.downloadExisting = true;
    else if (a === "--skip-speakers") cfg.ensureSpeakers = false;
    else if (a === "--pause-seconds") cfg.pauseSeconds = Number(next());
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
        "  --format txt|srt|docx|pdf  (padrão: txt; TXT usa timestamps)\n" +
        "  --timeout-minutes N     Máximo por arquivo (padrão: 180)\n" +
        "  --newest-first          Mais recentes primeiro\n" +
        "  --no-delay              Desativa a pausa adaptativa entre arquivos\n" +
        "  --download-existing     Somente baixa o que já existe no 1Transcribe\n" +
        "  --skip-speakers         No modo download, não executa Add speaker\n" +
        "  --pause-seconds N       No modo download, pausa entre itens (padrão: 3s)\n"
      );
      process.exit(0);
    } else {
      throw new Error("Opção desconhecida: " + a);
    }
  }

  if (!["txt", "srt", "docx", "pdf"].includes(cfg.format)) throw new Error("--format inválido");
  if (!Number.isFinite(cfg.limit) || cfg.limit < 0) throw new Error("--limit inválido");
  if (!Number.isFinite(cfg.timeoutMinutes) || cfg.timeoutMinutes <= 0) throw new Error("--timeout-minutes inválido");
  if (!Number.isFinite(cfg.pauseSeconds) || cfg.pauseSeconds < 0) throw new Error("--pause-seconds inválido");
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

async function mediaDurationSeconds(file) {
  try {
    const { spawnSync } = await import("node:child_process");
    const r = spawnSync(
      "ffprobe",
      ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file],
      { encoding: "utf8" }
    );
    if (r.status === 0) {
      const value = Number(String(r.stdout).trim());
      if (Number.isFinite(value) && value > 0) return value;
    }
  } catch {}
  return null;
}

function adaptiveDelaySeconds(durationSeconds, sizeBytes) {
  if (Number.isFinite(durationSeconds)) {
    if (durationSeconds <= 120) return 60;
    if (durationSeconds <= 300) return 45;
    if (durationSeconds <= 900) return 30;
    if (durationSeconds <= 1800) return 20;
    if (durationSeconds <= 3600) return 10;
    return 5;
  }

  const mb = sizeBytes / (1024 * 1024);
  if (mb <= 5) return 60;
  if (mb <= 15) return 45;
  if (mb <= 50) return 30;
  if (mb <= 150) return 20;
  return 10;
}

async function adaptivePause(item) {
  const duration = await mediaDurationSeconds(item.full);
  const seconds = adaptiveDelaySeconds(duration, item.size);
  const desc = Number.isFinite(duration)
    ? Math.round(duration) + "s de áudio"
    : Math.round(item.size / (1024 * 1024)) + " MiB";
  console.log("    pausa anti-saturação:", seconds + "s", "(" + desc + ")");
  await new Promise(resolve => setTimeout(resolve, seconds * 1000));
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

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function findFileUid(snapshotText, filename) {
  const full = findUid(snapshotText, new RegExp(escapeRegex(filename), "i"));
  if (full) return full;
  const stem = filename.replace(/\.[^.]+$/, "");
  return findUid(snapshotText, new RegExp(escapeRegex(stem), "i"));
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

async function clickVisibleDomText(client, pageId, labels) {
  const wanted = JSON.stringify(labels.map(x => String(x).toLowerCase()));
  const fn = `() => {
    const wanted = ${wanted};
    const selectors = "button,a,[role=button],[role=menuitem],[role=option]";
    const visible = el => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
    };
    const items = [...document.querySelectorAll(selectors)].filter(visible);
    const hit = items.find(el => wanted.includes((el.innerText || el.textContent || "").trim().toLowerCase()));
    if (!hit) {
      return { clicked: false, candidates: items.map(el => (el.innerText || el.textContent || "").trim()).filter(Boolean).slice(0, 80) };
    }
    hit.click();
    return { clicked: true, tag: hit.tagName, text: (hit.innerText || hit.textContent || "").trim() };
  }`;
  const result = await call(client, "evaluate_script", { pageId, function: fn });
  const raw = textResult(result);
  return raw;
}
async function speakerState(client, pageId) {
  const fn = `() => {
    const body = document.body?.innerText || "";
    const speakerMatches = body.match(/\\bSpeaker\\s+\\d+\\b/gi) || [];
    const addSpeaker = [...document.querySelectorAll("button,a,[role=button]")].some(el => {
      const text = (el.innerText || el.textContent || "").trim().toLowerCase();
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return text === "add speaker" && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    });
    return { addSpeaker, speakers: [...new Set(speakerMatches.map(x => x.toLowerCase()))], bodyTail: body.slice(-1000) };
  }`;
  return textResult(await call(client, "evaluate_script", { pageId, function: fn, waitForStableDom: false }));
}

async function ensureSpeakers(client, pageId, timeoutMs) {
  let state = await speakerState(client, pageId);
  if (/speaker\s+\d+/i.test(state)) {
    console.log("    speakers já identificados; seguindo...");
    return;
  }

  console.log("    identificando speakers...");
  const clicked = await clickVisibleDomText(client, pageId, ["Add speaker"]);
  if (!/clicked[^a-z]*[:=]?[^a-z]*true/i.test(clicked)) {
    throw new Error("Não encontrei o botão Add speaker. Estado: " + state);
  }

  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await new Promise(resolve => setTimeout(resolve, 5000));
    state = await speakerState(client, pageId);

    if (/speaker\s+\d+/i.test(state)) {
      console.log("    speakers identificados.");
      return;
    }

    if (/failed|error|try again/i.test(state)) {
      throw new Error("O 1Transcribe indicou erro na identificação de speakers. " + state);
    }
  }

  throw new Error("Timeout aguardando identificação dos speakers.");
}

async function configureDownloadModal(client, pageId) {
  const chooseTxt = `() => {
    const visible = el => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
    };
    const norm = el => (el.innerText || el.textContent || "").trim();
    const all = [...document.querySelectorAll("*")].filter(visible);
    const txt = all.find(el => /^\\.?TXT$/i.test(norm(el)));
    if (!txt) return { ok: false, step: "txt" };
    (txt.closest("button,[role=button],label") || txt).click();
    return { ok: true };
  }`;

  const txtResult = textResult(await call(client, "evaluate_script", { pageId, function: chooseTxt }));
  if (!/"?ok"?[^a-z]*[:=]?[^a-z]*true/i.test(txtResult)) {
    throw new Error("Não consegui selecionar .TXT. " + txtResult);
  }

  await new Promise(resolve => setTimeout(resolve, 250));

  const ensureTs = `() => {
    const visible = el => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
    };
    const norm = el => (el.innerText || el.textContent || "").trim();
    const labels = [...document.querySelectorAll("*")].filter(visible);
    const label = labels.find(el => /^Include timestamps$/i.test(norm(el)));
    if (!label) return { ok: false, step: "label" };

    let control = null;

    if (label.tagName === "LABEL" && label.htmlFor) {
      control = document.getElementById(label.htmlFor);
    }

    let node = label;
    for (let depth = 0; !control && depth < 5 && node; depth++, node = node.parentElement) {
      control = node.querySelector?.(
        "input[type=checkbox],[role=switch],[aria-checked],[data-state=checked],[data-state=unchecked]"
      ) || null;
    }

    if (!control) {
      const lr = label.getBoundingClientRect();
      const candidates = [...document.querySelectorAll("button,input[type=checkbox],[role=switch]")]
        .filter(visible)
        .map(el => {
          const r = el.getBoundingClientRect();
          return {
            el,
            dy: Math.abs((r.top + r.bottom) / 2 - (lr.top + lr.bottom) / 2),
            dx: Math.abs(r.left - lr.right)
          };
        })
        .filter(x => x.dy < 30)
        .sort((a, b) => (a.dy - b.dy) || (a.dx - b.dx));
      control = candidates[0]?.el || null;
    }

    if (!control) return { ok: false, step: "control" };

    let known = true;
    let checked = false;
    if (control.matches("input[type=checkbox]")) {
      checked = Boolean(control.checked);
    } else if (control.getAttribute("aria-checked") != null) {
      checked = control.getAttribute("aria-checked") === "true";
    } else if (control.getAttribute("data-state") != null) {
      checked = /checked|on/i.test(control.getAttribute("data-state") || "");
    } else {
      known = false;
    }

    if (!known || !checked) control.click();

    return {
      ok: true,
      known,
      before: checked,
      action: !known ? "clicked-unknown" : (checked ? "kept-on" : "turned-on"),
      tag: control.tagName,
      role: control.getAttribute("role"),
      ariaChecked: control.getAttribute("aria-checked"),
      dataState: control.getAttribute("data-state")
    };
  }`;

  const tsResult = textResult(await call(client, "evaluate_script", { pageId, function: ensureTs }));
  if (!/"?ok"?[^a-z]*[:=]?[^a-z]*true/i.test(tsResult)) {
    throw new Error("Não consegui ligar Include timestamps. " + tsResult);
  }

  console.log("    download: .TXT + timestamps.");
  return { txtResult, tsResult };
}
async function clickDownloadInsideModal(client, pageId) {
  const fn = `() => {
    const visible = el => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
    };
    const norm = el => (el.innerText || el.textContent || "").trim();
    const headings = [...document.querySelectorAll("h1,h2,h3,h4,div")].filter(visible);
    const title = headings.find(el => /^Download Transcript$/i.test(norm(el)));
    if (!title) return { clicked: false, reason: "modal-title-not-found" };

    let root = title.parentElement;
    for (let i = 0; i < 6 && root; i++, root = root.parentElement) {
      const buttons = [...root.querySelectorAll("button,[role=button]")].filter(visible);
      const target = buttons.find(el => /^Download$/i.test(norm(el)));
      if (target) {
        target.click();
        return { clicked: true, text: norm(target) };
      }
    }

    return { clicked: false, reason: "modal-download-not-found" };
  }`;
  const result = textResult(await call(client, "evaluate_script", { pageId, function: fn }));
  if (!/clicked[^a-z]*[:=]?[^a-z]*true/i.test(result)) {
    throw new Error("Não consegui clicar no Download final da modal. " + result);
  }
  return result;
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

async function waitForCorrectTranscript(client, pageId, filename, timeoutMs) {
  const stem = filename.replace(/\.[^.]+$/, "");
  const stemJson = JSON.stringify(stem);
  const started = Date.now();
  let last = "";

  while (Date.now() - started < timeoutMs) {
    const fn = [
      "() => {",
      "  const stem = " + stemJson + ";",
      "  const visible = el => {",
      "    const style = getComputedStyle(el);",
      "    const rect = el.getBoundingClientRect();",
      "    return style.display !== \"none\" && style.visibility !== \"hidden\" && rect.width > 0 && rect.height > 0;",
      "  };",
      "  const norm = el => (el.innerText || el.textContent || \"\").trim();",
      "  const els = [...document.querySelectorAll(\"h1,h2,h3,h4,button,a,[role=button],main *\")].filter(visible);",
      "  const exactTitle = els.some(el => norm(el).toLowerCase() === stem.toLowerCase());",
      "  const body = document.body?.innerText || \"\";",
      "  const importing = /\\bImporting\\.\\.\\./i.test(body) || /\\bImporting\\s+\\d+\\/\\d+/i.test(body);",
      "  const transcribing = /\\bTranscribing file\\.\\.\\./i.test(body);",
      "  const addSpeaker = els.some(el => norm(el).toLowerCase() === \"add speaker\");",
      "  const speakers = (body.match(/\\bSpeaker\\s+\\d+\\b/gi) || []).length;",
      "  const downloads = els.filter(el => norm(el).toLowerCase() === \"download\").length;",
      "  return { href: location.href, exactTitle, importing, transcribing, addSpeaker, speakers, downloads, title: document.title };",
      "}",
    ].join("\\n");

    last = textResult(await call(client, "evaluate_script", {
      pageId,
      function: fn,
      waitForStableDom: false,
    }));

    const isTranscript = /\/transcript\?id=/i.test(last);
    const correctTitle = /"?exactTitle"?[^a-z]*[:=]?[^a-z]*true/i.test(last);
    const importing = /"?importing"?[^a-z]*[:=]?[^a-z]*true/i.test(last);
    const transcribing = /"?transcribing"?[^a-z]*[:=]?[^a-z]*true/i.test(last);

    if (isTranscript && correctTitle && !importing && !transcribing) return last;
    await new Promise(resolve => setTimeout(resolve, 3000));
  }

  throw new Error("Timeout aguardando abrir a transcrição correta de " + filename + ". Último estado: " + last);
}

async function findExistingTranscriptUid(client, pageId, filename, maxLoads = 30) {
  for (let attempt = 0; attempt <= maxLoads; attempt++) {
    const snap = await snapshot(client, pageId);
    const found = findFileUid(snap, filename);
    if (found) return found;

    const loadMoreUid = findUid(snap, /\bLoad More\b/i);
    if (!loadMoreUid) return null;

    await call(client, "click", { pageId, uid: loadMoreUid });
    await new Promise(resolve => setTimeout(resolve, 800));
  }

  return null;
}
function hashText(text) {
  return crypto.createHash("sha256").update(String(text)).digest("hex");
}

function safeName(name) {
  const cleaned = String(name || "transcript")
    .replace(/[\\/:*?"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/g, "");
  return cleaned || "transcript";
}

function parseHomeCards(snapshotText) {
  const counts = new Map();
  const cards = [];
  for (const line of snapshotText.split(/\r?\n/)) {
    if (!/\bbutton\s+"[A-Z]{3}\s+\d{1,2}\s*[•·]\s*\d{1,2}:\d{2}\s*(AM|PM)\b/i.test(line)) continue;
    const fingerprint = line.replace(/^\s*uid=\S+\s+button\s+/, "").trim();
    const occurrence = counts.get(fingerprint) || 0;
    counts.set(fingerprint, occurrence + 1);
    cards.push({ fingerprint, occurrence });
  }
  return cards;
}

function findHomeCardUid(snapshotText, fingerprint, occurrence = 0) {
  let seen = 0;
  for (const line of snapshotText.split(/\r?\n/)) {
    if (!line.includes(fingerprint)) continue;
    const stripped = line.replace(/^\s*uid=\S+\s+button\s+/, "").trim();
    if (stripped !== fingerprint) continue;
    if (seen++ !== occurrence) continue;
    const m = line.match(/\buid=([^\s]+)/i);
    if (m) return m[1].replace(/^["\']|["\']$/g, "");
  }
  return null;
}

async function loadAllExistingCards(client, pageId, maxLoads = 100) {
  let lastSnapshot = "";
  let cards = [];
  let stable = 0;
  let previousCount = -1;

  for (let attempt = 0; attempt < maxLoads; attempt++) {
    lastSnapshot = await snapshot(client, pageId);
    cards = parseHomeCards(lastSnapshot);

    if (cards.length === previousCount) stable++;
    else stable = 0;
    previousCount = cards.length;

    const loadMoreUid = findUid(lastSnapshot, /\bbutton "Load More"/i);
    if (!loadMoreUid) break;
    await call(client, "click", { pageId, uid: loadMoreUid });
    await new Promise(resolve => setTimeout(resolve, 800));
    if (stable >= 5) break;
  }

  lastSnapshot = await snapshot(client, pageId);
  cards = parseHomeCards(lastSnapshot);
  return { cards, snapshot: lastSnapshot };
}

async function waitExistingTranscriptReady(client, pageId, timeoutMs) {
  const started = Date.now();
  let snap = "";

  while (Date.now() - started < timeoutMs) {
    snap = await snapshot(client, pageId);
    const isTranscript = /RootWebArea .*url="https:\/\/app\.1transcribe\.com\/transcript\?id=/i.test(snap);
    const busy = /Transcribing file\.\.\.|Importing\.\.\./i.test(snap);
    const hasDownload = /\bbutton "Download"/i.test(snap);
    if (isTranscript && !busy && hasDownload) return snap;
    await new Promise(resolve => setTimeout(resolve, 2000));
  }

  throw new Error("Timeout aguardando a transcrição existente abrir.");
}

function transcriptMetaFromSnapshot(snapshotText) {
  const urlMatch = snapshotText.match(/RootWebArea .*url="([^"]+)"/i);
  const url = urlMatch ? urlMatch[1] : "";
  const lines = snapshotText.split(/\r?\n/);
  const mainIndex = lines.findIndex(line => /\bmain\s*$/.test(line.trim()));
  let title = "";

  for (let i = Math.max(0, mainIndex + 1); i < lines.length; i++) {
    const line = lines[i];
    if (/\bbutton "Copy"/i.test(line)) break;
    const m = line.match(/\bbutton "([^"]+)"/);
    if (m && m[1] && !/^(1x|0\.5x|1\.5x|2x)$/i.test(m[1])) {
      title = m[1];
      break;
    }
  }

  let id = "";
  try { id = new URL(url).searchParams.get("id") || ""; } catch {}
  return { title: title || id || "transcript", id, url };
}

async function ensureSpeakersIfPossible(client, pageId, timeoutMs) {
  const state = await speakerState(client, pageId);
  if (/speaker\s+\d+/i.test(state)) return { state: "existing" };
  const canAdd = /"?addSpeaker"?[^a-z]*[:=]?[^a-z]*true/i.test(state);
  if (!canAdd) return { state: "unavailable" };
  await ensureSpeakers(client, pageId, timeoutMs);
  return { state: "generated" };
}

async function uniqueExistingDestination(outputDir, title, transcriptId) {
  const base = safeName(title);
  let destination = path.join(outputDir, base + ".txt");
  if (!fs.existsSync(destination)) return destination;
  const suffix = safeName(transcriptId || hashText(title).slice(0, 12));
  destination = path.join(outputDir, base + "--" + suffix + ".txt");
  return destination;
}

async function moveDownloadedFile(source, destination) {
  await fsp.mkdir(path.dirname(destination), { recursive: true });
  await fsp.rename(source, destination).catch(async err => {
    if (err.code === "EXDEV") {
      await fsp.copyFile(source, destination);
      await fsp.unlink(source);
    } else throw err;
  });
}

async function validateDownloadedTranscript(file) {
  const text = await fsp.readFile(file, "utf8");
  if (!text.trim()) {
    throw new Error("O TXT baixado está vazio: " + file);
  }

  const hasTimestamp =
    /\[(?:\d{1,2}:)?\d{1,2}:\d{2}\]/m.test(text) ||
    /(?:^|\n)\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s/m.test(text) ||
    /\b\d{1,2}:\d{2}(?::\d{2})?\b/m.test(text);

  if (!hasTimestamp) {
    throw new Error("O TXT baixado não contém timestamps reconhecíveis: " + file);
  }

  return {
    bytes: Buffer.byteLength(text, "utf8"),
    chars: text.length,
    hasTimestamp: true,
    hasSpeakerLabels: /\bSpeaker\s+\d+\b/i.test(text),
  };
}

async function findHomeCardUidIncremental(client, pageId, fingerprint, occurrence = 0, maxLoads = 100) {
  for (let attempt = 0; attempt <= maxLoads; attempt++) {
    const snap = await snapshot(client, pageId);
    const uid = findHomeCardUid(snap, fingerprint, occurrence);
    if (uid) return uid;

    const loadMoreUid = findUid(snap, /\\bbutton "Load More"/i);
    if (!loadMoreUid) return null;
    await call(client, "click", { pageId, uid: loadMoreUid });
    await new Promise(resolve => setTimeout(resolve, 650));
  }
  return null;
}

async function returnToHomePreservingHistory(client, pageId) {
  try {
    await call(client, "navigate_page", {
      pageId,
      type: "back",
      timeout: 120000,
    });
    await new Promise(resolve => setTimeout(resolve, 500));
    const snap = await snapshot(client, pageId);
    if (/url="https:\\/\\/app\\.1transcribe\\.com\\/home/i.test(snap)) return;
  } catch {}

  await call(client, "navigate_page", {
    pageId,
    type: "url",
    url: HOME_URL,
    timeout: 120000,
  });
}

async function downloadExistingMode(cfg) {
  await fsp.mkdir(cfg.output, { recursive: true });
  const evidenceDir = path.join(
    process.cwd(),
    "evidence",
    "1transcribe-download-existing",
    new Date().toISOString().replace(/[:.]/g, "-")
  );
  await fsp.mkdir(evidenceDir, { recursive: true });

  const stateFile = path.join(cfg.output, ".download-existing-state.json");
  const state = await loadState(stateFile);

  const transport = new StdioClientTransport({
    command: "npx",
    args: ["-y", "chrome-devtools-mcp@latest", "--autoConnect"],
    stderr: "inherit",
  });
  const client = new Client(
    { name: "my-transcriver-download-existing", version: "0.1.0" },
    { capabilities: {} }
  );

  let processed = 0;
  let downloadedCount = 0;
  let skipped = 0;
  let failures = 0;
  let consecutiveFailures = 0;
  const log = [];

  console.log("Conectando à sessão Chrome já aberta...");
  await client.connect(transport);

  try {
    const page = await getPageId(client);
    const pageId = page.id;

    await call(client, "navigate_page", {
      pageId,
      type: "url",
      url: HOME_URL,
      timeout: 120000,
    });

    const inventory = await loadAllExistingCards(client, pageId);
    const cards = inventory.cards;
    console.log("Transcrições encontradas:", cards.length);
    console.log("Saída:", cfg.output);

    await fsp.writeFile(
      path.join(evidenceDir, "inventory.json"),
      JSON.stringify(cards, null, 2) + "\n",
      "utf8"
    );

    for (const card of cards) {
      if (cfg.limit > 0 && processed >= cfg.limit) break;
      const key = hashText(card.fingerprint + "#" + card.occurrence);
      const done = state.completed[key];
      if (
        done?.output &&
        fs.existsSync(done.output) &&
        done.validation?.hasTimestamp === true
      ) {
        skipped++;
        console.log("[skip]", done.title || key.slice(0, 12));
        continue;
      }

      processed++;
      console.log("");
      console.log("[" + processed + "/" + cards.length + "]");

      try {
        const uid = await findHomeCardUidIncremental(
          client,
          pageId,
          card.fingerprint,
          card.occurrence
        );
        if (!uid) throw new Error("Não consegui reencontrar o card na home.");
        await call(client, "click", { pageId, uid });

        const readySnapshot = await waitExistingTranscriptReady(
          client,
          pageId,
          cfg.timeoutMinutes * 60 * 1000
        );
        const meta = transcriptMetaFromSnapshot(readySnapshot);
        console.log("    título:", meta.title);

        let speakers = { state: "skipped" };
        if (cfg.ensureSpeakers) {
          speakers = await ensureSpeakersIfPossible(
            client,
            pageId,
            cfg.timeoutMinutes * 60 * 1000
          );
          console.log("    speakers:", speakers.state);
        }

        const before = await downloadListing();
        const openDownload = await clickVisibleDomText(client, pageId, ["Download"]);
        if (!/clicked[^a-z]*[:=]?[^a-z]*true/i.test(openDownload)) {
          throw new Error("Não consegui abrir Download Transcript. " + openDownload);
        }

        await call(client, "wait_for", {
          pageId,
          text: ["Download Transcript"],
          timeout: 30000,
        });
        await configureDownloadModal(client, pageId);
        await new Promise(resolve => setTimeout(resolve, 300));
        await clickDownloadInsideModal(client, pageId);

        const tempFile = await waitNewDownload(before, 120000);
        const destination = await uniqueExistingDestination(
          cfg.output,
          meta.title,
          meta.id
        );
        await moveDownloadedFile(tempFile, destination);
        const validation = await validateDownloadedTranscript(destination);

        const record = {
          fingerprint: card.fingerprint,
          occurrence: card.occurrence,
          title: meta.title,
          transcriptId: meta.id,
          url: meta.url,
          output: destination,
          format: "txt",
          timestamps: true,
          speakers,
          validation,
          completedAt: new Date().toISOString(),
        };
        state.completed[key] = record;
        await saveState(stateFile, state);

        log.push({ status: "ok", ...record });
        downloadedCount++;
        consecutiveFailures = 0;
        console.log("    OK ->", destination);
      } catch (err) {
        failures++;
        consecutiveFailures++;
        const message = err?.stack || String(err);
        log.push({
          status: "failed",
          key,
          fingerprint: card.fingerprint,
          occurrence: card.occurrence,
          error: message,
        });
        console.error("    ERRO:", err?.message || err);
      }

      await fsp.writeFile(
        path.join(evidenceDir, "run.json"),
        JSON.stringify({
          config: cfg,
          found: cards.length,
          processed,
          downloaded: downloadedCount,
          skipped,
          failures,
          log,
        }, null, 2) + "\n",
        "utf8"
      );

      await returnToHomePreservingHistory(client, pageId);

      if (consecutiveFailures >= 3) {
        console.error("FUSÍVEL: 3 falhas consecutivas. Interrompendo.");
        break;
      }

      if (cfg.pauseSeconds > 0) {
        await new Promise(resolve => setTimeout(resolve, cfg.pauseSeconds * 1000));
      }
    }

    await fsp.writeFile(
      path.join(evidenceDir, "report.md"),
      [
        "# 1Transcribe — download de existentes",
        "",
        "- Encontradas: " + cards.length,
        "- Processadas: " + processed,
        "- Baixadas: " + downloadedCount,
        "- Já baixadas: " + skipped,
        "- Falhas: " + failures,
        "- Output: " + cfg.output,
        "- Formato: TXT com timestamps",
        "- Add speaker: " + (cfg.ensureSpeakers ? "sim" : "não"),
        "",
      ].join("\n"),
      "utf8"
    );

    console.log("");
    console.log("Concluído. Baixadas:", downloadedCount, "Puladas:", skipped, "Falhas:", failures);
    console.log("Evidências:", evidenceDir);
  } finally {
    await client.close().catch(() => {});
  }

  process.exitCode = failures ? 2 : 0;
}

async function processOne(client, uploadTool, pageId, cfg, item, evidenceDir, index) {
  await call(client, "navigate_page", {
    pageId,
    type: "url",
    url: HOME_URL,
    timeout: 120000,
  });

  let snap = await snapshot(client, pageId);
  const existingUid = await findExistingTranscriptUid(client, pageId, item.name);

  if (existingUid) {
    console.log("    já existe no 1Transcribe; reaproveitando...");
    await call(client, "click", { pageId, uid: existingUid });
    console.log("    abrindo transcrição existente...");
    await waitForCorrectTranscript(
      client,
      pageId,
      item.name,
      cfg.timeoutMinutes * 60 * 1000
    );
  } else {
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

    console.log("    upload enviado; aguardando importação/transcrição terminar...");
    await waitForCorrectTranscript(
      client,
      pageId,
      item.name,
      cfg.timeoutMinutes * 60 * 1000
    );
  }

  await ensureSpeakers(client, pageId, cfg.timeoutMinutes * 60 * 1000);

  snap = await snapshot(
    client,
    pageId,
    path.join(evidenceDir, "finished-" + String(index).padStart(3, "0") + ".txt")
  );

  const before = await downloadListing();
  const openDownload = await clickVisibleDomText(client, pageId, ["Download"]);
  if (!/clicked[^a-z]*[:=]?[^a-z]*true/i.test(openDownload)) {
    throw new Error("Não consegui abrir a janela Download Transcript. " + openDownload);
  }

  await call(client, "wait_for", { pageId, text: ["Download Transcript"], timeout: 30000 });
  await configureDownloadModal(client, pageId);
  await new Promise(resolve => setTimeout(resolve, 300));
  await clickDownloadInsideModal(client, pageId);

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

  if (cfg.downloadExisting) {
    if (cfg.output === DEFAULT_OUTPUT) {
      cfg.output = "/home/esteban/Sync/Projects/my-transcriver/protegendo-a-torre-1transcribe-existentes";
    }
    await downloadExistingMode(cfg);
    return;
  }

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
  let consecutiveFailures = 0;
  const maxConsecutiveFailures = 3;
  const log = [];

  try {
    for (const item of files) {
      if (cfg.limit > 0 && processed >= cfg.limit) break;

      const hash = await sha256(item.full);
      const done = state.completed[hash];
      const finalDone =
        done?.output &&
        fs.existsSync(done.output) &&
        done.format === "txt" &&
        done.timestamps === true &&
        done.speakers === true;
      if (finalDone) {
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
          format: "txt",
          timestamps: true,
          speakers: true,
          pipelineVersion: 2,
        };
        await saveState(stateFile, state);
        log.push({ input: item.full, output, status: "ok" });
        consecutiveFailures = 0;
        console.log("    OK ->", output);
      } catch (err) {
        failures++;
        consecutiveFailures++;
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

      if (consecutiveFailures >= maxConsecutiveFailures) {
        console.error("FUSÍVEL: 3 falhas consecutivas. Lote interrompido para evitar uploads incorretos.");
        break;
      }

      const reachedLimit = cfg.limit > 0 && processed >= cfg.limit;
      if (cfg.adaptiveDelay && !reachedLimit) {
        await adaptivePause(item);
      }
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
