#!/usr/bin/env node
// Launcher for telegram-to-agent-skill-cli: finds (or offers to install)
// uv, installs the Python package UNPINNED, then hands over to `tg setup`.
// Zero dependencies, no postinstall hooks.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";

const PACKAGE = "telegram-to-agent-skill-cli";

function findUv() {
  const probe = spawnSync("uv", ["--version"], { stdio: "ignore" });
  if (!probe.error) return "uv";
  for (const candidate of [
    join(homedir(), ".local", "bin", "uv"),
    join(homedir(), ".cargo", "bin", "uv"),
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

async function confirm(question) {
  if (!process.stdin.isTTY) return false;
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  const answer = await new Promise((resolve) =>
    rl.question(`${question} [y/N] `, resolve)
  );
  rl.close();
  return /^y(es)?$/i.test(answer.trim());
}

async function main() {
  if (process.platform === "win32") {
    console.error(
      "Windows: install uv first (PowerShell):\n" +
        '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"\n' +
        `then run: uv tool install ${PACKAGE} && tg setup\n` +
        "(WSL works out of the box: run this launcher inside WSL.)"
    );
    process.exit(1);
  }

  let uv = findUv();
  if (!uv) {
    const installCmd = "curl -LsSf https://astral.sh/uv/install.sh | sh";
    console.error(`uv is required. Install command:\n  ${installCmd}`);
    const yes = await confirm("Run it now?");
    if (!yes) process.exit(1);
    const res = spawnSync("sh", ["-c", installCmd], { stdio: "inherit" });
    if (res.status !== 0) process.exit(res.status ?? 1);
    uv = findUv();
    if (!uv) {
      console.error("uv installed but not found; open a new terminal and retry.");
      process.exit(1);
    }
  }

  // Unpinned install: `tg update` must keep working forever.
  const install = spawnSync(uv, ["tool", "install", PACKAGE], {
    stdio: "inherit",
  });
  if (install.status !== 0) process.exit(install.status ?? 1);

  const toolDir = spawnSync(uv, ["tool", "dir"], { encoding: "utf-8" });
  const tgBin = join(toolDir.stdout.trim(), PACKAGE, "bin", "tg");
  if (!existsSync(tgBin)) {
    console.error(`tg entrypoint not found at ${tgBin}`);
    process.exit(1);
  }

  const which = spawnSync("sh", ["-c", "command -v tg"], { stdio: "ignore" });
  if (which.status !== 0) {
    console.error(
      "Note: uv's tool bin is not in PATH; run `uv tool update-shell` and reopen the terminal."
    );
  }

  const args = process.argv.slice(2);
  const setup = spawnSync(tgBin, ["setup", ...args], { stdio: "inherit" });
  process.exit(setup.status ?? 0);
}

main();
