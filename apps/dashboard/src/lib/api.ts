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

export function credits(value: number | null): string {
  return value === null
    ? "—"
    : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 6 }).format(
        value / 1_000_000,
      );
}
