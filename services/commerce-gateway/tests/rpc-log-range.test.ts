import assert from "node:assert/strict";
import test from "node:test";

import { recentRpcLogFromBlock } from "../src/adapters/rpc-log-range.js";

test("RPC recovery scans never exceed an inclusive 10,000 block range", () => {
  assert.equal(recentRpcLogFromBlock(9_999n), 0n);
  assert.equal(recentRpcLogFromBlock(10_000n), 1n);
  assert.equal(recentRpcLogFromBlock(46_292_690n), 46_282_691n);
});
