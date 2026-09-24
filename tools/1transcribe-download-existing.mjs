#!/usr/bin/env node

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const target = fileURLToPath(
  new URL("./1transcribe-current-chrome.mjs", import.meta.url)
);

const defaultOutput =
  "/home/esteban/Sync/Projects/my-transcriver/protegendo-a-torre-1transcribe-existentes";

const args = [
  target,
  "--download-existing",
  "--all",
  "--output",
  defaultOutput,
  ...process.argv.slice(2),
];

const child = spawn(process.execPath, args, {
  stdio: "inherit",
});

child.on("exit", code => {
  process.exit(code ?? 1);
});

child.on("error", err => {
  console.error(err?.stack || err);
  process.exit(1);
});
