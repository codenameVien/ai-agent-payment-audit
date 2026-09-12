const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/backend";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text.slice(0, 240)}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function short(value: string | null | undefined, size = 7): string {
  if (!value) return "—";
  return value.length > size * 2 + 3
    ? `${value.slice(0, size)}…${value.slice(-size)}`
    : value;
}

export function tokenAmount(value: number | null, decimals = 6): string {
  return value === null
    ? "—"
    : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: decimals }).format(
        value / 10 ** decimals,
      );
}

export function credits(value: number | null): string {
  return tokenAmount(value, 6);
}

/** Contract identity, never a relabel of an older PBLC transfer. */
export function paymentTokenSymbol(address: string | null | undefined): string {
  if (address?.toLowerCase() === "0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b") return "AEGIS";
  if (["0xe75013d333bebb90b321dd658440c10b5a0face8", "0xded7f4992d98ef31453dcebbb8c2a6b50d0284b3"].includes(address?.toLowerCase() ?? "")) return "PBLC";
  return "토큰";
}
