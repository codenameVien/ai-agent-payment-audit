import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

import {
  createPublicClient,
  createWalletClient,
  http,
  type Address,
  type Hex,
  type PublicClient,
  type WalletClient,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

import {
  EvidenceAnchorHttpClient,
  EvidenceHttpClient,
  ReputationEvidenceHttpClient,
  RoutingSellerHttpClient,
} from "./adapters/http.js";
import {
  ViemErc8004Contracts,
  ViemReputationReceiptConfirmer,
} from "./adapters/viem-erc8004.js";
import { ViemEvidenceAnchorContract } from "./adapters/viem-evidence-anchor.js";
import { ViemReceiptReader } from "./adapters/viem-receipts.js";
import { LocalDecisionSigner, LocalPermit2Signer } from "./eip712.js";
import {
  BASE_SEPOLIA_ERC8004_IDENTITY,
  BASE_SEPOLIA_ERC8004_REPUTATION,
  Erc8004Service,
  Erc8004ReputationPublisher,
} from "./erc8004.js";
import { EvidenceAnchorService } from "./evidence-anchor.js";
import { CommerceGateway } from "./gateway.js";

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

interface RuntimeComponents {
  gateway: CommerceGateway;
  reputation: Erc8004ReputationPublisher;
  anchor?: EvidenceAnchorService;
}

export function createRuntime(env: NodeJS.ProcessEnv = process.env): RuntimeComponents {
  const rpcUrl = required(env, "BASE_SEPOLIA_RPC_URL");
  const privateKey = required(env, "BUYER_PRIVATE_KEY") as Hex;
  const account = privateKeyToAccount(privateKey);
  const sellerRoutes = JSON.parse(required(env, "SELLER_ROUTES_JSON")) as unknown;
  if (typeof sellerRoutes !== "object" || sellerRoutes === null || Array.isArray(sellerRoutes)) {
    throw new Error("SELLER_ROUTES_JSON must be an object");
  }
  for (const [agentId, url] of Object.entries(sellerRoutes)) {
    if (!agentId || typeof url !== "string" || !/^https?:\/\//.test(url)) {
      throw new Error("SELLER_ROUTES_JSON contains an invalid route");
    }
  }
  const publicClient = createPublicClient({ chain: baseSepolia, transport: http(rpcUrl) });
  const walletClient = createWalletClient({ account, chain: baseSepolia, transport: http(rpcUrl) });
  const erc8004 = new Erc8004Service(
    new ViemErc8004Contracts({
      publicClient: publicClient as PublicClient,
      walletClient: walletClient as WalletClient,
      identityRegistry:
        (env.ERC8004_IDENTITY_REGISTRY as Address | undefined) ??
        BASE_SEPOLIA_ERC8004_IDENTITY,
      reputationRegistry:
        (env.ERC8004_REPUTATION_REGISTRY as Address | undefined) ??
        BASE_SEPOLIA_ERC8004_REPUTATION,
    }),
    account.address,
  );
  const evidenceApiUrl = required(env, "EVIDENCE_API_URL");
  const internalServiceToken = required(env, "INTERNAL_SERVICE_TOKEN");
  const reputationRegistry =
    (env.ERC8004_REPUTATION_REGISTRY as Address | undefined) ??
    BASE_SEPOLIA_ERC8004_REPUTATION;
  const gateway = new CommerceGateway({
    evidence: new EvidenceHttpClient({
      baseUrl: evidenceApiUrl,
      internalServiceToken,
    }),
    seller: new RoutingSellerHttpClient(
      sellerRoutes as Record<string, string>,
      undefined,
      internalServiceToken,
    ),
    decisionSigner: new LocalDecisionSigner(privateKey, {
      chainId: baseSepolia.id,
      verifyingContract: required(env, "DECISION_VERIFYING_CONTRACT") as Address,
    }),
    permit2Signer: new LocalPermit2Signer(
      privateKey,
      baseSepolia.id,
      async (token, owner) => publicClient.readContract({
        address: token,
        abi: [{
          type: "function",
          name: "nonces",
          stateMutability: "view",
          inputs: [{ name: "owner", type: "address" }],
          outputs: [{ name: "nonce", type: "uint256" }],
        }],
        functionName: "nonces",
        args: [owner],
      }),
    ),
    receipts: new ViemReceiptReader(publicClient as PublicClient),
    identityVerifier: erc8004,
    clock: { nowSeconds: () => BigInt(Math.floor(Date.now() / 1000)) },
  });
  const reputation = new Erc8004ReputationPublisher({
    service: erc8004,
    evidence: new ReputationEvidenceHttpClient({
      baseUrl: evidenceApiUrl,
      internalServiceToken,
    }),
    receipts: new ViemReputationReceiptConfirmer(publicClient as PublicClient),
    chainId: baseSepolia.id,
    registryAddress: reputationRegistry,
  });
  const anchorAddress = env.EVIDENCE_ANCHOR_ADDRESS?.trim() as Address | undefined;
  const anchor = anchorAddress
    ? new EvidenceAnchorService({
        api: new EvidenceAnchorHttpClient({
          baseUrl: evidenceApiUrl,
          internalServiceToken,
        }),
        contract: new ViemEvidenceAnchorContract({
          publicClient: publicClient as PublicClient,
          walletClient: walletClient as WalletClient,
          address: anchorAddress,
        }),
        chainId: baseSepolia.id,
        contractAddress: anchorAddress,
      })
    : undefined;
  return { gateway, reputation, ...(anchor === undefined ? {} : { anchor }) };
}

export function createCommerceGateway(env: NodeJS.ProcessEnv = process.env): CommerceGateway {
  return createRuntime(env).gateway;
}

export function createCommerceGatewayServer(env: NodeJS.ProcessEnv = process.env) {
  const runtime = createRuntime(env);
  const serviceToken = required(env, "GATEWAY_SERVICE_TOKEN");
  return createServer((request, response) => {
    void (async () => {
      if (request.method === "GET" && request.url === "/health") {
        response.statusCode = 200;
        response.setHeader("content-type", "application/json");
        response.end(JSON.stringify({ status: "ok" }));
        return;
      }
      if (request.headers.authorization !== `Bearer ${serviceToken}`) {
        response.statusCode = 401;
        response.end();
        return;
      }
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
        purchaseId?: unknown;
        prompt?: unknown;
        resourceBody?: unknown;
        transactionHash?: unknown;
        agentId?: unknown;
      };
      if (typeof body.purchaseId !== "string" || body.purchaseId.length === 0) {
        throw new Error("purchaseId is required");
      }
      let result;
      if (request.method === "POST" && request.url === "/execute") {
        result = await runtime.gateway.execute(
          body.purchaseId,
          body.resourceBody ?? { prompt: body.prompt ?? "" },
        );
      } else if (
        request.method === "POST" &&
        request.url === "/reconcile" &&
        typeof body.transactionHash === "string"
      ) {
        result = await runtime.gateway.reconcile(body.purchaseId, body.transactionHash as Hex);
      } else if (
        request.method === "POST" &&
        request.url === "/reputation" &&
        (typeof body.agentId === "string" || typeof body.agentId === "number")
      ) {
        result = { transactionHash: await runtime.reputation.publish({
          purchaseId: body.purchaseId,
          agentId: BigInt(body.agentId),
        }) };
      } else if (request.method === "POST" && request.url === "/anchor") {
        if (runtime.anchor === undefined) {
          throw new Error("EVIDENCE_ANCHOR_ADDRESS is not configured");
        }
        result = { transactionHash: await runtime.anchor.anchor(body.purchaseId) };
      } else {
        response.statusCode = 404;
        response.end();
        return;
      }
      response.statusCode = 200;
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify(result));
    })().catch((error: unknown) => {
      response.statusCode = 409;
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify({ error: error instanceof Error ? error.message : "error" }));
    });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT ?? "8081");
  if (!Number.isSafeInteger(port) || port <= 0) throw new Error("PORT must be positive");
  createCommerceGatewayServer().listen(port, "0.0.0.0", () => {
    process.stdout.write(`commerce gateway listening on ${port}\n`);
  });
}
