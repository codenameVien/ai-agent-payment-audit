import assert from "node:assert/strict";
import test from "node:test";

import { recoverTypedDataAddress, type Address, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import {
  ERC3009_DECISION_AUTHORIZATION_TYPES,
  LocalDecisionSigner,
} from "../src/index.js";

const PRIVATE_KEY = `0x${"11".repeat(32)}` as Hex;
const TOKEN = "0x0000000000000000000000000000000000000003" as Address;
const SELLER = "0x0000000000000000000000000000000000000004" as Address;
const VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000005" as Address;
const DECISION_HASH = `0x${"22".repeat(32)}` as Hex;
const AUTHORIZATION_NONCE = `0x${"33".repeat(32)}` as Hex;

test("ERC-3009 decision authorization binds the selected payment and nonce", async () => {
  const buyer = privateKeyToAccount(PRIVATE_KEY);
  const message = {
    purchaseId: "purchase-1",
    decisionEventHash: DECISION_HASH,
    quoteId: "quote-1",
    amount: 100000n,
    token: TOKEN,
    payTo: SELLER,
    authorizationNonce: AUTHORIZATION_NONCE,
  };
  const signed = await new LocalDecisionSigner(PRIVATE_KEY, {
    chainId: 84532,
    verifyingContract: VERIFYING_CONTRACT,
  }).signErc3009(message);
  const recovered = await recoverTypedDataAddress({
    domain: {
      name: "PBL Decision Authorization",
      version: "1",
      chainId: 84532,
      verifyingContract: VERIFYING_CONTRACT,
    },
    types: ERC3009_DECISION_AUTHORIZATION_TYPES,
    primaryType: "Erc3009DecisionAuthorization",
    message,
    signature: signed.signature,
  });

  assert.equal(recovered, buyer.address);
  assert.match(signed.hash, /^0x[0-9a-f]{64}$/);
});
