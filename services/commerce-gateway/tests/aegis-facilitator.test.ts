import assert from "node:assert/strict";
import test from "node:test";

import { privateKeyToAccount } from "viem/accounts";
import type { Hex } from "viem";

import { MockFacilitator } from "../src/aegis/facilitator.js";
import { AegisAuthorizationSigner } from "../src/aegis/signer.js";
import type { AegisPaymentPayload, AegisPaymentRequirements } from "../src/aegis/x402.js";
import { TEST_NETWORK, TEST_TOKEN } from "./aegis-fixtures.js";

const PAYER_KEY = `0x${"11".repeat(32)}` as Hex;
const OTHER_KEY = `0x${"22".repeat(32)}` as Hex;
const PAY_TO = "0x000000000000000000000000000000000000a001";
const NONCE = `0x${"a7".repeat(32)}` as Hex;
const NOW = 1_800_000_000n;

function requirements(amount = "619"): AegisPaymentRequirements {
  return {
    scheme: "exact",
    network: TEST_NETWORK,
    amount,
    asset: TEST_TOKEN.address,
    payTo: PAY_TO,
    maxTimeoutSeconds: 60,
    extra: { assetTransferMethod: "eip3009", name: "PBL Agent Credit", version: "2" },
  };
}

async function context(options: {
  key?: Hex;
  amount?: string;
  nonce?: Hex;
  validAfter?: bigint;
  validBefore?: bigint;
} = {}): Promise<{ paymentPayload: AegisPaymentPayload; paymentRequirements: AegisPaymentRequirements }> {
  const key = options.key ?? PAYER_KEY;
  const signer = new AegisAuthorizationSigner(key, 84532);
  const amount = options.amount ?? "619";
  const authorization = {
    from: privateKeyToAccount(key).address,
    to: PAY_TO as `0x${string}`,
    value: amount,
    validAfter: String(options.validAfter ?? NOW - 10n),
    validBefore: String(options.validBefore ?? NOW + 600n),
    nonce: options.nonce ?? NONCE,
  };
  const signed = await signer.sign({
    token: TEST_TOKEN.address as `0x${string}`,
    tokenName: "PBL Agent Credit",
    tokenVersion: "2",
    authorization,
  });
  return {
    paymentPayload: {
      x402Version: 2,
      resource: { url: "http://127.0.0.1/v1/inference", description: "", mimeType: "" },
      accepted: requirements(amount),
      payload: { signature: signed.signature, authorization },
      extensions: { aegis: {} as never },
    },
    paymentRequirements: requirements(amount),
  };
}

function facilitator(): MockFacilitator {
  return new MockFacilitator({ nowSeconds: () => NOW });
}

test("a correctly signed authorization settles once", async () => {
  const mock = facilitator();
  const settled = await mock.settle(await context());
  assert.equal(settled.success, true);
  assert.ok(settled.transaction.startsWith("x402mock:"));
  // The settlement identifier must never be mistakable for a chain transaction.
  assert.ok(!settled.transaction.startsWith("0x"));
  assert.equal(settled.payer, privateKeyToAccount(PAYER_KEY).address.toLowerCase());
});

test("a consumed nonce only replays for the identical authorization", async () => {
  const mock = facilitator();
  const original = await context();
  const first = await mock.settle(original);
  assert.equal(first.success, true);

  const replay = await mock.settle(original);
  assert.deepEqual(replay, first);

  // Same nonce, different amount: a reuse attempt, never a success.
  const cheaper = await mock.settle(await context({ amount: "1" }));
  assert.equal(cheaper.success, false);
  assert.equal(cheaper.errorReason, "authorization is already used");

  // Same nonce, a different validity window, hence a different signature.
  const reshaped = await mock.settle(await context({ validBefore: NOW + 900n }));
  assert.equal(reshaped.success, false);
  assert.equal(reshaped.errorReason, "authorization is already used");

  assert.equal((await mock.verify(original)).isValid, false);
});

test("two different authorizations sharing a nonce cannot both settle", async () => {
  const mock = facilitator();
  const [first, second] = await Promise.all([
    mock.settle(await context()),
    mock.settle(await context({ validBefore: NOW + 900n })),
  ]);
  const successes = [first, second].filter((item) => item.success);
  assert.equal(successes.length, 1);
});

test("identical concurrent settlements share one outcome", async () => {
  const mock = facilitator();
  const prepared = await context();
  const [first, second] = await Promise.all([mock.settle(prepared), mock.settle(prepared)]);
  assert.equal(first.success, true);
  assert.deepEqual(second, first);
});

test("validAfter equal to now is refused, as the token contract would", async () => {
  const mock = facilitator();
  // AEGISToken.transferWithAuthorization reverts on block.timestamp <= validAfter.
  const boundary = await mock.verify(await context({ validAfter: NOW }));
  assert.equal(boundary.isValid, false);
  assert.equal(boundary.invalidReason, "authorization is not valid yet");
  const oneSecondEarlier = await mock.verify(await context({ validAfter: NOW - 1n }));
  assert.equal(oneSecondEarlier.isValid, true);
});

test("an expired authorization is refused at the closing boundary", async () => {
  const mock = facilitator();
  const expired = await mock.verify(await context({ validBefore: NOW }));
  assert.equal(expired.isValid, false);
  assert.equal(expired.invalidReason, "authorization is expired");
});

test("a signature from another key never authorizes the declared payer", async () => {
  const mock = facilitator();
  const honest = await context();
  const forged = await context({ key: OTHER_KEY });
  const swapped = {
    paymentRequirements: honest.paymentRequirements,
    paymentPayload: {
      ...honest.paymentPayload,
      payload: {
        signature: forged.paymentPayload.payload.signature,
        authorization: honest.paymentPayload.payload.authorization,
      },
    },
  };
  const verified = await mock.verify(swapped);
  assert.equal(verified.isValid, false);
  assert.equal(verified.invalidReason, "signature does not belong to the payer");
  assert.equal((await mock.settle(swapped)).success, false);
});

test("an authorization that does not pay the required amount is refused", async () => {
  const mock = facilitator();
  const prepared = await context({ amount: "1" });
  const mismatched = {
    paymentPayload: prepared.paymentPayload,
    paymentRequirements: requirements("619"),
  };
  const verified = await mock.verify(mismatched);
  assert.equal(verified.isValid, false);
  assert.equal(verified.invalidReason, "authorized value is not the required amount");
});

test("a malformed settlement body is refused instead of throwing", async () => {
  const mock = facilitator();
  const answer = await mock.settle({
    paymentPayload: { payload: {} } as never,
    paymentRequirements: requirements(),
  });
  assert.equal(answer.success, false);
  assert.equal(answer.errorReason, "authorization is malformed");
});
