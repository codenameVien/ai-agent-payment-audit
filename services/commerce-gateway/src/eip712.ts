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
  Erc3009DecisionAuthorization,
  Eip2612GasSponsoringInfo,
  Erc3009Authorization,
  Erc3009Signer,
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

export class LocalPermit2Signer implements Permit2Signer {
  readonly #account;
  readonly #chainId: number;
  readonly #readNonce?: (token: Address, owner: Address) => Promise<bigint>;

  constructor(
    privateKey: Hex,
    chainId = 84532,
    readNonce?: (token: Address, owner: Address) => Promise<bigint>,
  ) {
    this.#account = privateKeyToAccount(privateKey);
    this.#chainId = chainId;
    this.#readNonce = readNonce;
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

  async signEip2612Permit(args: {
    authorization: Permit2Authorization;
    tokenName: string;
    tokenVersion: string;
  }): Promise<Eip2612GasSponsoringInfo> {
    if (this.#readNonce === undefined) {
      throw new Error("EIP-2612 nonce reader is not configured");
    }
    if (args.authorization.from.toLowerCase() !== this.#account.address.toLowerCase()) {
      throw new Error("EIP-2612 permit owner does not match signer");
    }
    const token = args.authorization.permitted.token;
    const nonce = await this.#readNonce(token, this.#account.address);
    const signature = await this.#account.signTypedData({
      domain: {
        name: args.tokenName,
        version: args.tokenVersion,
        chainId: this.#chainId,
        verifyingContract: token,
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
        owner: this.#account.address,
        spender: CANONICAL_PERMIT2,
        value: BigInt(args.authorization.permitted.amount),
        nonce,
        deadline: BigInt(args.authorization.deadline),
      },
    });
    return {
      from: this.#account.address,
      asset: token,
      spender: CANONICAL_PERMIT2,
      amount: args.authorization.permitted.amount,
      nonce: nonce.toString(),
      deadline: args.authorization.deadline,
      signature,
      version: "1",
    };
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
