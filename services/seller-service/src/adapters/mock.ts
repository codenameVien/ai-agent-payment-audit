import type { ProviderAdapter, ProviderResult } from "../contracts.js";

export class MockProviderAdapter implements ProviderAdapter {
  readonly providerId: string;
  readonly #prefix: string;

  constructor(providerId: string, prefix = "mock") {
    this.providerId = providerId;
    this.#prefix = prefix;
  }

  async generate(modelId: string, prompt: string): Promise<ProviderResult> {
    return {
      providerId: this.providerId,
      modelId,
      modelVersion: "mock-v1",
      responseId: `${this.#prefix}-${modelId}`,
      text: `${this.#prefix}:${prompt}`,
    };
  }
}
