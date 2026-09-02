"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/backend";

export function LiveStatus() {
  const [state, setState] = useState<"connecting" | "live" | "stale">("connecting");
  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/events`, { withCredentials: true });
    source.onopen = () => setState("live");
    source.onmessage = () => window.dispatchEvent(new Event("pbl:evidence"));
    source.onerror = () => setState("stale");
    return () => source.close();
  }, []);
  const labels = {
    connecting: "연결 중",
    live: "실시간",
    stale: "연결 끊김 · 새로고침 가능",
  };
  return <span className={`live ${state}`} role="status"><i />{labels[state]}</span>;
}
