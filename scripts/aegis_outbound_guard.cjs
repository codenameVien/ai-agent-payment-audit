// 실제 outbound transport tripwire.
//
// P6-03의 boundary counter는 항상 0을 반환해 "외부 호출이 없었다"는 근거가 되지 못했다.
// 이 모듈은 실제로 사용되는 socket/DNS 경로를 감싸 loopback 이외의 시도를 거부하고
// 시도 건수를 기록한다. `node --require` 로 프로세스 시작 전에 주입한다.
//
//   AEGIS_OUTBOUND_LOG  시도 1건당 JSON 한 줄을 append 할 경로(선택)
//
// 허용: 127.0.0.0/8, ::1, localhost, unix socket 경로. 그 외는 즉시 예외로 막는다.

"use strict";

const dns = require("node:dns");
const fs = require("node:fs");
const net = require("node:net");

const LOOPBACK_NAMES = new Set(["localhost", "localhost.", "ip6-localhost"]);
const counters = { allowed: 0, rejected: 0, rejectedTargets: [] };

function isLoopback(host) {
  if (host === undefined || host === null || host === "") return true;
  const value = String(host).trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (LOOPBACK_NAMES.has(value)) return true;
  if (value === "::1" || value === "0:0:0:0:0:0:0:1") return true;
  if (value === "::ffff:127.0.0.1") return true;
  return /^127\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.test(value);
}

function record(kind, host, port) {
  counters[kind] += 1;
  if (kind === "rejected") counters.rejectedTargets.push(`${host}:${port ?? ""}`);
  // Read lazily: a harness may choose its log path after this module was preloaded.
  const logPath = process.env.AEGIS_OUTBOUND_LOG;
  if (logPath === undefined || logPath === "") return;
  try {
    fs.appendFileSync(
      logPath,
      `${JSON.stringify({
        kind,
        host: String(host ?? ""),
        port: port ?? null,
        pid: process.pid,
        at: new Date().toISOString(),
      })}\n`,
    );
  } catch {
    // The tripwire must never be the reason a process dies; the throw below is the guard.
  }
}

class NonLoopbackOutboundError extends Error {
  constructor(host, port) {
    super(`outbound connection to ${host}:${port ?? ""} refused: loopback only`);
    this.name = "NonLoopbackOutboundError";
  }
}

function guardHost(host, port) {
  if (isLoopback(host)) {
    record("allowed", host, port);
    return;
  }
  record("rejected", host, port);
  throw new NonLoopbackOutboundError(host, port);
}

const originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function connect(...args) {
  // `net.connect(options)` hands `Socket.prototype.connect` the already normalized
  // `[options, callback]` array, so the object we must inspect can be one level in.
  const first = args[0];
  const options = Array.isArray(first) ? first[0] : first;
  if (typeof options === "object" && options !== null && !Array.isArray(options)) {
    if (typeof options.path === "string") return originalConnect.apply(this, args);
    guardHost(options.host ?? options.hostname, options.port);
  } else if (typeof options === "number" || typeof options === "string") {
    // connect(port[, host]) and connect(path) share this position.
    if (typeof options === "string" && Number.isNaN(Number(options))) {
      return originalConnect.apply(this, args);
    }
    const host = Array.isArray(first) ? first[1] : args[1];
    guardHost(typeof host === "string" ? host : "localhost", options);
  }
  return originalConnect.apply(this, args);
};

// A name lookup is an outbound intent of its own, so it is refused before it resolves.
const originalLookup = dns.lookup;
dns.lookup = function lookup(hostname, options, callback) {
  const done = typeof options === "function" ? options : callback;
  if (!isLoopback(hostname)) {
    record("rejected", hostname, null);
    const error = new NonLoopbackOutboundError(hostname, null);
    if (typeof done === "function") {
      process.nextTick(() => done(error));
      return undefined;
    }
    throw error;
  }
  return originalLookup.call(dns, hostname, options, callback);
};

if (dns.promises && typeof dns.promises.lookup === "function") {
  const originalPromiseLookup = dns.promises.lookup.bind(dns.promises);
  dns.promises.lookup = async (hostname, options) => {
    if (!isLoopback(hostname)) {
      record("rejected", hostname, null);
      throw new NonLoopbackOutboundError(hostname, null);
    }
    return originalPromiseLookup(hostname, options);
  };
}

module.exports = {
  counters,
  isLoopback,
  NonLoopbackOutboundError,
};
