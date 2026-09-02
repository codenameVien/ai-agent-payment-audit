import type { ProviderAdapter, ProviderResult } from "../contracts.js";

interface GeminiResponse {
  responseId?: string;
  modelVersion?: string;
  candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
}

export class GeminiProviderAdapter implements ProviderAdapter {
  readonly providerId = "gemini";
  readonly #apiKey: string;
  readonly #fetch: typeof fetch;

  constructor(args: { apiKey: string; fetchImpl?: typeof fetch }) {
    if (args.apiKey.length === 0) throw new Error("Gemini API key is required");
    this.#apiKey = args.apiKey;
    this.#fetch = args.fetchImpl ?? fetch;
  }

  async generate(modelId: string, prompt: string): Promise<ProviderResult> {
    const response = await this.#fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(modelId)}:generateContent`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "x-goog-api-key": this.#apiKey },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
      },
    );
    if (!response.ok) throw new Error(`Gemini request failed with HTTP ${response.status}`);
    const data = (await response.json()) as GeminiResponse;
    const text = data.candidates?.[0]?.content?.parts
      ?.map((part) => part.text ?? "").join("").trim() ?? "";
    if (text.length === 0) throw new Error("Gemini response did not contain text");
    return {
      providerId: this.providerId,
      modelId,
      modelVersion: data.modelVersion ?? modelId,
      responseId: data.responseId ?? "unreported",
      text,
    };
  }
}
