import assert from "node:assert/strict";
import test from "node:test";

import { recoverTypedDataAddress, type Address, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import {
  CANONICAL_PERMIT2,
  LocalPermit2Signer,
  type Permit2Authorization,
} from "../src/index.js";

const PRIVATE_KEY = `0x${"11".repeat(32)}` as Hex;
const TOKEN = "0x0000000000000000000000000000000000000003" as Address;

test("EIP-2612 sponsorship signature recovers to the buyer and exact payment amount", async () => {
  const buyer = privateKeyToAccount(PRIVATE_KEY);
  const authorization: Permit2Authorization = {
    permitted: { token: TOKEN, amount: "100000" },
    from: buyer.address,
    spender: "0x402085c248EeA27D92E8b30b2C58ed07f9E20001",
    nonce: "123",
    deadline: "1800000060",
    witness: {
      to: "0x0000000000000000000000000000000000000004",
      validAfter: "1800000000",
    },
  };
  const signer = new LocalPermit2Signer(PRIVATE_KEY, 84532, async () => 7n);
  const permit = await signer.signEip2612Permit({
    authorization,
    tokenName: "PBL Agent Credit",
    tokenVersion: "1",
  });
  const recovered = await recoverTypedDataAddress({
    domain: {
      name: "PBL Agent Credit",
      version: "1",
      chainId: 84532,
      verifyingContract: TOKEN,
    },
    types: {
      Permit: [
        { name: "owner", type: "address" },
        { name: "spender", type: "address" },
        { name: "value", type: "uint256" },
        { name: "nonce", type: "uint256" },
        { name: "deadline", type: "uint256" },
      ],
    },
    primaryType: "Permit",
    message: {
      owner: buyer.address,
      spender: CANONICAL_PERMIT2,
      value: 100000n,
      nonce: 7n,
      deadline: 1800000060n,
    },
    signature: permit.signature,
  });
  assert.equal(recovered, buyer.address);
  assert.equal(permit.amount, "100000");
  assert.equal(permit.spender, CANONICAL_PERMIT2);
  assert.equal(permit.nonce, "7");
});

test("EIP-2612 sponsorship fails closed without an on-chain nonce reader", async () => {
  const buyer = privateKeyToAccount(PRIVATE_KEY);
  const signer = new LocalPermit2Signer(PRIVATE_KEY, 84532);
  await assert.rejects(
    () => signer.signEip2612Permit({
      authorization: {
        permitted: { token: TOKEN, amount: "1" },
        from: buyer.address,
        spender: "0x402085c248EeA27D92E8b30b2C58ed07f9E20001",
        nonce: "1",
        deadline: "2",
        witness: {
          to: "0x0000000000000000000000000000000000000004",
          validAfter: "1",
        },
      },
      tokenName: "PBL Agent Credit",
      tokenVersion: "1",
    }),
    /nonce reader is not configured/,
  );
});
