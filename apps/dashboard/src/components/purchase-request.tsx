"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { AEGIS_PENDING_PURCHASE_KEY, AEGIS_PRIORITY_OPTIONS, AEGIS_REQUEST_SCHEMA_VERSION, allowsIndependentRequest, matchesPendingRequest, promptHash, resolvePendingRequest } from "@/lib/aegis";
import type { PendingResolution } from "@/lib/aegis";
import { api } from "@/lib/api";
import type { PurchaseDetail } from "@/lib/types";

type Mode = "checking" | "mock" | "live" | "unavailable";
type CardState = "ready" | "running" | "success" | "error" | "superseded";
type Result = { output?: string; summary: string };
type Card = { id: number; prompt: string; budget: string; priority: string; state: CardState; purchaseId?: string; error?: string; result?: Result };

const UNITS = 1_000_000;
const MAX_BUDGET_UNITS = 1_000_000;

function budgetUnits(value: string) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  const units = Math.round(parsed * UNITS);
  if (!Number.isFinite(parsed) || parsed <= 0 || units < 1 || units > MAX_BUDGET_UNITS) throw new Error("예산은 0보다 크고 AEGIS 1개 이하여야 합니다.");
  return units;
}
function detail(purchaseId: string) { return api<PurchaseDetail>(`/purchases/${encodeURIComponent(purchaseId)}`); }
function errorText(value: unknown) { return value instanceof Error ? value.message : "구매 실행에 실패했습니다."; }
function displayAmount(amount: number, symbol: string) { return `${(amount / UNITS).toFixed(6).replace(/0+$/, "").replace(/\.$/, "")} ${symbol}`; }

export function PurchaseRequest() {
  const [draft, setDraft] = useState("");
  const [budget, setBudget] = useState("");
  const [priority, setPriority] = useState("auto");
  const [cards, setCards] = useState<Card[]>([]);
  const [pending, setPending] = useState<PendingResolution | null>(null);
  const [mode, setMode] = useState<Mode>("checking");
  const [symbol, setSymbol] = useState("토큰");
  const [busy, setBusy] = useState(false);
  const executionLock = useRef(false);
  const newestCard = useRef<HTMLDivElement>(null);

  const refreshPending = useCallback(async (): Promise<PendingResolution> => {
    for (const key of ["aegis:purchase-request-id", "pbl:purchase-request-id", "pbl:normal-experiment-purchase-id"]) window.localStorage.removeItem(key);
    setPending(null);
    const resolution = await resolvePendingRequest(window.localStorage, detail);
    setPending(resolution);
    return resolution;
  }, []);

  useEffect(() => { void refreshPending(); }, [refreshPending]);
  useEffect(() => {
    let alive = true;
    void api<{ execution_mode?: unknown; token_symbol?: unknown }>("/health").then((health) => {
      if (!alive) return;
      if (health.execution_mode === "mock" || health.execution_mode === "live") {
        setMode(health.execution_mode);
        setSymbol(typeof health.token_symbol === "string" ? health.token_symbol : "토큰");
      } else setMode("unavailable");
    }).catch(() => { if (alive) setMode("unavailable"); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { newestCard.current?.scrollIntoView({ block: "nearest" }); }, [cards]);

  const patch = (id: number, value: Partial<Card>) => setCards((all) => all.map((card) => card.id === id ? { ...card, ...value } : card));
  function send(event?: FormEvent) {
    event?.preventDefault();
    const prompt = draft.trim();
    if (!prompt || busy) return;
    setCards((all) => [...all.map((card) => card.state === "ready" ? { ...card, state: "superseded" as const } : card), { id: Date.now(), prompt, budget, priority, state: "ready" }]);
    setDraft("");
  }
  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
  }

  async function confirm(card: Card) {
    if (executionLock.current || card.state !== "ready" || pending === null || pending.status === "blocked" || mode === "checking" || mode === "unavailable") return;
    executionLock.current = true;
    setBusy(true);
    patch(card.id, { state: "running", error: undefined });
    let purchaseId = card.purchaseId;
    try {
      const input = { promptHash: await promptHash(card.prompt), priority: card.priority === "auto" ? null : card.priority, budgetUnits: budgetUnits(card.budget) };
      const resumable = pending.status === "resume" ? pending.pending : null;
      if (!purchaseId && resumable && matchesPendingRequest(resumable, input)) purchaseId = resumable.purchaseId;
      if (!purchaseId && resumable && !allowsIndependentRequest(resumable)) throw new Error("저장된 구매의 결제가 진행 중이거나 미확정입니다. 먼저 해당 거래를 확인하세요.");
      if (!purchaseId) {
        const created = await api<{ purchase_id: string }>("/purchases", { method: "POST", body: JSON.stringify({ domain: "ai_inference", request: { requestSchema: AEGIS_REQUEST_SCHEMA_VERSION, prompt: card.prompt, ...(input.priority === null ? {} : { priority: input.priority }) }, ...(input.budgetUnits === null ? {} : { budget_units: input.budgetUnits }), policy: {} }) });
        purchaseId = created.purchase_id;
        patch(card.id, { purchaseId });
        window.localStorage.setItem(AEGIS_PENDING_PURCHASE_KEY, purchaseId);
        if ((await refreshPending()).status === "blocked") throw new Error("새 구매 요청을 다시 확인할 수 없어 실행을 중단했습니다.");
      }
      const run = await api<{ payment?: Record<string, unknown>; audit?: { severity?: string } }>(`/purchases/${encodeURIComponent(purchaseId)}/run`, { method: "POST" });
      const payment = run.payment ?? {};
      if (payment.payment_status !== "settled" || payment.state !== "SETTLED") throw new Error("결제 실행 결과가 확정 정산이 아니어서 다시 실행하지 않았습니다.");
      window.localStorage.removeItem(AEGIS_PENDING_PURCHASE_KEY);
      const provider = payment.provider_result as Record<string, unknown> | undefined;
      const output = typeof provider?.text === "string" ? provider.text : undefined;
      const facts = [
        `정산 ${payment.payment_status}`,
        typeof payment.amount_units === "number" ? `금액 ${displayAmount(payment.amount_units, symbol)}` : null,
        typeof payment.provider_model_id === "string" ? `모델 ${payment.provider_model_id}` : null,
        run.audit?.severity ? `감사 ${run.audit.severity}` : null,
      ].filter((value): value is string => Boolean(value));
      patch(card.id, { state: "success", purchaseId, result: { output, summary: facts.join(" · ") } });
      void refreshPending();
    } catch (reason) {
      patch(card.id, { state: "error", purchaseId, error: errorText(reason) });
      void refreshPending();
    } finally {
      executionLock.current = false;
      setBusy(false);
    }
  }

  const confirmBlocked = busy || pending === null || pending.status === "blocked" || mode === "checking" || mode === "unavailable";
  return <section className="requestChatSurface" id="request-chat">
    <header className="requestChatHeader"><div><p className="eyebrow">BUYER AGENT CHAT</p><h1>무엇을 도와드릴까요?</h1><p>전송은 대화만 만들고, 카드의 명시 실행만 구매를 시작합니다.</p></div><div className="requestChatActions"><span className={`badge ${mode === "live" ? "normal" : "caution"}`}>{mode === "live" ? `실제 Base Sepolia ${symbol} 결제 · Mock Provider` : mode === "mock" ? `Mock 결제 · ${symbol}` : mode === "checking" ? "결제 모드 확인 중" : "결제 모드 확인 불가"}</span><button type="button" onClick={() => { setCards([]); setDraft(""); }} disabled={busy}>새 대화</button></div></header>
    <div className="chatThread" aria-live="polite">
      {cards.length === 0 && <div className="chatWelcome">메시지를 보내 구매 요청을 작성하세요. 전송은 결제하지 않습니다.</div>}
      {cards.map((card) => <article className="chatTurn" data-testid="purchase-card" data-status={card.state} data-purchase-id={card.purchaseId} key={card.id}>
        <div className="chatBubble user"><span>나</span><p>{card.prompt}</p></div>
        <div className={`purchase-confirmation-card ${card.state}`}>
          <strong>{card.state === "success" ? "구매 결과" : card.state === "running" ? "구매 실행 중" : card.state === "error" ? "실행 실패" : card.state === "superseded" ? "새 요청이 작성되었습니다" : "구매 요청 확인"}</strong>
          <dl><div><dt>예산</dt><dd>{card.budget ? `${card.budget} ${symbol}` : "지갑의 1회 한도"}</dd></div><div><dt>우선순위</dt><dd>{AEGIS_PRIORITY_OPTIONS.find((item) => item.value === card.priority)?.label ?? card.priority}</dd></div></dl>
          {card.state === "ready" && <><p>{mode === "live" ? `실제 Base Sepolia ${symbol} 전송이 한 번 발생할 수 있으며 Provider 응답은 Mock입니다.` : "Mock 결제에서는 토큰을 전송하지 않습니다."}</p><p>이 카드의 고정 요청·예산·우선순위로 한 번만 결제를 실행합니다. 최대 8,000 출력 토큰을 반영한 모델별 고정 선결제액입니다.</p><button className="primary" type="button" data-purchase-confirm onClick={() => void confirm(card)} disabled={confirmBlocked}>동의하고 구매 실행</button></>}
          {card.state === "running" && <p>결정·결제·감사 증거를 기록하고 있습니다.</p>}
          {card.state === "error" && <><p className="riskText">{card.error}</p>{card.purchaseId && <Link href={`/purchases/${encodeURIComponent(card.purchaseId)}`}>거래 상세와 증거 보기 →</Link>}<button type="button" onClick={() => { patch(card.id, { state: "ready" }); void confirm({ ...card, state: "ready" }); }} disabled={confirmBlocked}>같은 요청 다시 시도</button></>}
          {card.state === "success" && <div data-testid="purchase-result"><p>{card.result?.summary}</p>{card.result?.output && <pre>{card.result.output}</pre>}<p>Provider 응답은 Mock으로 기록됩니다.</p>{card.purchaseId && <Link href={`/purchases/${encodeURIComponent(card.purchaseId)}`}>거래 상세와 증거 보기 →</Link>}</div>}
          {card.state === "superseded" && <p>이 카드는 실행하지 않았습니다.</p>}
        </div>
      </article>)}
      <div ref={newestCard} />
      {pending?.status === "blocked" && <div className="notice risk">저장된 미완료 구매를 확인하지 못했습니다. <button type="button" onClick={() => void refreshPending()} disabled={busy}>다시 확인</button></div>}
    </div>
    <form className="chatComposerBar" id="request-composer" onSubmit={send}><details className="chatSettings" onClick={(event) => { if (busy) event.preventDefault(); }}><summary>예산·우선순위 설정</summary><div><label>예산({symbol}, 선택)<input name="budget" type="number" min="0.000001" max="1" step="0.000001" value={budget} onChange={(event) => setBudget(event.target.value)} disabled={busy} /></label><label>우선순위<select name="priority" value={priority} onChange={(event) => setPriority(event.target.value)} disabled={busy}>{AEGIS_PRIORITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label></div></details><textarea aria-label="메시지" name="prompt" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onComposerKeyDown} disabled={busy} placeholder="구매할 AI 작업을 입력하세요" /><button className="primary" aria-label="메시지 보내기" type="submit" disabled={busy || !draft.trim()}>보내기</button></form>
  </section>;
}
