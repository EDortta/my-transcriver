#!/usr/bin/env node

import { chromium } from "playwright";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";

const HOME_URL = "https://app.1transcribe.com/home";
const DEFAULT_INPUT = "/home/esteban/Sync/Backups/Android/VoiceRecorder";
const DEFAULT_OUTPUT = path.join(os.homedir(), "Downloads", "1transcribe");
const DEFAULT_PROFILE = path.join(os.homedir(), ".cache", "my-transcriver", "1transcribe-profile");
const MEDIA_EXT = new Set([".m4a", ".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac", ".mp4", ".mov", ".webm", ".mkv"]);

function parseArgs(argv) {
  const cfg = {
    input: DEFAULT_INPUT,
    file: null,
    output: DEFAULT_OUTPUT,
    profile: DEFAULT_PROFILE,
    limit: 1,
    format: "txt",
    headless: false,
    timeoutMinutes: 180,
    newestFirst: false
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error("Faltou valor para " + arg);
      return argv[++i];
    };
    if (arg === "--input") cfg.input = next();
    else if (arg === "--file") cfg.file = next();
    else if (arg === "--output") cfg.output = next();
    else if (arg === "--profile") cfg.profile = next();
    else if (arg === "--limit") cfg.limit = Number(next());
    else if (arg === "--all") cfg.limit = 0;
    else if (arg === "--format") cfg.format = next().toLowerCase();
    else if (arg === "--timeout-minutes") cfg.timeoutMinutes = Number(next());
    else if (arg === "--newest-first") cfg.newestFirst = true;
    else if (arg === "--headless") cfg.headless = true;
    else if (arg === "-h" || arg === "--help") {
      console.log(
        "Uso:\\n" +
        "  node tools/1transcribe-batch.mjs [opções]\\n\\n" +
        "Opções:\\n" +
        "  --input DIR             Pasta dos áudios\\n" +
        "  --output DIR            Pasta das transcrições\\n" +
        "  --limit N               Quantos processar (padrão: 1)\\n" +
        "  --all                   Processar todos os pendentes\\n" +
        "  --format txt|srt|docx|pdf\\n" +
        "  --timeout-minutes N     Timeout por arquivo (padrão: 180)\\n" +
        "  --newest-first          Mais recentes primeiro\\n" +
        "  --headless              Sem janela gráfica\\n\\n" +
        "Padrões:\\n" +
        "  input:  " + DEFAULT_INPUT + "\\n" +
        "  output: " + DEFAULT_OUTPUT + "\\n" +
        "  profile:" + DEFAULT_PROFILE + "\\n"
      );
      process.exit(0);
    } else {
      throw new Error("Opção desconhecida: " + arg);
    }
  }

  if (!["txt", "srt", "docx", "pdf"].includes(cfg.format)) throw new Error("--format inválido");
  if (!Number.isFinite(cfg.limit) || cfg.limit < 0) throw new Error("--limit inválido");
  return cfg;
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
  try {
    return JSON.parse(await fsp.readFile(file, "utf8"));
  } catch {
    return { schema: 1, completed: {} };
  }
}

async function saveState(file, state) {
  const tmp = file + ".tmp";
  await fsp.writeFile(tmp, JSON.stringify(state, null, 2) + "\\n", "utf8");
  await fsp.rename(tmp, file);
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

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

async function saveEvidence(page, dir, name) {
  try { await page.screenshot({ path: path.join(dir, name + ".png"), fullPage: true }); } catch {}
  try { await fsp.writeFile(path.join(dir, name + ".html"), await page.content(), "utf8"); } catch {}
}

async function ensureLogin(page) {
  await page.goto(HOME_URL, { waitUntil: "domcontentloaded", timeout: 120000 });
  const importButton = page.getByRole("button", { name: /^Import$/i }).first();

  if (await importButton.isVisible().catch(() => false)) return;

  console.log("");
  console.log("Faça login no 1Transcribe na janela aberta.");
  console.log("A sessão ficará guardada no perfil do robô.");
  console.log("O script continua sozinho quando aparecer o botão Import.");
  console.log("");

  await importButton.waitFor({ state: "visible", timeout: 10 * 60 * 1000 });
}

async function openImport(page) {
  await page.goto(HOME_URL, { waitUntil: "domcontentloaded", timeout: 120000 });
  const importButton = page.getByRole("button", { name: /^Import$/i }).first();
  await importButton.waitFor({ state: "visible", timeout: 60000 });
  await importButton.click();

  await page.getByText(/Transcribe Files/i).first().waitFor({ state: "visible", timeout: 30000 });

  const ptVisible = await page.getByText(/Portugu[eê]s/i).first().isVisible().catch(() => false);
  if (!ptVisible) {
    const language = page.getByText(/Language in the file/i).first();
    await language.locator("..").click().catch(() => {});
    const pt = page.getByText(/Portugu[eê]s/i).last();
    if (await pt.isVisible().catch(() => false)) await pt.click();
  }
}

async function upload(page, file) {
  const chooserPromise = page.waitForEvent("filechooser", { timeout: 30000 });
  await page.getByRole("button", { name: /^Transcribe Files$/i }).first().click();
  const chooser = await chooserPromise;
  await chooser.setFiles(file);
  await page.waitForTimeout(1500);
}

async function waitFinished(page, timeoutMs) {
  const started = Date.now();
  let nextLog = 0;

  while (Date.now() - started < timeoutMs) {
    const download = page.getByRole("button", { name: /^Download$/i }).first();
    if (await download.isVisible().catch(() => false)) return;

    const body = await page.locator("body").innerText().catch(() => "");
    if (/failed|error|try again|could not transcribe/i.test(body)) {
      throw new Error("O site mostrou erro durante a transcrição.");
    }

    if (Date.now() >= nextLog) {
      const pct = body.match(/\\b(\\d{1,3})%\\b/)?.[1];
      console.log("    aguardando" + (pct ? " — " + pct + "%" : "") + "...");
      nextLog = Date.now() + 30000;
    }

    await page.waitForTimeout(5000);
  }

  throw new Error("Timeout esperando a transcrição.");
}

async function formatLocator(page, format) {
  const patterns = {
    txt: /^(TXT|Text)$/i,
    srt: /^SRT$/i,
    docx: /^(DOCX|Word)$/i,
    pdf: /^PDF$/i
  };

  const button = page.getByRole("button", { name: patterns[format] }).first();
  if (await button.isVisible().catch(() => false)) return button;

  const text = page.getByText(patterns[format]).first();
  if (await text.isVisible().catch(() => false)) return text;

  return null;
}

async function downloadTranscript(page, cfg, inputFile) {
  let downloadButton = page.getByRole("button", { name: /^Download$/i }).first();
  if (!(await downloadButton.isVisible().catch(() => false))) {
    downloadButton = page.getByText(/^Download$/i).first();
  }
  await downloadButton.waitFor({ state: "visible", timeout: 60000 });

  let download = null;

  try {
    const event = page.waitForEvent("download", { timeout: 5000 });
    await downloadButton.click();
    download = await event;
  } catch {
    // O primeiro clique normalmente abre o seletor de formato.
    const formatChoice = await formatLocator(page, cfg.format);
    if (!formatChoice) {
      throw new Error("Download abriu, mas não encontrei a opção " + cfg.format.toUpperCase() + ".");
    }

    const event = page.waitForEvent("download", { timeout: 30000 });
    await formatChoice.click();
    download = await event;
  }

  const suggested = download.suggestedFilename();
  const ext = path.extname(suggested) || ("." + cfg.format);
  const base = path.basename(inputFile, path.extname(inputFile));
  const destination = path.join(cfg.output, base + ext.toLowerCase());

  await download.saveAs(destination);
  return destination;
}

async function main() {
  const cfg = parseArgs(process.argv);

  await fsp.mkdir(cfg.output, { recursive: true });
  await fsp.mkdir(cfg.profile, { recursive: true });

  const evidenceDir = path.join(process.cwd(), "evidence", "1transcribe-batch", stamp());
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

  console.log("Encontrados:", files.length, "arquivos");
  console.log("Entrada:", cfg.input);
  console.log("Saída:", cfg.output);
  console.log("Nesta execução:", cfg.limit === 0 ? "todos os pendentes" : cfg.limit);

  const context = await chromium.launchPersistentContext(cfg.profile, {
    headless: cfg.headless,
    acceptDownloads: true,
    viewport: { width: 1440, height: 1000 }
  });

  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(60000);

  let processed = 0;
  let failures = 0;
  const log = [];

  try {
    await ensureLogin(page);

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
      console.log("[" + processed + "] " + item.name);

      try {
        await openImport(page);
        console.log("    upload...");
        await upload(page, item.full);

        console.log("    transcrevendo...");
        await waitFinished(page, cfg.timeoutMinutes * 60 * 1000);

        console.log("    download...");
        const output = await downloadTranscript(page, cfg, item.full);

        state.completed[hash] = {
          input: item.full,
          output,
          size: item.size,
          completedAt: new Date().toISOString()
        };
        await saveState(stateFile, state);

        log.push({ input: item.full, output, status: "ok" });
        console.log("    OK:", output);
      } catch (err) {
        failures++;
        log.push({ input: item.full, status: "failed", error: err?.stack || String(err) });
        console.error("    ERRO:", err?.message || err);
        await saveEvidence(page, evidenceDir, "failed-" + String(processed).padStart(3, "0"));
        await page.goto(HOME_URL, { waitUntil: "domcontentloaded", timeout: 120000 }).catch(() => {});
      }

      await fsp.writeFile(
        path.join(evidenceDir, "run.json"),
        JSON.stringify({ config: cfg, processed, failures, log }, null, 2) + "\\n",
        "utf8"
      );
    }
  } finally {
    await context.close();
  }

  await fsp.writeFile(
    path.join(evidenceDir, "report.md"),
    [
      "# 1Transcribe batch",
      "",
      "- Input: " + cfg.input,
      "- Output: " + cfg.output,
      "- Processados: " + processed,
      "- Falhas: " + failures,
      "- Formato: " + cfg.format,
      ""
    ].join("\\n"),
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
