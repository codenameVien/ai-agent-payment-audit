"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { api, short } from "@/lib/api";

const PENDING_PURCHASE_KEY = "pbl:purchase-request-id";
const LEGACY_PENDING_PURCHASE_KEY = "pbl:normal-experiment-purchase-id";
const DEFAULT_PROMPT = "사용자 요구에 가장 적합한 AI 모델을 선택해 한 문장으로 응답해줘.";

type Phase = "ready" | "creating" | "running";
type Priority = "balanced" | "quality" | "price" | "speed";

function toBudgetUnits(value: FormDataEntryValue | null): number | undefined {
  const text = String(value ?? "").trim();
  if (text === "") return undefined;
  const credits = Number(text);
  if (!Number.isFinite(credits) || credits <= 0 || credits > 1) {
    throw new Error("예산은 0보다 크고 1 PBLC 이하여야 합니다.");
  }
  const units = Math.round(credits * 1_000_000);
  if (units <= 0) throw new Error("예산은 최소 0.000001 PBLC입니다.");
  return units;
}

export function PurchaseRequest() {
  const router = useRouter();
  const [acknowledged, setAcknowledged] = useState(false);
  const [phase, setPhase] = useState<Phase>("ready");
  const [purchaseId, setPurchaseId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const pending = window.localStorage.getItem(PENDING_PURCHASE_KEY)
      ?? window.localStorage.getItem(LEGACY_PENDING_PURCHASE_KEY);
    if (pending) {
      setPurchaseId(pending);
      window.localStorage.setItem(PENDING_PURCHASE_KEY, pending);
    }
  }, []);

  const busy = phase !== "ready";

  async function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !acknowledged) return;

    setError(null);
    let currentPurchaseId = purchaseId;
    try {
      if (currentPurchaseId === null) {
        setPhase("creating");
        const data = new FormData(event.currentTarget);
        const priority = String(data.get("priority")) as Priority;
        const budgetUnits = toBudgetUnits(data.get("budget"));
        const created = await api<{ purchase_id: string }>("/purchases", {
          method: "POST",
          body: JSON.stringify({
            domain: "ai_inference",
            request: {
              prompt: String(data.get("prompt")),
              priority,
            },
            ...(budgetUnits === undefined ? {} : { budget_units: budgetUnits }),
            policy: { priority },
          }),
        });
        currentPurchaseId = created.purchase_id;
        setPurchaseId(currentPurchaseId);
        window.localStorage.setItem(PENDING_PURCHASE_KEY, currentPurchaseId);
      }

      setPhase("running");
      await api(`/purchases/${encodeURIComponent(currentPurchaseId)}/run`, {
        method: "POST",
      });
      window.localStorage.removeItem(PENDING_PURCHASE_KEY);
      window.localStorage.removeItem(LEGACY_PENDING_PURCHASE_KEY);
      router.push(`/purchases/${encodeURIComponent(currentPurchaseId)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "구매 에이전트 실행에 실패했습니다.");
      setPhase("ready");
    }
  }

  if (error?.startsWith("401")) {
    return (
      <section className="panel">
        <p className="notice risk" role="alert">로그인 세션이 필요합니다.</p>
        <Link href="/login">MetaMask로 로그인하기 →</Link>
      </section>
    );
  }

  const buttonLabel = phase === "creating"
    ? "구매 요청 기록 중…"
    : phase === "running"
      ? "비교·선택·결제·감사 진행 중…"
      : purchaseId
        ? "기존 구매 요청 이어서 실행"
        : "구매 에이전트 실행";

  return (
    <div className="experimentLayout">
      <section className="panel">
        <div className="sectionHead">
          <div>
            <p className="eyebrow">PURCHASE REQUEST</p>
            <h2>AI 구매 요청</h2>
          </div>
          <span className="badge normal">Base Sepolia</span>
        </div>

        <form className="experimentForm" onSubmit={run}>
          <label>
            <span>사용자 요청</span>
            <textarea name="prompt" required minLength={1} rows={5} defaultValue={DEFAULT_PROMPT} />
          </label>

          <label>
            <span>예산(PBLC, 선택)</span>
            <input name="budget" type="number" min="0.000001" max="1" step="0.000001" placeholder="비워두면 지갑의 1회 한도 적용" />
          </label>

          <label>
            <span>우선순위</span>
            <select name="priority" defaultValue="balanced">
              <option value="balanced">균형</option>
              <option value="quality">품질</option>
              <option value="price">가격</option>
              <option value="speed">속도</option>
            </select>
          </label>

          <div className="experimentTerms" aria-label="구매 실행 조건">
            <div><span>구매 주체</span><strong>구매 에이전트</strong></div>
            <div><span>결제 자산</span><strong>PBLC</strong></div>
            <div><span>네트워크</span><strong>Base Sepolia</strong></div>
            <div><span>감사 범위</span><strong>선택 · 결제 · 전달</strong></div>
          </div>

          <label className="paymentConsent">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
            <span>
              구매 에이전트가 선택한 고정 견적을 PBLC로 실제 테스트넷에서 한 번 결제하고,
              판단·결제·감사 증거를 같은 purchaseId로 기록하는 것을 확인했습니다.
            </span>
          </label>

          {error && <div className="notice risk" role="alert">실행이 완료되지 않았습니다: {error}</div>}
          {purchaseId && (
            <div className="notice">
              생성된 purchaseId {short(purchaseId, 10)}를 유지하고 있습니다. 재시도해도 새 구매를 만들지 않습니다.
            </div>
          )}

          <div className="experimentActions">
            <button className="primary" type="submit" disabled={busy || !acknowledged}>{buttonLabel}</button>
            {purchaseId && <Link href={`/purchases/${purchaseId}`}>현재 거래 상세 보기 →</Link>}
          </div>
        </form>
      </section>

      <aside className="panel modeNotice">
        <p className="eyebrow">BUYER AGENT FLOW</p>
        <h2>구매 에이전트가 수행하는 일</h2>
        <ol className="experimentSteps">
          <li><span><strong>요청 분석</strong><small>예산과 우선순위를 정규화합니다.</small></span></li>
          <li><span><strong>견적 비교·선택</strong><small>Gemini·Nemotron 후보의 필터와 점수를 기록합니다.</small></span></li>
          <li><span><strong>정확히 한 번 결제</strong><small>Payment Executor에 선택된 고정 견적의 x402 결제를 요청합니다.</small></span></li>
          <li><span><strong>결과·감사 반환</strong><small>응답과 독립 온체인 검증 결과를 읽기 전용 대시보드에 연결합니다.</small></span></li>
        </ol>
        <p className="panelNote">이 페이지는 구매 요청 전용입니다. 거래 조회와 감사는 대시보드에서 수행합니다.</p>
      </aside>
    </div>
  );
}
