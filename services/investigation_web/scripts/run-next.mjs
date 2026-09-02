import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const mode = process.argv[2];
const rawAgentUrl = process.env.INVESTIGATION_AGENT_URL;

try {
  if (!rawAgentUrl) throw new Error("INVESTIGATION_AGENT_URL is required");
  const agentUrl = new URL(rawAgentUrl);
  if (agentUrl.protocol !== "http:" && agentUrl.protocol !== "https:") {
    throw new Error("INVESTIGATION_AGENT_URL must use HTTP or HTTPS");
  }
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : "Invalid INVESTIGATION_AGENT_URL"}\n`);
  process.exit(2);
}

if (mode !== "dev" && mode !== "start") {
  process.stderr.write("Expected Next.js mode: dev or start\n");
  process.exit(2);
}

const nextBin = fileURLToPath(new URL("../node_modules/next/dist/bin/next", import.meta.url));
const child = spawn(process.execPath, [nextBin, mode, ...process.argv.slice(3)], {
  env: process.env,
  stdio: "inherit",
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}

child.on("error", (error) => {
  process.stderr.write(`Could not start Next.js: ${error.message}\n`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 1);
});
