export class X402BindingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "X402BindingError";
  }
}

export function encodeHeader(value: unknown): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}
