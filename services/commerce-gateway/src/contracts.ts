import type { Address, Hex } from "viem";

export type EvidenceHash = Hex | `sha256:${string}`;

export interface Erc3009Authorization {
  from: Address;
  to: Address;
  value: string;
  validAfter: string;
  validBefore: string;
  nonce: Hex;
}

export interface Erc3009Signer {
  sign(args: {
    token: Address;
    tokenName: string;
    tokenVersion: string;
    authorization: Erc3009Authorization;
  }): Promise<Hex>;
}
