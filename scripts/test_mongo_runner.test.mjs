// scripts/test_mongo_local.sh 안전성 테스트.
//
// 실제 mongod / mongosh / uv 를 쓰지 않는다. PATH 앞에 스텁 실행파일을 깔아
// 러너가 "무엇을 실행했고 무엇을 건드리지 않았는지"를 로그로 관찰한다.
// 검증 대상은 소유권이다: 남의 포트/서버/프로세스/디렉터리를 건드리지 않고,
// 자기가 만든 자식 PID 와 임시 dbpath 만 정리하는가.
import assert from "node:assert/strict";
import net from "node:net";
import path from "node:path";
import test from "node:test";
import { once } from "node:events";
import { spawn, spawnSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const SCRIPT = fileURLToPath(new URL("./test_mongo_local.sh", import.meta.url));

/** 러너가 지우면 안 되는 이웃 임시 디렉터리(이름 규칙은 같지만 소유 도장이 없다). */
const DECOY_DIR = "pbl-mongo-test.dec001";

/** 상속돼도 pytest 로 새어 나가면 안 되는 실서버 URL. */
const LIVE_URI = "mongodb://live.example.invalid:27017/must-not-leak";

const MONGOD_ALIVE = `
printf 'mongod %s\\n' "$*" >> "$STUB_LOG"
printf '%s\\n' "$$" > "$STUB_SERVER_PID"
exec sleep 30
`;

const MONGOD_DIES = `
printf 'mongod %s\\n' "$*" >> "$STUB_LOG"
printf '%s\\n' "$$" > "$STUB_SERVER_PID"
log_path=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--logpath" ]; then log_path="$arg"; fi
  prev="$arg"
done
if [ -n "$log_path" ]; then printf 'FATAL-STUB: dbpath is locked\\n' > "$log_path"; fi
exit 1
`;

/** 자기가 띄운 서버라고 정직하게 보고하는 mongosh. */
const MONGOSH_TRUTHFUL = `
printf 'mongosh %s\\n' "$*" >> "$STUB_LOG"
eval_arg=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--eval" ]; then eval_arg="$2"; shift; shift; continue; fi
  shift
done
case "$eval_arg" in
  *serverStatus*)
    if [ -f "$STUB_SERVER_PID" ]; then cat "$STUB_SERVER_PID"; else exit 1; fi ;;
  *rs.initiate*) printf '%s\\n' '{ ok: 1 }' ;;
  *isWritablePrimary*) printf '%s\\n' 'true' ;;
  *) printf '%s\\n' 'null' ;;
esac
`;

/** 포트에 이미 있던 남의 서버처럼 다른 PID 를 보고하는 mongosh. */
const MONGOSH_FOREIGN = MONGOSH_TRUTHFUL.replace(
  'if [ -f "$STUB_SERVER_PID" ]; then cat "$STUB_SERVER_PID"; else exit 1; fi',
  'printf \'%s\\n\' "$STUB_FOREIGN_PID"',
);

const UV_STUB = `
printf 'uv %s\\n' "$*" >> "$STUB_LOG"
printf 'uv-env TEST_MONGODB_URI=%s MONGODB_URI=%s\\n' \\
  "\${TEST_MONGODB_URI:-<unset>}" "\${MONGODB_URI:-<unset>}" >> "$STUB_LOG"
if [ -n "\${STUB_UV_HANG:-}" ]; then exec sleep 30; fi
exit "\${STUB_UV_EXIT:-0}"
`;

function makeSandbox(t, stubs) {
  const root = mkdtempSync(path.join(tmpdir(), "mongo-runner-case."));
  const bin = path.join(root, "bin");
  const tmp = path.join(root, "tmp");
  const log = path.join(root, "calls.log");
  const serverPidFile = path.join(root, "server.pid");
  const decoyDir = path.join(tmp, DECOY_DIR);
  mkdirSync(bin);
  mkdirSync(tmp);
  mkdirSync(decoyDir);
  writeFileSync(log, "");
  writeFileSync(path.join(decoyDir, "keep.txt"), "다른 실행의 자료\n");

  for (const [name, body] of Object.entries(stubs)) {
    const file = path.join(bin, name);
    writeFileSync(file, `#!/usr/bin/env bash\n${body}\n`);
    chmodSync(file, 0o755);
  }

  const sandbox = { root, bin, tmp, log, serverPidFile, decoyDir };
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return sandbox;
}

function envFor(sandbox, extra = {}) {
  return {
    PATH: `${sandbox.bin}:${process.env.PATH}`,
    HOME: process.env.HOME ?? sandbox.root,
    TMPDIR: sandbox.tmp,
    STUB_LOG: sandbox.log,
    STUB_SERVER_PID: sandbox.serverPidFile,
    MONGODB_URI: LIVE_URI,
    ...extra,
  };
}

function runScript(sandbox, extra = {}) {
  return spawnSync("bash", [SCRIPT], {
    cwd: sandbox.root,
    encoding: "utf8",
    timeout: 90_000,
    env: envFor(sandbox, extra),
  });
}

function readLog(sandbox) {
  return readFileSync(sandbox.log, "utf8");
}

function mongodArgs(sandbox) {
  const line = readLog(sandbox)
    .split("\n")
    .find((entry) => entry.startsWith("mongod "));
  assert.ok(line, "mongod 스텁이 실행되지 않았다");
  return line;
}

function flagValue(argLine, flag) {
  const match = argLine.match(new RegExp(`${flag} (\\S+)`));
  assert.ok(match, `${flag} 인자가 없다: ${argLine}`);
  return match[1];
}

function ownedPid(sandbox) {
  return Number(readFileSync(sandbox.serverPidFile, "utf8").trim());
}

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function spawnDecoyProcess(t) {
  const decoy = spawn("sleep", ["30"], { stdio: "ignore" });
  t.after(() => decoy.kill("SIGKILL"));
  return decoy;
}

async function listenOn(t, port = 0) {
  const server = net.createServer(() => {});
  server.on("error", () => {});
  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  t.after(() => server.close());
  return server;
}

function connectOnce(port) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, "127.0.0.1");
    socket.on("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.on("error", reject);
  });
}

async function freePort(t) {
  const server = await listenOn(t);
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitUntil(predicate, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return false;
}

test("잘못된 TEST_MONGO_PORT 는 mongod 를 띄우기 전에 거부한다", (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });

  for (const value of ["", " ", "abc", "27019x", "-1", "0", "80", "65536", "123456"]) {
    const result = runScript(sandbox, { TEST_MONGO_PORT: value });
    assert.notEqual(result.status, 0, `'${value}' 가 통과했다`);
    assert.match(result.stderr, /TEST_MONGO_PORT/);
    assert.equal(readLog(sandbox), "", `'${value}' 에서 외부 명령이 실행됐다`);
    assert.equal(existsSync(sandbox.serverPidFile), false);
  }
});

test("이미 사용 중인 포트는 거부하고 그 서버를 건드리지 않는다", async (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });
  const server = await listenOn(t);
  const { port } = server.address();

  const result = runScript(sandbox, { TEST_MONGO_PORT: String(port) });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /이미 사용 중/);
  // mongod / mongosh / uv 중 무엇도 실행되지 않았다 = shutdown 명령 자체가 불가능.
  assert.equal(readLog(sandbox), "");
  assert.doesNotMatch(result.stderr, /shutdown/i);
  assert.equal(server.listening, true);
  assert.equal(await connectOnce(port), true);
});

test("mongod 기동 실패 시 이 실행이 만든 PID 와 경로만 정리한다", (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_DIES,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });
  const decoy = spawnDecoyProcess(t);

  const result = runScript(sandbox);

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /기동 직후 종료/);
  // 실패 원인 로그는 지우기 전에 보여준다.
  assert.match(result.stderr, /FATAL-STUB: dbpath is locked/);

  const dbpath = flagValue(mongodArgs(sandbox), "--dbpath");
  assert.ok(dbpath.startsWith(sandbox.tmp), `dbpath 가 TMPDIR 밖이다: ${dbpath}`);
  assert.equal(existsSync(dbpath), false, "소유한 임시 dbpath 가 남았다");

  assert.equal(existsSync(sandbox.decoyDir), true, "이웃 임시 디렉터리를 지웠다");
  assert.equal(existsSync(path.join(sandbox.decoyDir, "keep.txt")), true);
  assert.equal(isAlive(decoy.pid), true, "무관한 프로세스를 종료시켰다");
  assert.equal(isAlive(ownedPid(sandbox)), false, "자기 자식이 살아남았다");
  assert.doesNotMatch(readLog(sandbox), /shutdownServer/);
  assert.doesNotMatch(readLog(sandbox), /^uv /m);
});

test("포트에서 남의 서버가 응답하면 초기화도 pytest 도 하지 않고 멈춘다", (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_FOREIGN,
    uv: UV_STUB,
  });

  const result = runScript(sandbox, { STUB_FOREIGN_PID: "999999" });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /만들지 않은 서버/);
  const log = readLog(sandbox);
  assert.doesNotMatch(log, /rs\.initiate/, "남의 서버에 rs.initiate 를 보냈다");
  assert.doesNotMatch(log, /shutdownServer/, "남의 서버에 shutdown 을 보냈다");
  assert.doesNotMatch(log, /^uv /m, "검증 실패인데 pytest 를 돌렸다");
  assert.equal(isAlive(ownedPid(sandbox)), false);
  assert.equal(existsSync(flagValue(mongodArgs(sandbox), "--dbpath")), false);
});

test("pytest 실패 종료코드를 그대로 전달하고 소유 인스턴스 URI 만 넘긴다", (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });

  const result = runScript(sandbox, { STUB_UV_EXIT: "7" });

  assert.equal(result.status, 7);
  const log = readLog(sandbox);
  const port = flagValue(mongodArgs(sandbox), "--port");
  assert.match(
    log,
    new RegExp(
      `uv-env TEST_MONGODB_URI=mongodb://127\\.0\\.0\\.1:${port}/\\?replicaSet=pblrs&directConnection=true MONGODB_URI=<unset>`,
    ),
    `상속 URL 이 새거나 URI 가 다르다:\n${log}`,
  );
  assert.doesNotMatch(log, /live\.example\.invalid/);
  // 오래된 단일 파일이 아니라 tests 디렉터리 전체의 mongo 마커를 돌린다.
  assert.match(log, /^uv run --project services\/buyer-audit-api pytest services\/buyer-audit-api\/tests -m mongo$/m);
  assert.doesNotMatch(log, /test_mongo_repository\.py/);
});

test("정상 종료해도 소유한 자식과 임시 dbpath 만 정리한다", (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });
  const decoy = spawnDecoyProcess(t);

  const result = runScript(sandbox, { STUB_UV_EXIT: "0" });

  assert.equal(result.status, 0);
  const args = mongodArgs(sandbox);
  assert.match(args, /--bind_ip 127\.0\.0\.1/);
  assert.match(args, /--replSet pblrs/);
  const dbpath = flagValue(args, "--dbpath");
  assert.ok(dbpath.startsWith(sandbox.tmp));
  assert.equal(existsSync(dbpath), false);
  assert.equal(existsSync(sandbox.decoyDir), true);
  assert.equal(isAlive(decoy.pid), true);
  assert.equal(isAlive(ownedPid(sandbox)), false);
});

test("명시한 빈 포트는 그대로 쓰고 자동 포트는 빈 포트를 고른다", async (t) => {
  const explicit = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });
  const port = await freePort(t);

  const pinned = runScript(explicit, { TEST_MONGO_PORT: String(port) });
  assert.equal(pinned.status, 0);
  assert.equal(flagValue(mongodArgs(explicit), "--port"), String(port));

  const auto = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });
  const dynamic = runScript(auto);
  assert.equal(dynamic.status, 0);
  const chosen = Number(flagValue(mongodArgs(auto), "--port"));
  assert.ok(chosen >= 20000 && chosen < 29000, `자동 포트 범위 밖: ${chosen}`);
  await assert.rejects(connectOnce(chosen), "자동 포트가 이미 쓰이고 있었다");
});

test("SIGINT 를 받으면 130 으로 끝나고 소유 자원만 정리한다", async (t) => {
  const sandbox = makeSandbox(t, {
    mongod: MONGOD_ALIVE,
    mongosh: MONGOSH_TRUTHFUL,
    uv: UV_STUB,
  });
  const decoy = spawnDecoyProcess(t);

  const child = spawn("bash", [SCRIPT], {
    cwd: sandbox.root,
    detached: true,
    stdio: ["ignore", "ignore", "pipe"],
    env: envFor(sandbox, { STUB_UV_HANG: "1" }),
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  const started = await waitUntil(() => /^uv /m.test(readLog(sandbox)));
  assert.equal(started, true, `pytest 단계까지 못 갔다:\n${readLog(sandbox)}\n${stderr}`);

  // 터미널 Ctrl-C 와 동일하게 포그라운드 프로세스 그룹에 보낸다.
  process.kill(-child.pid, "SIGINT");
  const [code] = await once(child, "close");

  assert.equal(code, 130);
  assert.equal(existsSync(flagValue(mongodArgs(sandbox), "--dbpath")), false);
  assert.equal(isAlive(ownedPid(sandbox)), false);
  assert.equal(existsSync(sandbox.decoyDir), true);
  assert.equal(isAlive(decoy.pid), true);
});
