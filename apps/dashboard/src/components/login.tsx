"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { api, short } from "@/lib/api";

interface EthereumProvider { request(args: { method: string; params?: unknown[] }): Promise<unknown>; }

export function Login() {
  const router = useRouter();
  const [owner, setOwner] = useState("");
  const [buyer, setBuyer] = useState("");
  const [message, setMessage] = useState("MetaMask 서명은 로그인에만 사용되며 개인키는 전달되지 않습니다.");
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
      setOwner(address); setMessage(`로그인 완료: ${short(address)}`);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "로그인 실패"); }
    finally { setBusy(false); }
  }

  async function bind() {
    try { await api("/auth/buyer-wallet", { method: "PUT", body: JSON.stringify({ buyer_wallet_address: buyer }) }); setMessage("구매 에이전트 지갑을 연결했습니다."); router.push("/"); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "지갑 연결 실패"); }
  }

  return <main className="centerPage"><section className="loginCard"><p className="eyebrow">안전한 사용자 경계</p><h1>MetaMask로 로그인</h1><p>{message}</p><button className="primary" onClick={() => void connect()} disabled={busy}>{busy ? "서명 대기 중…" : "MetaMask 연결"}</button>{owner && <div className="bindBox"><label>구매 에이전트 지갑 주소<input value={buyer} onChange={(event) => setBuyer(event.target.value)} placeholder="0x…" /></label><button onClick={() => void bind()} disabled={!buyer}>지갑 연결 후 대시보드 열기</button></div>}</section></main>;
}
