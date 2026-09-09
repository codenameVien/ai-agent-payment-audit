/**
 * The three Mock Provider adapters behind the Provider Gateways.
 *
 * These are ordinary code. There is no model call, no negotiation, no counteroffer and
 * no judgement of any kind: a gateway serves exactly the provider model version the
 * recorded decision selected, or it refuses. Every result is labelled as mock execution,
 * and `observedExecutionMs` is the elapsed time of this mock, never a measurement of the
 * real model.
 */

import { createHash } from "node:crypto";

export const MOCK_EXECUTION_MODE = "mock";
export const MOCK_PROVIDER_LABEL = "Mock Provider 실행";

export interface MockProviderRequest {
  purchaseId: string;
  prompt: string;
}

export interface MockProviderResult {
  responseId: string;
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  executionMode: string;
  providerLabel: string;
  promptHash: string;
  text: string;
  observedExecutionMs: number;
}

export interface MockProvider {
  readonly providerId: string;
  readonly providerModelId: string;
  readonly modelVersion: string;
  complete(request: MockProviderRequest): Promise<MockProviderResult>;
}

function sha256(value: string): string {
  return `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`;
}

/**
 * A deterministic provider mock.
 *
 * The response identity is derived from the purchase and the prompt, so a repeated
 * delivery attempt after a settled payment produces the same identity instead of a
 * second, conflicting delivery record.
 */
export function createMockProvider(config: {
  providerId: string;
  providerModelId: string;
  modelVersion: string;
  vendorLabel: string;
}): MockProvider {
  return {
    providerId: config.providerId,
    providerModelId: config.providerModelId,
    modelVersion: config.modelVersion,
    async complete(request: MockProviderRequest): Promise<MockProviderResult> {
      const startedAt = Date.now();
      const promptHash = sha256(request.prompt);
      const text = [
        `[${MOCK_PROVIDER_LABEL}] ${config.vendorLabel} ${config.providerModelId}`,
        `(${config.modelVersion}) 로 처리한 모의 응답입니다.`,
        `요청 ${request.purchaseId} 의 프롬프트 해시는 ${promptHash} 입니다.`,
        "실제 모델 호출과 실제 성능 측정은 포함되지 않습니다.",
      ].join(" ");
      return {
        responseId: sha256(`${request.purchaseId}:${promptHash}:${config.providerModelId}`),
        providerId: config.providerId,
        providerModelId: config.providerModelId,
        modelVersion: config.modelVersion,
        executionMode: MOCK_EXECUTION_MODE,
        providerLabel: MOCK_PROVIDER_LABEL,
        promptHash,
        text,
        observedExecutionMs: Math.max(0, Date.now() - startedAt),
      };
    },
  };
}

/**
 * The runnable demo catalog, keyed by provider id.
 *
 * The identifiers are the exact published provider model ids; the AA numbers mapped to
 * them in `packages/schemas/fixtures/aa/model-catalog.runtime.json` remain explicitly
 * synthetic fixture data and are recorded as such.
 */
export const AEGIS_MOCK_PROVIDERS: Readonly<Record<string, MockProvider>> = Object.freeze({
  openai: createMockProvider({
    providerId: "openai",
    providerModelId: "gpt-4.1-mini-2025-04-14",
    modelVersion: "2025-04-14",
    vendorLabel: "OpenAI",
  }),
  anthropic: createMockProvider({
    providerId: "anthropic",
    providerModelId: "claude-haiku-4-5-20251001",
    modelVersion: "2025-10-01",
    vendorLabel: "Anthropic Claude",
  }),
  google: createMockProvider({
    providerId: "google",
    providerModelId: "gemini-2.5-flash",
    modelVersion: "gemini-2.5-flash",
    vendorLabel: "Google Gemini",
  }),
});

export function mockProviderFor(providerId: string): MockProvider {
  const provider = AEGIS_MOCK_PROVIDERS[providerId];
  if (provider === undefined) {
    throw new Error(`unknown AEGIS provider id: ${providerId}`);
  }
  return provider;
}
