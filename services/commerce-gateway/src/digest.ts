import type { Hex } from "viem";

export function digestToBytes32(value: string): Hex {
  if (/^0x[0-9a-fA-F]{64}$/.test(value)) return value as Hex;
  if (/^sha256:[0-9a-fA-F]{64}$/.test(value)) return `0x${value.slice(7)}` as Hex;
  throw new Error("evidence hash is not a bytes32 SHA-256 digest");
}
