import { hashTypedData, type Address, type Hex, type TypedDataDomain } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import type {
  DecisionSigner,
  Erc3009DecisionAuthorization,
  Erc3009Authorization,
  Erc3009Signer,
} from "./contracts.js";

export const ERC3009_DECISION_AUTHORIZATION_TYPES = {
  Erc3009DecisionAuthorization: [
    { name: "purchaseId", type: "string" },
    { name: "decisionEventHash", type: "bytes32" },
    { name: "quoteId", type: "string" },
    { name: "amount", type: "uint256" },
    { name: "token", type: "address" },
    { name: "payTo", type: "address" },
    { name: "authorizationNonce", type: "bytes32" },
  ],
} as const;

export const TRANSFER_WITH_AUTHORIZATION_TYPES = {
  TransferWithAuthorization: [
    { name: "from", type: "address" },
    { name: "to", type: "address" },
    { name: "value", type: "uint256" },
    { name: "validAfter", type: "uint256" },
    { name: "validBefore", type: "uint256" },
    { name: "nonce", type: "bytes32" },
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

  async signErc3009(
    message: Erc3009DecisionAuthorization,
  ): Promise<{ hash: Hex; signature: Hex }> {
    const hash = hashTypedData({
      domain: this.#domain,
      types: ERC3009_DECISION_AUTHORIZATION_TYPES,
      primaryType: "Erc3009DecisionAuthorization",
      message,
    });
    const signature = await this.#account.signTypedData({
      domain: this.#domain,
      types: ERC3009_DECISION_AUTHORIZATION_TYPES,
      primaryType: "Erc3009DecisionAuthorization",
      message,
    });
    return { hash, signature };
  }
}

export class LocalErc3009Signer implements Erc3009Signer {
  readonly #account;
  readonly #chainId: number;

  constructor(privateKey: Hex, chainId = 84532) {
    this.#account = privateKeyToAccount(privateKey);
    this.#chainId = chainId;
  }

  sign(args: {
    token: Address;
    tokenName: string;
    tokenVersion: string;
    authorization: Erc3009Authorization;
  }): Promise<Hex> {
    if (args.authorization.from.toLowerCase() !== this.#account.address.toLowerCase()) {
      throw new Error("ERC-3009 authorization payer does not match signer");
    }
    return this.#account.signTypedData({
      domain: {
        name: args.tokenName,
        version: args.tokenVersion,
        chainId: this.#chainId,
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
}
