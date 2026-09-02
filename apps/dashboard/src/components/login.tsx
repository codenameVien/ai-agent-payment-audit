"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { api, short } from "@/lib/api";
import type { MeResponse } from "@/lib/types";

interface EthereumProvider { request(args: { method: string; params?: unknown[] }): Promise<unknown>; }

export function Login() {
  const router = useRouter();
  const [message, setMessage] = useState("MetaMask는 본인 확인에만 사용합니다. 결제는 미리 배정된 구매 에이전트 지갑이 자동으로 처리합니다.");
  const [busy, setBusy] = useState(false);

  async function connect() {
    const ethereum = (window as typeof window & { ethereum?: EthereumProvider }).ethereum;
    if (!ethereum) { setMessage("MetaMask를 찾지 못했습니다."); return; }
    setBusy(true);
    try {
      const accounts = await ethereum.request({ method: "eth_requestAccounts" }) as string[];
      const address = accounts[0]; if (!address) throw new Error("선택된 계정이 없습니다");
      const challenge = await api<{ message: string }>("/auth/siwe/challenge", { method: "POST", body: JSON.stringify({ owner_address: address }) });
      const signature = await ethereum.request({ method: "personal_sign", params: [challenge.message, address] }) as string;
      await api("/auth/siwe/verify", { method: "POST", body: JSON.stringify({ message: challenge.message, signature }) });
      const me = await api<MeResponse>("/auth/me");
      if (!me.buyer_wallet_address) {
        setMessage(`로그인 완료: ${short(address)} · 이 계정에 배정된 구매 에이전트가 없습니다. 관리자 설정을 확인해 주세요.`);
        return;
      }
      setMessage(`로그인 완료: ${short(address)} · 구매 에이전트 연결됨`);
      router.replace("/");
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "로그인 실패"); }
    finally { setBusy(false); }
  }

  return <main className="centerPage"><section className="loginCard"><p className="eyebrow">안전한 사용자 경계</p><h1>MetaMask로 로그인</h1><p>{message}</p><button className="primary" onClick={() => void connect()} disabled={busy}>{busy ? "서명 대기 중…" : "MetaMask로 로그인"}</button><small className="loginHelp">개인키는 전달되지 않으며, 로그인 서명에는 토큰 전송 권한이 없습니다.</small></section></main>;
}
