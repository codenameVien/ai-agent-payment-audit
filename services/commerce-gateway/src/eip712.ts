import type { Address, Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import type { Erc3009Authorization, Erc3009Signer } from "./contracts.js";

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
