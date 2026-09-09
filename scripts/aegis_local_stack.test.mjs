#!/usr/bin/env node
// 로컬 스택 runner 의 소유권 경계 회귀 테스트.
//
// mongod 를 기동하지 않고 순수 판정 함수만 검증한다. 이 테스트는 사용자의 MongoDB 에
// 접속하지 않으며, 생성한 임시 디렉터리도 자기가 만든 것만 삭제한다.

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  assertOwnedServer,
  parseServerIdentity,
  removeOwnedDir,
  startAegisStack,
} from "./aegis_local_stack.mjs";

const OWNER_STAMP = ".pbl-aegis-stack-owner";

test("a server identity is only accepted once it is fully reported", () => {
  assert.equal(parseServerIdentity(""), null);
  assert.equal(parseServerIdentity("MongoNetworkError: connect ECONNREFUSED"), null);
  assert.equal(parseServerIdentity('{"pid":"123"}'), null);
  assert.equal(parseServerIdentity('{"dbPath":"/tmp/x"}'), null);
  assert.equal(parseServerIdentity('{"pid":"0","dbPath":"/tmp/x"}'), null);
  assert.deepEqual(parseServerIdentity('{"pid":"4242","dbPath":"/tmp/owned"}'), {
    pid: 4242,
    dbPath: "/tmp/owned",
  });
});

test("a mongod this run did not spawn is never mutated", () => {
  const expected = { pid: 4242, dbPath: "/tmp/owned", port: 27017 };
  assert.doesNotThrow(() =>
    assertOwnedServer({ pid: 4242, dbPath: "/tmp/owned" }, expected),
  );
  // Somebody else's mongod took the probed port between the probe and the spawn.
  assert.throws(
    () => assertOwnedServer({ pid: 777, dbPath: "/tmp/owned" }, expected),
    /does not own/,
  );
  // Same PID reported, but a different data directory: still not ours.
  assert.throws(
    () => assertOwnedServer({ pid: 4242, dbPath: "/data/db" }, expected),
    /does not own/,
  );
});

test("cleanup removes only a directory this run created and stamped", async () => {
  const foreign = await mkdtemp(join(tmpdir(), "pbl-aegis-foreign."));
  try {
    assert.equal(await removeOwnedDir(foreign), false);
    assert.ok(existsSync(foreign));
    assert.equal(await removeOwnedDir("/"), false);
    assert.equal(await removeOwnedDir(undefined), false);
  } finally {
    await rm(foreign, { recursive: true, force: true });
  }

  const unstamped = await mkdtemp(join(tmpdir(), "pbl-aegis-stack."));
  try {
    assert.equal(await removeOwnedDir(unstamped), false);
    assert.ok(existsSync(unstamped));
  } finally {
    await rm(unstamped, { recursive: true, force: true });
  }

  const owned = await mkdtemp(join(tmpdir(), "pbl-aegis-stack."));
  await writeFile(join(owned, OWNER_STAMP), `${process.pid}\n`, "utf8");
  assert.equal(await removeOwnedDir(owned), true);
  assert.equal(existsSync(owned), false);
});

test("the stack cannot be pointed at an existing database", async () => {
  await assert.rejects(
    startAegisStack({ mongodbUri: "mongodb://127.0.0.1:27017/?directConnection=true" }),
    /unsupported stack option/,
  );
  await assert.rejects(startAegisStack({ database: "pbl_audit" }), /unsupported stack option/);
});
