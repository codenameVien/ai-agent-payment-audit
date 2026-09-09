/**
 * In-process doubles for the AEGIS runtime tests.
 *
 * The Evidence API double speaks the same wire contract as the real one, including its
 * snake_case bodies and its single-attempt rules, so the gateway and the payment executor
 * are exercised over real HTTP without a Python process or a database. The authoritative
 * semantics of the real API are covered by its own Python tests and by the stack smoke.
 */

import { createServer, type Server } from "node:http";
import { once } from "node:events";

import { termsBindingHash, type AegisTokenIdentity } from "../src/aegis/terms.js";

export const TEST_TOKEN: AegisTokenIdentity = {
  address: "0x0000000000000000000000000000000000000000",
  chainId: 84532,
  decimals: 6,
  name: "AEGIS",
  status: "prepared",
  symbol: "AEGIS",
};

export const TEST_NETWORK = "eip155:84532";

export interface CatalogModel {
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  recipient: string;
  inputPricePerMillion: string;
  outputPricePerMillion: string;
}

export const TEST_MODELS: Record<string, CatalogModel> = {
  openai: {
    providerId: "openai",
    providerModelId: "gpt-4.1-2025-04-14",
    modelVersion: "2025-04-14",
    recipient: "0x000000000000000000000000000000000000a001",
    inputPricePerMillion: "0.15",
    outputPricePerMillion: "0.6",
  },
  anthropic: {
    providerId: "anthropic",
    providerModelId: "claude-sonnet-5",
    modelVersion: "claude-sonnet-5",
    recipient: "0x000000000000000000000000000000000000a002",
    inputPricePerMillion: "0.8",
    outputPricePerMillion: "4",
  },
  google: {
    providerId: "google",
    providerModelId: "gemini-2.5-flash",
    modelVersion: "gemini-2.5-flash",
    recipient: "0x000000000000000000000000000000000000a003",
    inputPricePerMillion: "0.3",
    outputPricePerMillion: "2.5",
  },
};

export interface DecisionOverrides {
  amountUnits?: number;
  recipient?: string;
  /** Swap only the stored payout address, leaving the binding it was computed from. */
  tamperedRecipient?: string;
  termsBindingHash?: string;
  snapshotInputPrice?: string;
  estimatedInputTokens?: number;
  maxOutputTokens?: number;
}

function candidateKey(model: CatalogModel): string {
  return `${model.providerId}:${model.providerModelId}:${model.modelVersion}`;
}

export function buildDecisionEvidence(args: {
  purchaseId: string;
  model: CatalogModel;
  amountUnits: bigint;
  overrides?: DecisionOverrides;
}): Record<string, unknown> {
  const overrides = args.overrides ?? {};
  const key = candidateKey(args.model);
  const snapshotHash = `sha256:${"1".repeat(64)}`;
  const recipient = overrides.recipient ?? args.model.recipient;
  const amountUnits = overrides.amountUnits ?? Number(args.amountUnits);
  const binding =
    overrides.termsBindingHash ??
    termsBindingHash({
      purchaseId: args.purchaseId,
      snapshotHash,
      providerId: args.model.providerId,
      providerModelId: args.model.providerModelId,
      modelVersion: args.model.modelVersion,
      amountUnits: BigInt(amountUnits),
      recipient,
      token: TEST_TOKEN,
    });
  const source = {
    inputPricePerMillion: args.model.inputPricePerMillion,
    outputPricePerMillion: args.model.outputPricePerMillion,
  };
  const storedRecipient = overrides.tamperedRecipient ?? recipient;
  return {
    purchase_id: args.purchaseId,
    decision: {
      scoringPolicyVersion: "aa-three-factor-v1",
      purchaseId: args.purchaseId,
      snapshotHash,
      termsBindingHash: binding,
      amountUnits,
      tokens: {
        estimatedInputTokens: overrides.estimatedInputTokens ?? 25,
        maxOutputTokens: overrides.maxOutputTokens ?? 1024,
      },
      token: { ...TEST_TOKEN },
      winner: {
        candidateKey: key,
        providerId: args.model.providerId,
        providerModelId: args.model.providerModelId,
        modelVersion: args.model.modelVersion,
        amountUnits,
      },
      candidates: [
        {
          candidateKey: key,
          providerId: args.model.providerId,
          providerModelId: args.model.providerModelId,
          modelVersion: args.model.modelVersion,
          recipient: storedRecipient,
          mappingProvenance: "fixture",
          amountUnits,
          source,
        },
      ],
    },
    decision_event_hash: `sha256:${"2".repeat(64)}`,
    snapshot: {
      purchaseId: args.purchaseId,
      mode: "fixture",
      models: [
        {
          candidateKey: key,
          inputPricePerMillion: overrides.snapshotInputPrice ?? source.inputPricePerMillion,
          outputPricePerMillion: source.outputPricePerMillion,
        },
      ],
    },
    snapshot_event_hash: `sha256:${"3".repeat(64)}`,
    snapshot_hash: snapshotHash,
  };
}

interface StoredIntent {
  purchase_id: string;
  buyer_wallet_address: string;
  state: string;
  amount_units: number;
  token: string;
  pay_to: string;
  authorization_nonce: string;
  decision_authorization_hash: string | null;
  claimed_at: string;
  quote_id: string;
}

export interface EvidenceApiDouble {
  url: string;
  close(): Promise<void>;
  purchases: Map<string, Record<string, unknown>>;
  intents: Map<string, StoredIntent>;
  deliveries: Record<string, unknown>[];
  settlements: Record<string, unknown>[];
  failures: Record<string, unknown>[];
  reconciliations: Record<string, unknown>[];
  budgetUnits: number;
}

export async function startEvidenceApiDouble(args: {
  internalServiceToken: string;
  buyerWalletAddress: string;
  nonce?: string;
}): Promise<EvidenceApiDouble> {
  const purchases = new Map<string, Record<string, unknown>>();
  const intents = new Map<string, StoredIntent>();
  const state: EvidenceApiDouble = {
    url: "",
    close: async () => undefined,
    purchases,
    intents,
    deliveries: [],
    settlements: [],
    failures: [],
    reconciliations: [],
    budgetUnits: 1_000_000,
  };

  const server: Server = createServer((request, response) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const raw = Buffer.concat(chunks).toString("utf8");
      const body = raw.length === 0 ? {} : (JSON.parse(raw) as Record<string, unknown>);
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      const send = (status: number, value: unknown): void => {
        response.statusCode = status;
        response.setHeader("content-type", "application/json");
        response.end(JSON.stringify(value));
      };
      if (request.headers.authorization !== `Bearer ${args.internalServiceToken}`) {
        send(401, { detail: "invalid internal service credential" });
        return;
      }
      const decisionMatch = /^\/internal\/purchases\/([^/]+)\/aegis-decision$/.exec(url.pathname);
      if (decisionMatch !== null) {
        const evidence = purchases.get(decodeURIComponent(decisionMatch[1]!));
        if (evidence === undefined) send(404, { detail: "not found" });
        else send(200, evidence);
        return;
      }
      const intentMatch = /^\/internal\/evidence\/payment-intents\/([^/]+)$/.exec(url.pathname);
      if (intentMatch !== null && request.method === "GET") {
        const intent = intents.get(decodeURIComponent(intentMatch[1]!));
        if (intent === undefined) send(404, { detail: "not found" });
        else send(200, intent);
        return;
      }
      const deliveryMatch =
        /^\/internal\/evidence\/aegis\/purchases\/([^/]+)\/delivery$/.exec(url.pathname);
      if (deliveryMatch !== null) {
        const purchaseId = decodeURIComponent(deliveryMatch[1]!);
        if (intents.get(purchaseId)?.state !== "SETTLED") {
          send(409, { detail: "delivery requires a settled payment" });
          return;
        }
        state.deliveries.push({ purchaseId, ...body });
        send(200, { type: "DELIVERED" });
        return;
      }
      const purchaseId = String(body.purchase_id ?? "");
      const evidence = purchases.get(purchaseId);
      if (evidence === undefined) {
        send(404, { detail: "not found" });
        return;
      }
      const decision = evidence.decision as Record<string, unknown>;
      if (url.pathname === "/internal/evidence/aegis/payments/reserve") {
        let intent = intents.get(purchaseId);
        if (intent === undefined) {
          intent = {
            purchase_id: purchaseId,
            buyer_wallet_address: args.buyerWalletAddress,
            state: "CLAIMED",
            amount_units: decision.amountUnits as number,
            token: (decision.token as AegisTokenIdentity).address,
            pay_to: (
              (decision.candidates as Record<string, unknown>[])[0]!.recipient as string
            ).toLowerCase(),
            authorization_nonce: args.nonce ?? `0x${"a7".repeat(32)}`,
            decision_authorization_hash: null,
            claimed_at: new Date(Date.now() - 5_000).toISOString(),
            quote_id: decision.termsBindingHash as string,
          };
          intents.set(purchaseId, intent);
        }
        send(200, {
          terms: {
            purchase_id: purchaseId,
            amount_units: decision.amountUnits,
            budget_units: state.budgetUnits,
            decision_event_hash: evidence.decision_event_hash,
            snapshot_hash: evidence.snapshot_hash,
            terms_binding_hash: decision.termsBindingHash,
            provider_id: (decision.winner as Record<string, unknown>).providerId,
            provider_model_id: (decision.winner as Record<string, unknown>).providerModelId,
            model_version: (decision.winner as Record<string, unknown>).modelVersion,
            recipient: intent.pay_to,
          },
          intent,
        });
        return;
      }
      const intent = intents.get(purchaseId);
      if (intent === undefined) {
        send(409, { detail: "payment intent is missing" });
        return;
      }
      if (url.pathname === "/internal/evidence/payment-intents/authorize") {
        if (intent.state === "CLAIMED") {
          intent.state = "AUTHORIZED";
          intent.decision_authorization_hash = String(body.authorization_hash ?? "");
        }
        send(200, intent);
        return;
      }
      if (url.pathname === "/internal/evidence/aegis/payments/settle") {
        if (intent.state === "SETTLED") {
          send(200, intent);
          return;
        }
        if (intent.state !== "AUTHORIZED") {
          send(409, { detail: "payment cannot settle from current state" });
          return;
        }
        intent.state = "SETTLED";
        state.settlements.push({ purchaseId, ...body });
        send(200, intent);
        return;
      }
      if (url.pathname === "/internal/evidence/aegis/payments/fail") {
        if (intent.state === "SETTLED") {
          send(409, { detail: "payment already has a different terminal outcome" });
          return;
        }
        intent.state = "FAILED";
        state.failures.push({ purchaseId, ...body });
        send(200, intent);
        return;
      }
      if (url.pathname === "/internal/evidence/payment-intents/reconciliation") {
        intent.state = "RECONCILIATION_REQUIRED";
        state.reconciliations.push({ purchaseId, ...body });
        send(200, intent);
        return;
      }
      send(404, { detail: "not found" });
    })().catch((error: unknown) => {
      response.statusCode = 500;
      response.end(JSON.stringify({ detail: String(error) }));
    });
  });

  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("no address");
  state.url = `http://127.0.0.1:${address.port}`;
  state.close = async () => {
    server.close();
    await once(server, "close");
  };
  return state;
}

export async function startHandlerServer(
  handler: (request: Request) => Promise<Response>,
): Promise<{ url: string; close(): Promise<void> }> {
  const server = createServer((incoming, outgoing) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const chunk of incoming) chunks.push(Buffer.from(chunk));
      const method = incoming.method ?? "GET";
      const headers = new Headers();
      for (const [name, value] of Object.entries(incoming.headers)) {
        if (typeof value === "string") headers.set(name, value);
      }
      const response = await handler(
        new Request(`http://127.0.0.1${incoming.url ?? "/"}`, {
          method,
          headers,
          ...(method === "GET" || method === "HEAD"
            ? {}
            : { body: Buffer.concat(chunks).toString("utf8") }),
        }),
      );
      outgoing.statusCode = response.status;
      response.headers.forEach((value, name) => outgoing.setHeader(name, value));
      outgoing.end(Buffer.from(await response.arrayBuffer()));
    })().catch(() => {
      outgoing.statusCode = 500;
      outgoing.end("{}");
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("no address");
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: async () => {
      server.close();
      await once(server, "close");
    },
  };
}
