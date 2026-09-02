import {
  hashTypedData,
  type Address,
  type Hex,
  type TypedDataDomain,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

import type {
  DecisionAuthorization,
  DecisionSigner,
  Permit2Authorization,
  Permit2Signer,
} from "./contracts.js";
import { CANONICAL_PERMIT2 } from "./x402.js";

export const DECISION_AUTHORIZATION_TYPES = {
  DecisionAuthorization: [
    { name: "purchaseId", type: "string" },
    { name: "decisionEventHash", type: "bytes32" },
    { name: "quoteId", type: "string" },
    { name: "amount", type: "uint256" },
    { name: "token", type: "address" },
    { name: "payTo", type: "address" },
    { name: "permit2Nonce", type: "uint256" },
  ],
} as const;

export const PERMIT2_WITNESS_TYPES = {
  PermitWitnessTransferFrom: [
    { name: "permitted", type: "TokenPermissions" },
    { name: "spender", type: "address" },
    { name: "nonce", type: "uint256" },
    { name: "deadline", type: "uint256" },
    { name: "witness", type: "Witness" },
  ],
  TokenPermissions: [
    { name: "token", type: "address" },
    { name: "amount", type: "uint256" },
  ],
  Witness: [
    { name: "to", type: "address" },
    { name: "validAfter", type: "uint256" },
  ],
} as const;

export interface DecisionDomainConfig {
  chainId: number;
  verifyingContract: Address;
}

function decisionDomain(config: DecisionDomainConfig): TypedDataDomain {
  return {
    name: "PBL Decision Authorization",
    version: "1",
    chainId: config.chainId,
    verifyingContract: config.verifyingContract,
  };
}

export class LocalDecisionSigner implements DecisionSigner {
  readonly #account;
  readonly #domain: TypedDataDomain;

  constructor(privateKey: Hex, config: DecisionDomainConfig) {
    this.#account = privateKeyToAccount(privateKey);
    this.#domain = decisionDomain(config);
  }

  async sign(message: DecisionAuthorization): Promise<{ hash: Hex; signature: Hex }> {
    const hash = hashTypedData({
      domain: this.#domain,
      types: DECISION_AUTHORIZATION_TYPES,
      primaryType: "DecisionAuthorization",
      message,
    });
    const signature = await this.#account.signTypedData({
      domain: this.#domain,
      types: DECISION_AUTHORIZATION_TYPES,
      primaryType: "DecisionAuthorization",
      message,
    });
    return { hash, signature };
  }
}

export class LocalPermit2Signer implements Permit2Signer {
  readonly #account;
  readonly #chainId: number;

  constructor(privateKey: Hex, chainId = 84532) {
    this.#account = privateKeyToAccount(privateKey);
    this.#chainId = chainId;
  }

  sign(authorization: Permit2Authorization): Promise<Hex> {
    if (authorization.from.toLowerCase() !== this.#account.address.toLowerCase()) {
      throw new Error("Permit2 authorization payer does not match signer");
    }
    return this.#account.signTypedData({
      domain: {
        name: "Permit2",
        chainId: this.#chainId,
        verifyingContract: CANONICAL_PERMIT2,
      },
      types: PERMIT2_WITNESS_TYPES,
      primaryType: "PermitWitnessTransferFrom",
      message: {
        permitted: {
          token: authorization.permitted.token,
          amount: BigInt(authorization.permitted.amount),
        },
        spender: authorization.spender,
        nonce: BigInt(authorization.nonce),
        deadline: BigInt(authorization.deadline),
        witness: {
          to: authorization.witness.to,
          validAfter: BigInt(authorization.witness.validAfter),
        },
      },
    });
  }
}
