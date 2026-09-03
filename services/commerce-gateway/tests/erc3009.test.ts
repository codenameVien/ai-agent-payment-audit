import assert from "node:assert/strict";
import test from "node:test";

import { recoverTypedDataAddress, type Address, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import {
  createErc3009Authorization,
  createErc3009PaymentPayload,
  LocalErc3009Signer,
  TRANSFER_WITH_AUTHORIZATION_TYPES,
  type PaymentIntent,
  type PaymentRequirements,
} from "../src/index.js";

const PRIVATE_KEY = `0x${"22".repeat(32)}` as Hex;
const TOKEN = "0x0000000000000000000000000000000000000003" as Address;
const SELLER = "0x0000000000000000000000000000000000000004" as Address;

test("ERC-3009 exact payload is bound to buyer, token, seller, amount and random nonce", async () => {
  const buyer = privateKeyToAccount(PRIVATE_KEY);
  const intent = {
    purchase_id: "purchase-erc3009",
    buyer_wallet_address: buyer.address,
    policy_date: "2026-09-04",
    quote_id: "quote-1",
    decision_event_hash: `sha256:${"11".repeat(32)}`,
    amount_units: 100000,
    token: TOKEN,
    pay_to: SELLER,
    permit2_nonce: "123",
    transfer_method: "eip3009",
    authorization_nonce: `0x${"ab".repeat(32)}`,
    state: "CLAIMED",
    claimed_at: "2026-09-04T00:00:00Z",
  } satisfies PaymentIntent;
  const authorization = createErc3009Authorization({
    intent,
    validAfter: 1_800_000_000n,
    validBefore: 1_800_000_060n,
  });
  const signature = await new LocalErc3009Signer(PRIVATE_KEY, 84532).sign({
    token: TOKEN,
    tokenName: "PBL Agent Credit",
    tokenVersion: "2",
    authorization,
  });
  const recovered = await recoverTypedDataAddress({
    domain: {
      name: "PBL Agent Credit",
      version: "2",
      chainId: 84532,
      verifyingContract: TOKEN,
    },
    types: TRANSFER_WITH_AUTHORIZATION_TYPES,
    primaryType: "TransferWithAuthorization",
    message: {
      from: buyer.address,
      to: SELLER,
      value: 100000n,
      validAfter: 1_800_000_000n,
      validBefore: 1_800_000_060n,
      nonce: intent.authorization_nonce,
    },
    signature,
  });
  const requirement = {
    scheme: "exact",
    network: "eip155:84532",
    amount: "100000",
    asset: TOKEN,
    payTo: SELLER,
    maxTimeoutSeconds: 60,
    extra: { assetTransferMethod: "eip3009", name: "PBL Agent Credit", version: "2" },
  } satisfies PaymentRequirements;
  const payload = createErc3009PaymentPayload({
    resource: { url: "https://seller.test/inference" },
    requirement,
    authorization,
    signature,
  });
  assert.equal(recovered, buyer.address);
  assert.equal(payload.payload.authorization?.nonce, intent.authorization_nonce);
  assert.equal(payload.payload.authorization?.value, "100000");
  assert.equal(payload.payload.permit2Authorization, undefined);
});

test("ERC-3009 payload rejects absent nonce and invalid time window", () => {
  const buyer = privateKeyToAccount(PRIVATE_KEY);
  const base = {
    purchase_id: "p", buyer_wallet_address: buyer.address, policy_date: "2026-09-04",
    quote_id: "q", decision_event_hash: `sha256:${"11".repeat(32)}`,
    amount_units: 1, token: TOKEN, pay_to: SELLER, permit2_nonce: "1",
    transfer_method: "eip3009", state: "CLAIMED", claimed_at: "2026-09-04T00:00:00Z",
  } satisfies PaymentIntent;
  assert.throws(
    () => createErc3009Authorization({ intent: base, validAfter: 1n, validBefore: 2n }),
    /nonce is missing/,
  );
  assert.throws(
    () => createErc3009Authorization({
      intent: { ...base, authorization_nonce: `0x${"01".repeat(32)}` },
      validAfter: 2n,
      validBefore: 2n,
    }),
    /validBefore/,
  );
});
