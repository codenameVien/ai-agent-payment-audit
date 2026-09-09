#!/usr/bin/env node
// aa-three-factor-v1 로컬 스택: 증거 API + Mock Provider Gateway 3개 + Mock Facilitator + 결제 실행 모듈.
//
// 안전 규칙
//   * 기존 .env / .env.local / 상속된 MONGODB_* 를 읽지 않는다. 자식 프로세스는 최소한의
//     깨끗한 환경과 이 실행이 생성한 임시 비밀값만 받는다.
//   * mongod 는 이 실행이 만든 임시 dbpath 에서만 기동하고, 종료 시 자기 자식만 정리한다.
//     사용자의 기존 MongoDB 와 기존 기록은 열지도 쓰지도 않는다.
//   * 서명 키는 실행마다 새로 생성하는 임시 테스트 키이며 결제 실행 모듈 프로세스에만
//     전달된다. 디스크에 저장하지 않고 로그에도 남기지 않는다.
//   * 실제 AA/Provider/체인/AWS 호출은 없다. Provider 와 Facilitator 는 모두 Mock 이다.

import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OWNER_STAMP = ".pbl-aegis-stack-owner";
const OWNED_DIR_PREFIX = "pbl-aegis-stack.";
// The database name is fixed and lives only inside this run's own temporary mongod, so
// there is no option through which a caller could point this stack at an existing store.
const DATABASE = "pbl_aegis_local";
const REPLICA_SET = "aegisrs";
// Storage is deliberately absent from this list: this run always owns its own mongod.
const ALLOWED_OPTIONS = new Set([
  "perTransactionLimitUnits",
  "dailyLimitUnits",
  // Abnormal-evidence scenarios point the fixture capture at other shipped fixtures.
  "modelCatalogPath",
  "aaFixturePages",
  // Real outbound tripwire: every child is started with the guard preloaded.
  "outboundLogPath",
]);
const PROVIDERS = ["openai", "anthropic", "google"];
const NETWORK = "eip155:84532";
const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
const LOCAL_OWNER = "0x00000000000000000000000000000000000a6e15";

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

function base64Key() {
  return randomBytes(32).toString("base64");
}

function token() {
  return randomBytes(24).toString("hex");
}

/** An OS-assigned free loopback port. Occupied ports are never reused or displaced. */
function freePort() {
  return new Promise((done, fail) => {
    const probe = createServer();
    probe.unref();
    probe.on("error", fail);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => done(port));
    });
  });
}

/** A deliberately minimal child environment: nothing about the developer's setup leaks in. */
function baseEnv(options = {}) {
  const parent = process.env;
  const env = {
    PATH: parent.PATH ?? "/usr/bin:/bin",
    HOME: parent.HOME ?? "",
    TMPDIR: parent.TMPDIR ?? "/tmp",
    LANG: parent.LANG ?? "en_US.UTF-8",
  };
  if (options.outboundLogPath !== undefined) {
    env.AEGIS_OUTBOUND_LOG = options.outboundLogPath;
    env.NODE_OPTIONS = `--require ${join(REPO_ROOT, "scripts/aegis_outbound_guard.cjs")}`;
  }
  return env;
}

class ChildProcesses {
  #children = [];

  spawn(name, command, args, env, cwd = REPO_ROOT) {
    const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    const label = `[${name}]`;
    child.stdout.on("data", (chunk) => process.stderr.write(`${label} ${chunk}`));
    child.stderr.on("data", (chunk) => process.stderr.write(`${label} ${chunk}`));
    child.on("exit", (code, signal) => {
      if (!child.stopping && code !== 0) {
        process.stderr.write(`${label} exited unexpectedly: code=${code} signal=${signal}\n`);
      }
    });
    this.#children.push({ name, child });
    return child;
  }

  #allExited() {
    return this.#children.every(
      ({ child }) => child.exitCode !== null || child.signalCode !== null,
    );
  }

  /**
   * Stop every child this run started and report whether they are all really gone.
   *
   * The answer gates the temporary dbpath removal: deleting the data directory of a
   * mongod that might still be running would be exactly the kind of destructive guess
   * this runner refuses to make.
   */
  async stopAll() {
    for (const { child } of this.#children.slice().reverse()) {
      if (child.exitCode !== null || child.signalCode !== null) continue;
      child.stopping = true;
      child.kill("SIGTERM");
    }
    for (let attempt = 0; attempt < 40 && !this.#allExited(); attempt += 1) {
      await sleep(100);
    }
    if (this.#allExited()) return true;
    for (const { child } of this.#children) {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }
    // SIGKILL is not instantaneous: wait for the actual exit before claiming ownership
    // of the files those children were writing.
    for (let attempt = 0; attempt < 50 && !this.#allExited(); attempt += 1) {
      await sleep(100);
    }
    return this.#allExited();
  }
}

async function waitForHttp(url, { tries = 200, delayMs = 150, name }) {
  for (let attempt = 0; attempt < tries; attempt += 1) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
    } catch {
      // not up yet
    }
    await sleep(delayMs);
  }
  throw new Error(`${name} did not become healthy at ${url}`);
}

/** Run one mongosh evaluation against the loopback port and return its stdout. */
function mongoshEval(port, script) {
  return new Promise((done) => {
    const child = spawn(
      "mongosh",
      ["--quiet", "--host", "127.0.0.1", "--port", String(port), "--eval", script],
      { env: baseEnv(), stdio: ["ignore", "pipe", "ignore"] },
    );
    let output = "";
    child.stdout.on("data", (chunk) => {
      output += String(chunk);
    });
    child.on("error", () => done({ code: -1, output: "" }));
    child.on("exit", (code) => done({ code, output }));
  });
}

/** The identity a server reported about itself, or `null` while it is not answering yet. */
export function parseServerIdentity(output) {
  const match = /\{[\s\S]*\}/.exec(output ?? "");
  if (match === null) return null;
  let value;
  try {
    value = JSON.parse(match[0]);
  } catch {
    return null;
  }
  const pid = Number(value?.pid);
  if (!Number.isSafeInteger(pid) || pid <= 0 || typeof value?.dbPath !== "string") return null;
  return { pid, dbPath: value.dbPath };
}

/**
 * Refuse to touch a server this run did not start.
 *
 * A freed probe port can be taken by somebody else between the probe and the spawn, so a
 * server answering on "our" port is not proof of ownership. Only a reported PID equal to
 * the spawned child and a dbPath equal to the temporary directory this run created are.
 */
export function assertOwnedServer(identity, expected) {
  if (identity.pid !== expected.pid || identity.dbPath !== expected.dbPath) {
    throw new Error(
      `port ${expected.port} is answered by a mongod this run does not own ` +
        `(pid ${identity.pid}, dbpath ${identity.dbPath}); refusing to touch it`,
    );
  }
}

async function waitForOwnedServer({ port, child, dbPath }) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error("the isolated mongod exited before it became ready");
    }
    const { output } = await mongoshEval(
      port,
      "JSON.stringify({pid: db.serverStatus().pid.toString(), " +
        "dbPath: db.adminCommand({getCmdLineOpts: 1}).parsed.storage.dbPath})",
    );
    const identity = parseServerIdentity(output);
    if (identity !== null) {
      // Ownership is proven before any state-changing command is sent.
      assertOwnedServer(identity, { pid: child.pid, dbPath, port });
      return;
    }
    await sleep(250);
  }
  throw new Error("the isolated mongod did not answer in time");
}

async function startOwnedMongo(children, owned) {
  const dbPath = await mkdtemp(join(tmpdir(), OWNED_DIR_PREFIX));
  // Registered before anything else can fail, so a later error still reclaims it.
  owned.dbPath = dbPath;
  await writeFile(join(dbPath, OWNER_STAMP), `${process.pid}\n`, "utf8");
  const port = await freePort();
  const child = children.spawn(
    "mongod",
    "mongod",
    [
      "--dbpath", dbPath,
      "--port", String(port),
      "--bind_ip", "127.0.0.1",
      "--replSet", REPLICA_SET,
      "--nounixsocket",
      "--logpath", join(dbPath, "mongod.log"),
    ],
    baseEnv(),
  );
  await waitForOwnedServer({ port, child, dbPath });
  await mongoshEval(
    port,
    `try { rs.initiate({_id:'${REPLICA_SET}',members:[{_id:0,host:'127.0.0.1:${port}'}]}) } ` +
      "catch (error) { }",
  );
  for (let attempt = 0; attempt < 240; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error("the isolated mongod exited before it became primary");
    }
    const { output } = await mongoshEval(port, "print(db.hello().isWritablePrimary)");
    if (output.includes("true")) {
      return {
        uri: `mongodb://127.0.0.1:${port}/?replicaSet=${REPLICA_SET}&directConnection=true`,
      };
    }
    await sleep(250);
  }
  throw new Error("the isolated mongod did not become a writable primary");
}

/** Delete only a directory this run created and stamped, and only after its child exited. */
export async function removeOwnedDir(dbPath) {
  if (typeof dbPath !== "string" || !dbPath.includes(`/${OWNED_DIR_PREFIX}`)) return false;
  if (!existsSync(join(dbPath, OWNER_STAMP))) return false;
  await rm(dbPath, { recursive: true, force: true });
  return true;
}

/**
 * Stop the children, then reclaim the temporary dbpath only if they are confirmed gone.
 *
 * An unconfirmed exit leaves the directory in place and says so: a leftover temporary
 * directory is a cleanup annoyance, while deleting a live mongod's dbpath is data loss.
 */
export async function reclaim(children, owned) {
  const exited = await children.stopAll();
  if (!exited) {
    process.stderr.write(
      `[stack] a child did not confirm exit; preserving ${owned.dbPath ?? "(none)"}\n`,
    );
    return false;
  }
  return removeOwnedDir(owned.dbPath);
}

/**
 * Start the whole aa-three-factor-v1 composition and return its handles.
 *
 * Storage is not configurable. This run always creates, owns and later removes its own
 * temporary replica-set mongod, so there is no option through which an existing database
 * could be opened, indexed or written to.
 *
 * Every secret is generated here for this run only. The signing key is generated in this
 * process and injected into the payment executor child alone; no other child, and no
 * log line, ever receives it.
 */
export async function startAegisStack(options = {}) {
  const unknown = Object.keys(options).filter((key) => !ALLOWED_OPTIONS.has(key));
  if (unknown.length > 0) {
    throw new Error(
      `unsupported stack option(s): ${unknown.join(", ")}; this runner owns its own mongod`,
    );
  }
  // The storage this run owns and the services that talk to it are stopped separately,
  // so a restart can replace every service against the same durable store.
  const infra = new ChildProcesses();
  let services = new ChildProcesses();
  const owned = { dbPath: null };
  try {
    if (!existsSync(join(REPO_ROOT, "services/commerce-gateway/dist/src/aegis/main.js"))) {
      throw new Error(
        "build the runtime first: npm run build --workspace @pbl/commerce-gateway",
      );
    }
    const mongo = await startOwnedMongo(infra, owned);

    const secrets = {
      internalServiceToken: token(),
      adminServiceToken: token(),
      gatewayServiceToken: token(),
      sessionSecret: base64Key(),
      payloadMasterKey: base64Key(),
      // Ephemeral test key, generated per run. No real wallet key is ever used here.
      signerPrivateKey: `0x${randomBytes(32).toString("hex")}`,
    };

    /** Start one full set of services against the store this run already owns. */
    async function startServices() {
    const ports = {
      evidence: await freePort(),
      facilitator: await freePort(),
      executor: await freePort(),
      openai: await freePort(),
      anthropic: await freePort(),
      google: await freePort(),
    };
    const evidenceUrl = `http://127.0.0.1:${ports.evidence}`;
    const facilitatorUrl = `http://127.0.0.1:${ports.facilitator}`;
    const executorUrl = `http://127.0.0.1:${ports.executor}`;
    const gatewayRoutes = Object.fromEntries(
      PROVIDERS.map((providerId) => [providerId, `http://127.0.0.1:${ports[providerId]}`]),
    );

    services.spawn(
      "evidence-api",
      "uv",
      [
        "run", "--project", "services/buyer-audit-api",
        "uvicorn", "buyer_audit_api.main:app",
        "--app-dir", "services/buyer-audit-api/src",
        "--host", "127.0.0.1", "--port", String(ports.evidence),
      ],
      {
        ...baseEnv(),
        MONGODB_URI: mongo.uri,
        MONGODB_DATABASE: DATABASE,
        SESSION_SECRET_BASE64: secrets.sessionSecret,
        PAYLOAD_MASTER_KEY_BASE64: secrets.payloadMasterKey,
        INTERNAL_SERVICE_TOKEN: secrets.internalServiceToken,
        ADMIN_SERVICE_TOKEN: secrets.adminServiceToken,
        GATEWAY_SERVICE_TOKEN: secrets.gatewayServiceToken,
        COMMERCE_GATEWAY_URL: executorUrl,
        AEGIS_LOCAL_OWNER_ADDRESS: LOCAL_OWNER,
        AEGIS_TOKEN_ADDRESS: ZERO_ADDRESS,
        AEGIS_TOKEN_STATUS: "prepared",
        AEGIS_EXECUTION_MODE: "mock",
        ...(options.modelCatalogPath === undefined
          ? {}
          : { AEGIS_MODEL_CATALOG_PATH: options.modelCatalogPath }),
        ...(options.aaFixturePages === undefined
          ? {}
          : { AA_FIXTURE_PAGES_JSON: JSON.stringify(options.aaFixturePages) }),
      },
    );

    services.spawn(
      "facilitator",
      process.execPath,
      ["services/commerce-gateway/dist/src/aegis/main.js", "facilitator"],
      { ...baseEnv(options), PORT: String(ports.facilitator) },
    );

    for (const providerId of PROVIDERS) {
      services.spawn(
        `gateway-${providerId}`,
        process.execPath,
        ["services/commerce-gateway/dist/src/aegis/main.js", "provider-gateway"],
        {
          ...baseEnv(options),
          PORT: String(ports[providerId]),
          AEGIS_PROVIDER_ID: providerId,
          AEGIS_NETWORK: NETWORK,
          AEGIS_FACILITATOR_URL: facilitatorUrl,
          EVIDENCE_API_URL: evidenceUrl,
          INTERNAL_SERVICE_TOKEN: secrets.internalServiceToken,
        },
      );
    }

    services.spawn(
      "payment-executor",
      process.execPath,
      ["services/commerce-gateway/dist/src/aegis/main.js", "payment-executor"],
      {
        ...baseEnv(options),
        PORT: String(ports.executor),
        AEGIS_NETWORK: NETWORK,
        AEGIS_GATEWAY_ROUTES_JSON: JSON.stringify(gatewayRoutes),
        AEGIS_SIGNER_PRIVATE_KEY: secrets.signerPrivateKey,
        EVIDENCE_API_URL: evidenceUrl,
        INTERNAL_SERVICE_TOKEN: secrets.internalServiceToken,
        GATEWAY_SERVICE_TOKEN: secrets.gatewayServiceToken,
      },
    );

    await waitForHttp(`${evidenceUrl}/health`, { name: "evidence API" });
    await waitForHttp(`${facilitatorUrl}/health`, { name: "mock facilitator" });
    await waitForHttp(`${executorUrl}/health`, { name: "payment executor" });
    for (const providerId of PROVIDERS) {
      await waitForHttp(`${gatewayRoutes[providerId]}/health`, {
        name: `${providerId} provider gateway`,
      });
    }

    // The address is read back from the executor rather than derived here, so the key
    // stays confined to the child that has to sign with it.
    const addressResponse = await fetch(`${executorUrl}/address`, {
      headers: { authorization: `Bearer ${secrets.gatewayServiceToken}` },
    });
    if (!addressResponse.ok) throw new Error("payment executor did not report its address");
    const { buyerWalletAddress } = await addressResponse.json();

    const bound = await fetch(`${evidenceUrl}/internal/auth/buyer-wallet`, {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${secrets.adminServiceToken}`,
      },
      body: JSON.stringify({
        owner_address: LOCAL_OWNER,
        buyer_wallet_address: buyerWalletAddress,
      }),
    });
    if (!bound.ok) throw new Error(`binding the buyer wallet failed: ${await bound.text()}`);

    const policy = await fetch(`${evidenceUrl}/internal/evidence/wallet-policies`, {
      method: "PUT",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${secrets.internalServiceToken}`,
      },
      body: JSON.stringify({
        buyer_wallet_address: buyerWalletAddress,
        policy_date: new Date().toISOString().slice(0, 10),
        token: ZERO_ADDRESS,
        per_transaction_limit_units: options.perTransactionLimitUnits ?? 100_000,
        daily_limit_units: options.dailyLimitUnits ?? 1_000_000,
      }),
    });
    if (!policy.ok) {
      const detail = await policy.text();
      // After a restart the same policy is already in place and may hold reservations,
      // which the Evidence API refuses to replace. That is the correct answer, not a
      // startup failure, so only an unexpected refusal aborts.
      if (!detail.includes("active wallet policy cannot be replaced")) {
        throw new Error(`configuring the wallet policy failed: ${detail}`);
      }
    }

      return {
        evidenceUrl,
        facilitatorUrl,
        executorUrl,
        gatewayRoutes,
        buyerWalletAddress,
      };
    }

    const handle = (started) => ({
      ...started,
      ownerAddress: LOCAL_OWNER,
      internalServiceToken: secrets.internalServiceToken,
      mongodbUri: mongo.uri,
      /**
       * Replace every service process while keeping the same durable store.
       *
       * This is what makes restart evidence real: nothing in memory survives, so what
       * the new processes can still see came out of MongoDB.
       */
      async restart() {
        await services.stopAll();
        services = new ChildProcesses();
        return handle(await startServices());
      },
      async stop() {
        await services.stopAll();
        // Only after every child has really exited, and only this run's stamped directory.
        await reclaim(infra, owned);
      },
    });
    return handle(await startServices());
  } catch (error) {
    await services.stopAll();
    await reclaim(infra, owned);
    throw error;
  }
}

if (process.argv[1] && process.argv[1].endsWith("aegis_local_stack.mjs")) {
  const stack = await startAegisStack();
  process.stderr.write(
    [
      "aa-three-factor-v1 local stack is up (Mock Provider / Mock Facilitator).",
      `  evidence API      ${stack.evidenceUrl}`,
      `  payment executor  ${stack.executorUrl}`,
      `  mock facilitator  ${stack.facilitatorUrl}`,
      ...Object.entries(stack.gatewayRoutes).map(
        ([providerId, url]) => `  gateway ${providerId.padEnd(9)} ${url}`,
      ),
      `  buyer wallet      ${stack.buyerWalletAddress} (ephemeral test key)`,
      "Ctrl-C to stop; the isolated mongod and its temporary dbpath are removed on exit.",
      "",
    ].join("\n"),
  );
  const shutdown = async () => {
    await stack.stop();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown());
  process.on("SIGTERM", () => void shutdown());
}
