/**
 * The signing boundary of the payment execution module.
 *
 * The private key exists only inside this process. The buyer agent, the Provider Gateway,
 * the Evidence API and every LLM prompt see at most the resulting address, the EIP-712
 * digest and the signature. The local runner generates an ephemeral test key per run, so
 * no real wallet key is ever loaded here.
 */

import { hashTypedData, type Address, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import type { Erc3009Authorization } from "../contracts.js";
import { LocalErc3009Signer, TRANSFER_WITH_AUTHORIZATION_TYPES } from "../eip712.js";

export interface SignedErc3009Authorization {
  hash: Hex;
  signature: Hex;
}

/**
 * The EIP-712 digest of one ERC-3009 authorization.
 *
 * The payment execution module records it as the authorization hash of the attempt, and
 * the Provider Gateway recomputes it from the payload it was handed. That is what ties a
 * presented signature to the single authorization the Evidence API durably reserved,
 * rather than to any authorization that merely happens to be well formed.
 */
export function authorizationDigest(args: {
  token: Address;
  tokenName: string;
  tokenVersion: string;
  chainId: number;
  authorization: Erc3009Authorization;
}): Hex {
  return hashTypedData({
    domain: {
      name: args.tokenName,
      version: args.tokenVersion,
      chainId: args.chainId,
      verifyingContract: args.token,
    },
    types: TRANSFER_WITH_AUTHORIZATION_TYPES,
    primaryType: "TransferWithAuthorization",
    message: {
      from: args.authorization.from,
      to: args.authorization.to,
      value: BigInt(args.authorization.value),
      validAfter: BigInt(args.authorization.validAfter),
      validBefore: BigInt(args.authorization.validBefore),
      nonce: args.authorization.nonce,
    },
  });
}

export class AegisAuthorizationSigner {
  readonly #inner: LocalErc3009Signer;
  readonly #address: Address;
  readonly #chainId: number;

  constructor(privateKey: Hex, chainId: number) {
    this.#inner = new LocalErc3009Signer(privateKey, chainId);
    this.#address = privateKeyToAccount(privateKey).address;
    this.#chainId = chainId;
  }

  get address(): Address {
    return this.#address;
  }

  /** The digest is returned so the Evidence API can store the exact terms that were signed. */
  async sign(args: {
    token: Address;
    tokenName: string;
    tokenVersion: string;
    authorization: Erc3009Authorization;
  }): Promise<SignedErc3009Authorization> {
    const hash = authorizationDigest({ ...args, chainId: this.#chainId });
    return { hash, signature: await this.#inner.sign(args) };
  }
}
