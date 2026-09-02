import type { ProviderAdapter, ProviderResult } from "../contracts.js";

interface NvidiaResponse {
  id?: string;
  model?: string;
  choices?: Array<{ message?: { content?: string } }>;
}

export class NemotronProviderAdapter implements ProviderAdapter {
  readonly providerId = "nemotron";
  readonly #apiKey: string;
  readonly #fetch: typeof fetch;

  constructor(args: { apiKey: string; fetchImpl?: typeof fetch }) {
    if (args.apiKey.length === 0) throw new Error("NVIDIA API key is required");
    this.#apiKey = args.apiKey;
    this.#fetch = args.fetchImpl ?? fetch;
  }

  async generate(modelId: string, prompt: string): Promise<ProviderResult> {
    const response = await this.#fetch("https://integrate.api.nvidia.com/v1/chat/completions", {
      method: "POST",
      headers: {
        authorization: `Bearer ${this.#apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: modelId,
        messages: [{ role: "user", content: prompt }],
        stream: false,
      }),
    });
    if (!response.ok) throw new Error(`Nemotron request failed with HTTP ${response.status}`);
    const data = (await response.json()) as NvidiaResponse;
    const text = data.choices?.[0]?.message?.content?.trim() ?? "";
    if (text.length === 0) throw new Error("Nemotron response did not contain text");
    return {
      providerId: this.providerId,
      modelId,
      modelVersion: data.model ?? modelId,
      responseId: data.id ?? "unreported",
      text,
    };
  }
}
