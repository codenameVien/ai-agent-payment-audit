"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, credits, short } from "@/lib/api";
import type { AuditFinding, PurchaseSummary, WalletView } from "@/lib/types";
import { Empty, SeverityBadge } from "./status";

export function Overview() {
  const router = useRouter();
  const [wallet, setWallet] = useState<WalletView | null>(null);
  const [purchases, setPurchases] = useState<PurchaseSummary[]>([]);
  const [alerts, setAlerts] = useState<AuditFinding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const [nextWallet, nextPurchases, nextAlerts] = await Promise.all([
        api<WalletView>("/wallet"),
        api<PurchaseSummary[]>("/purchases"),
        api<AuditFinding[]>("/audit-alerts"),
      ]);
      setWallet(nextWallet); setPurchases(nextPurchases); setAlerts(nextAlerts); setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "조회 실패"); }
  }, []);
  useEffect(() => { void load(); const listener = () => void load(); window.addEventListener("pbl:evidence", listener); return () => window.removeEventListener("pbl:evidence", listener); }, [load]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(null);
    const data = new FormData(event.currentTarget);
    try {
      const created = await api<{ purchase_id: string }>("/purchases", { method: "POST", body: JSON.stringify({
        domain: "ai_inference",
        request: { prompt: String(data.get("prompt")), priority: String(data.get("priority")) },
        budget_units: Math.round(Number(data.get("budget")) * 1_000_000),
        policy: { priority: String(data.get("priority")) },
      }) });
      await api(`/purchases/${encodeURIComponent(created.purchase_id)}/run`, {
        method: "POST",
      });
      event.currentTarget.reset();
      await load();
      router.push(`/purchases/${created.purchase_id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "요청 실패"); }
    finally { setBusy(false); }
  }

  if (error?.startsWith("401")) return <Empty>로그인이 필요합니다. <Link href="/login">MetaMask로 로그인</Link></Empty>;
  const risk = alerts.filter((item) => item.severity === "RISK").length;
  const caution = alerts.filter((item) => item.severity === "CAUTION").length;
  return <main>
    <section className="hero"><div><p className="eyebrow">AI 구매 감사 콘솔</p><h1>에이전트의 선택과 결제를<br />한 흐름으로 확인하세요.</h1><p>판단 근거, 예산, x402 정산, ERC-8004 평판을 purchaseId로 연결합니다.</p></div><div className="heroShield" aria-hidden="true">✓</div></section>
    {error && <div className="notice risk" role="alert">데이터가 최신이 아닐 수 있습니다: {error} <button onClick={() => void load()}>다시 시도</button></div>}
    <section className="metricGrid" aria-label="계정 요약">
      <article><span>데모 토큰 잔액</span><strong>{wallet?.token_balance_units == null ? "RPC 연결 후 표시" : `${credits(wallet.token_balance_units)} PBLC`}</strong><small>{wallet?.balance_status === "rpc_not_configured" ? "현재 로컬 데이터" : "Base Sepolia"}</small></article>
      <article><span>결제 사용 / 예약</span><strong>{credits(wallet?.spent_units ?? 0)} / {credits(wallet?.reserved_units ?? 0)}</strong><small>일일 한도 {credits(wallet?.daily_limit_units ?? null)}</small></article>
      <article><span>열린 경고</span><strong className={risk ? "riskText" : ""}>{risk} 위험 · {caution} 주의</strong><small><Link href="/alerts">경고 검토하기 →</Link></small></article>
    </section>
    <section className="twoCol">
      <article className="panel"><div className="sectionHead"><div><p className="eyebrow">새 구매 요청</p><h2>예산 안에서 최적 AI 선택</h2></div></div><form onSubmit={submit} className="requestForm"><label>요청 내용<textarea name="prompt" required rows={5} placeholder="예: 한국어 기술 문서를 정확하게 요약해줘" /></label><div className="formRow"><label>최대 예산 (PBLC)<input name="budget" required type="number" min="0" step="0.000001" defaultValue="1" /></label><label>우선순위<select name="priority" defaultValue="balanced"><option value="balanced">균형</option><option value="quality">품질</option><option value="price">가격</option><option value="speed">속도</option></select></label></div><button className="primary" disabled={busy}>{busy ? "AI 선택·결제·감사 진행 중…" : "구매 에이전트에게 맡기기"}</button><small>결제 전 후보·가격·신원·정책이 모두 고정됩니다.</small></form></article>
      <article className="panel"><div className="sectionHead"><div><p className="eyebrow">지갑 연결</p><h2>소유자와 구매 지갑</h2></div><Link href="/login">관리</Link></div><dl className="walletList"><div><dt>Owner</dt><dd>{short(wallet?.owner_address)}</dd></div><div><dt>Buyer Agent</dt><dd>{short(wallet?.buyer_wallet_address)}</dd></div><div><dt>Token</dt><dd>{short(wallet?.token)}</dd></div></dl></article>
    </section>
    <section className="panel"><div className="sectionHead"><div><p className="eyebrow">최근 거래</p><h2>판단부터 감사까지</h2></div><Link href="/purchases">전체 보기 →</Link></div>{purchases.length === 0 ? <Empty>아직 거래가 없습니다.</Empty> : <div className="tableWrap"><table><thead><tr><th>요청</th><th>상태</th><th>결제</th><th>감사</th></tr></thead><tbody>{purchases.slice(0, 5).map((item) => <tr key={item.purchase_id}><td><Link href={`/purchases/${item.purchase_id}`}>{short(item.purchase_id, 10)}</Link><small>{new Date(item.created_at).toLocaleString("ko-KR")}</small></td><td>{item.status}</td><td>{credits(item.amount_units)} PBLC</td><td><SeverityBadge value={item.audit_severity} /></td></tr>)}</tbody></table></div>}</section>
  </main>;
}
