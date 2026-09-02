import type { ProviderAdapter, ProviderResult } from "../contracts.js";

export class MockProviderAdapter implements ProviderAdapter {
  readonly providerId: string;
  readonly #prefix: string;
  readonly #modelVersion: string;

  constructor(providerId: string, prefix = "mock", modelVersion = "mock-v1") {
    this.providerId = providerId;
    this.#prefix = prefix;
    this.#modelVersion = modelVersion;
  }

  async generate(modelId: string, prompt: string): Promise<ProviderResult> {
    return {
      providerId: this.providerId,
      modelId,
      modelVersion: this.#modelVersion,
      responseId: `${this.#prefix}-${modelId}`,
      text: `${this.#prefix}:${prompt}`,
    };
  }
}
