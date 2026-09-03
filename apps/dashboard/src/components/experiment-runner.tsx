"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { api, short } from "@/lib/api";

const NORMAL_BUDGET_UNITS = 100_000;
const PENDING_PURCHASE_KEY = "pbl:normal-experiment-purchase-id";
const DEFAULT_PROMPT =
  "Base Sepolia 결제 감사 정상 시나리오를 확인할 수 있도록 한 문장으로 응답해줘.";

type Phase = "ready" | "creating" | "running";

export function ExperimentRunner() {
  const router = useRouter();
  const [acknowledged, setAcknowledged] = useState(false);
  const [phase, setPhase] = useState<Phase>("ready");
  const [purchaseId, setPurchaseId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPurchaseId(window.sessionStorage.getItem(PENDING_PURCHASE_KEY));
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
        const created = await api<{ purchase_id: string }>("/purchases", {
          method: "POST",
          body: JSON.stringify({
            domain: "ai_inference",
            request: {
              prompt: String(data.get("prompt")),
              priority: "balanced",
            },
            budget_units: NORMAL_BUDGET_UNITS,
            policy: { priority: "balanced" },
          }),
        });
        currentPurchaseId = created.purchase_id;
        setPurchaseId(currentPurchaseId);
        window.sessionStorage.setItem(PENDING_PURCHASE_KEY, currentPurchaseId);
      }

      setPhase("running");
      await api(`/purchases/${encodeURIComponent(currentPurchaseId)}/run`, {
        method: "POST",
      });
      window.sessionStorage.removeItem(PENDING_PURCHASE_KEY);
      router.push(`/purchases/${encodeURIComponent(currentPurchaseId)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "정상 거래 실행에 실패했습니다.");
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

  const buttonLabel =
    phase === "creating"
      ? "거래 기록 생성 중…"
      : phase === "running"
        ? "선택·결제·감사 진행 중…"
        : purchaseId
          ? "생성된 거래 이어서 실행"
          : "정상 거래 1건 실행";

  return (
    <div className="experimentLayout">
      <section className="panel">
        <div className="sectionHead">
          <div>
            <p className="eyebrow">Normal transaction</p>
            <h2>정상 거래 실험</h2>
          </div>
          <span className="badge normal">실제 테스트넷</span>
        </div>

        <form className="experimentForm" onSubmit={run}>
          <label>
            <span>AI 요청</span>
            <textarea name="prompt" required minLength={1} rows={5} defaultValue={DEFAULT_PROMPT} />
          </label>

          <div className="experimentTerms" aria-label="고정 실행 조건">
            <div><span>최대 결제</span><strong>0.1 PBLC</strong></div>
            <div><span>선택 기준</span><strong>균형 balanced</strong></div>
            <div><span>네트워크</span><strong>Base Sepolia</strong></div>
            <div><span>감사 범위</span><strong>선택 · 결제 · 전달</strong></div>
          </div>

          <label className="paymentConsent">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            <span>
              구매 에이전트 지갑에서 판매자 지갑으로 최대 0.1 PBLC가 실제 Base Sepolia에서
              전송되고, 거래·감사 증거가 MongoDB에 기록되는 것을 확인했습니다.
            </span>
          </label>

          {error && (
            <div className="notice risk" role="alert">
              실행이 완료되지 않았습니다: {error}
            </div>
          )}

          {purchaseId && (
            <div className="notice">
              생성된 purchaseId {short(purchaseId, 10)}를 유지하고 있습니다. 재시도해도 새
              구매를 만들지 않습니다.
            </div>
          )}

          <div className="experimentActions">
            <button className="primary" type="submit" disabled={busy || !acknowledged}>
              {buttonLabel}
            </button>
            {purchaseId && <Link href={`/purchases/${purchaseId}`}>현재 거래 상세 보기 →</Link>}
          </div>
        </form>
      </section>

      <aside className="panel modeNotice">
        <p className="eyebrow">Evidence path</p>
        <h2>이 버튼이 확인하는 것</h2>
        <ol className="experimentSteps">
          <li><span><strong>AI 선택</strong><small>Gemini·Nemotron 견적을 비교하고 선택 근거를 고정합니다.</small></span></li>
          <li><span><strong>x402 결제</strong><small>자체 ERC-20 PBLC의 실제 테스트넷 정산을 확인합니다.</small></span></li>
          <li><span><strong>응답 전달</strong><small>현재 provider adapter는 mock이며 모델·응답 해시 결속을 시험합니다.</small></span></li>
          <li><span><strong>감사 반영</strong><small>정상 여부와 증거 타임라인을 읽기 전용 대시보드에 표시합니다.</small></span></li>
        </ol>
        <p className="panelNote">
          비정상 실험은 아직 포함하지 않습니다. 어떤 규칙을 어떻게 위반시킬지 검증 설계를
          확정한 뒤 별도 시나리오로 추가합니다.
        </p>
      </aside>
    </div>
  );
}
