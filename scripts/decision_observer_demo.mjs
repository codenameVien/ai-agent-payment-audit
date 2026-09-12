// Dedicated preview: isolated temporary Mongo, no .env.local or wallet keys.
// Qwen runs locally; AA, Provider, settlement and Anchor are explicitly Mock.
import { spawn } from "node:child_process";
import { startAegisStack } from "./aegis_local_stack.mjs";

const stack = await startAegisStack({
  priorityClassifier: "local-qwen", observerMode: "local-qwen", checkpointMode: "mock",
});
const dashboard = spawn(process.execPath, ["node_modules/next/dist/bin/next", "dev", "apps/dashboard", "--hostname", "127.0.0.1", "--port", "3100"], {
  env: { PATH: process.env.PATH, HOME: process.env.HOME, API_ORIGIN: stack.evidenceUrl },
  stdio: "inherit",
});
let stopping = false;
async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  dashboard.kill("SIGTERM");
  await stack.stop();
  process.exit(code);
}
dashboard.on("error", () => void stop(1));
dashboard.on("exit", (code) => void stop(code ?? 0));
process.on("SIGINT", () => void stop());
process.on("SIGTERM", () => void stop());
console.log(JSON.stringify({ preview: "http://127.0.0.1:3100/request", evidenceApi: stack.evidenceUrl,
  observer: "local-qwen", settlement: "mock", checkpoint: "mock", storage: "temporary; removed on exit" }));
