import {
  recoverTypedDataAddress,
  verifyTypedData,
  type Address,
  type Hex,
  type TypedDataDomain,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

import type { QuoteSigner, SellerQuote } from "./contracts.js";

export const SELLER_QUOTE_TYPES = {
  SellerQuote: [
    { name: "quoteId", type: "string" },
    { name: "purchaseId", type: "string" },
    { name: "sellerAgentId", type: "string" },
    { name: "erc8004AgentId", type: "uint256" },
    { name: "providerId", type: "string" },
    { name: "modelId", type: "string" },
    { name: "modelVersion", type: "string" },
    { name: "amount", type: "uint256" },
    { name: "token", type: "address" },
    { name: "payTo", type: "address" },
    { name: "expectedLatencyMs", type: "uint256" },
    { name: "inputLimit", type: "uint256" },
    { name: "outputLimit", type: "uint256" },
    { name: "expiresAt", type: "uint256" },
    { name: "quoteNonce", type: "uint256" },
    { name: "counterofferOf", type: "string" },
    { name: "available", type: "bool" },
    { name: "counterofferReason", type: "string" },
  ],
} as const;

export interface SellerQuoteDomainConfig {
  chainId: number;
  verifyingContract: Address;
}

export function sellerQuoteDomain(config: SellerQuoteDomainConfig): TypedDataDomain {
  return {
    name: "PBL Seller Quote",
    version: "1",
    chainId: config.chainId,
    verifyingContract: config.verifyingContract,
  };
}

export class LocalEip712QuoteSigner implements QuoteSigner {
  readonly address: Address;
  readonly #account;
  readonly #domain: TypedDataDomain;

  constructor(privateKey: Hex, config: SellerQuoteDomainConfig) {
    this.#account = privateKeyToAccount(privateKey);
    this.address = this.#account.address;
    this.#domain = sellerQuoteDomain(config);
  }

  async sign(quote: SellerQuote): Promise<Hex> {
    return this.#account.signTypedData({
      domain: this.#domain,
      types: SELLER_QUOTE_TYPES,
      primaryType: "SellerQuote",
      message: quote,
    });
  }
}

export async function recoverSellerQuoteSigner(
  quote: SellerQuote,
  signature: Hex,
  config: SellerQuoteDomainConfig,
): Promise<Address> {
  return recoverTypedDataAddress({
    domain: sellerQuoteDomain(config),
    types: SELLER_QUOTE_TYPES,
    primaryType: "SellerQuote",
    message: quote,
    signature,
  });
}

export async function verifySellerQuote(
  quote: SellerQuote,
  signature: Hex,
  expectedSigner: Address,
  config: SellerQuoteDomainConfig,
): Promise<boolean> {
  return verifyTypedData({
    address: expectedSigner,
    domain: sellerQuoteDomain(config),
    types: SELLER_QUOTE_TYPES,
    primaryType: "SellerQuote",
    message: quote,
    signature,
  });
}
