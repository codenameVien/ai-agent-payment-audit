"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  AEGIS_PENDING_PURCHASE_KEY,
  AEGIS_PRIORITY_OPTIONS,
  AEGIS_REQUEST_SCHEMA_VERSION,
  canSubmitRequest,
  matchesPendingRequest,
  promptHash,
  resolvePendingRequest,
} from "@/lib/aegis";
import type { PendingResolution } from "@/lib/aegis";
import { api, credits, short } from "@/lib/api";
import type { PurchaseDetail } from "@/lib/types";

const DEFAULT_PROMPT = "사용자 요구에 가장 적합한 AI 모델을 선택해 한 문장으로 응답해줘.";
/** AEGIS is a 6-decimal token, so one unit is 1e-6 nominal USD. */
const AEGIS_UNITS_PER_TOKEN = 1_000_000;

type Phase = "ready" | "invalid" | "creating" | "running";

function toBudgetUnits(value: FormDataEntryValue | null): number | null {
  const text = String(value ?? "").trim();
  if (text === "") return null;
  const amount = Number(text);
  if (!Number.isFinite(amount) || amount <= 0 || amount > 1) {
    throw new Error("예산은 0보다 크고 1 AEGIS 이하여야 합니다.");
  }
  const units = Math.round(amount * AEGIS_UNITS_PER_TOKEN);
  if (units <= 0) throw new Error("예산은 최소 0.000001 AEGIS입니다.");
  return units;
}

/** 401/403 here is a local API access problem, never a missing public login. */
function isAccessError(message: string | null): boolean {
  return message !== null && (message.startsWith("401") || message.startsWith("403"));
}

function loadPurchase(purchaseId: string): Promise<PurchaseDetail> {
  return api<PurchaseDetail>(`/purchases/${encodeURIComponent(purchaseId)}`);
}

export function PurchaseRequest() {
  const router = useRouter();
  const [acknowledged, setAcknowledged] = useState(false);
  const [phase, setPhase] = useState<Phase>("ready");
  const [pending, setPending] = useState<PendingResolution | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resolvePending = useCallback(async () => {
    // The stored id is only trusted after a public detail GET proves what it is. Keys
    // written by the superseded PBLC runtime are listed for lookup and never touched.
    // `null` re-enters the checking state, so a re-check blocks the run exactly like
    // the first one does.
    setPending(null);
    setPending(await resolvePendingRequest(window.localStorage, loadPurchase));
  }, []);

  useEffect(() => {
    void resolvePending();
  }, [resolvePending]);

  const busy = phase === "creating" || phase === "running";
  const checking = pending === null;
  const blocked = pending?.status === "blocked";
  const resumable = pending?.status === "resume" ? pending.pending : null;
  const submittable = canSubmitRequest(pending, { busy, acknowledged });

  async function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!submittable) return;

    setError(null);
    const data = new FormData(event.currentTarget);
    const prompt = String(data.get("prompt"));
    const priority = String(data.get("priority"));
    let budgetUnits: number | null;
    try {
      budgetUnits = toBudgetUnits(data.get("budget"));
    } catch (reason) {
      setPhase("invalid");
      setError(reason instanceof Error ? reason.message : "예산 입력이 올바르지 않습니다.");
      return;
    }

    try {
      const input = {
        promptHash: await promptHash(prompt),
        priority: priority === "auto" ? null : priority,
        budgetUnits,
      };
      // A pending purchase is reused only for the request it was created from.
      let currentPurchaseId =
        resumable !== null && matchesPendingRequest(resumable, input)
          ? resumable.purchaseId
          : null;

      if (currentPurchaseId === null) {
        setPhase("creating");
        const created = await api<{ purchase_id: string }>("/purchases", {
          method: "POST",
          body: JSON.stringify({
            domain: "ai_inference",
            request: {
              requestSchema: AEGIS_REQUEST_SCHEMA_VERSION,
              prompt,
              // "auto" sends no priority at all, so the server classifies the request.
              ...(input.priority === null ? {} : { priority: input.priority }),
            },
            ...(budgetUnits === null ? {} : { budget_units: budgetUnits }),
            policy: {},
          }),
        });
        currentPurchaseId = created.purchase_id;
        window.localStorage.setItem(AEGIS_PENDING_PURCHASE_KEY, currentPurchaseId);
        await resolvePending();
      }

      setPhase("running");
      await api(`/purchases/${encodeURIComponent(currentPurchaseId)}/run`, {
        method: "POST",
      });
      window.localStorage.removeItem(AEGIS_PENDING_PURCHASE_KEY);
      router.push(`/purchases/${encodeURIComponent(currentPurchaseId)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "구매 에이전트 실행에 실패했습니다.");
      setPhase("ready");
    }
  }

  if (isAccessError(error)) {
    return (
      <section className="panel">
        <p className="notice risk" role="alert">
          로컬 감사 API가 요청을 거부했습니다({error}). 로그인 화면 없이 동작하는 단일 사용자
          데모이므로, API 서버 실행과 로컬 소유자·허용 출처 설정을 확인해 주세요.
        </p>
        <p className="panelNote">
          이 화면은 공개 다중 사용자 인증을 구현하지 않습니다. 내부 API 토큰과 서명 키는
          브라우저로 전달되지 않습니다.
        </p>
      </section>
    );
  }

  const buttonLabel = phase === "creating"
    ? "구매 요청 기록 중…"
    : phase === "running"
      ? "비교·선택·결제·감사 진행 중…"
      : checking
        ? "저장된 요청 확인 중…"
        : resumable
          ? "같은 요청이면 기존 구매 이어서 실행"
          : "구매 에이전트 실행";

  return (
    <div className="experimentLayout">
      <section className="panel">
        <div className="sectionHead">
          <div>
            <p className="eyebrow">PURCHASE REQUEST</p>
            <h2>AI 구매 요청</h2>
          </div>
          <span className="badge caution">Mock 실행 · 준비 토큰</span>
        </div>

        <form className="experimentForm" onSubmit={run}>
          <label>
            <span>사용자 요청</span>
            <textarea name="prompt" required minLength={1} rows={5} defaultValue={DEFAULT_PROMPT} />
          </label>

          <label>
            <span>예산(AEGIS, 선택)</span>
            <input name="budget" type="number" min="0.000001" max="1" step="0.000001" placeholder="비워두면 지갑의 1회 한도 적용" />
            <small>
              AEGIS는 소수점 6자리이고 1 AEGIS는 명목 1 USD로 환산합니다. 명목 환산값이며 실제
              화폐 가치가 아닙니다.
            </small>
          </label>

          <label>
            <span>우선순위</span>
            <select name="priority" defaultValue="auto">
              {AEGIS_PRIORITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} — {option.note}
                </option>
              ))}
            </select>
            <small>
              선택한 우선순위는 고정 가중치표로 그대로 적용되며, 별도 가중치 승인 단계는
              없습니다. &quot;요청에서 자동 판단&quot;은 priority를 보내지 않아 서버가 요청 문구로
              분류합니다.
            </small>
          </label>

          <div className="experimentTerms" aria-label="구매 실행 조건">
            <div><span>구매 주체</span><strong>구매 에이전트</strong></div>
            <div><span>결제 자산</span><strong>AEGIS · 발행 준비 상태</strong></div>
            <div><span>실행·정산</span><strong>Mock Provider · Mock Facilitator</strong></div>
            <div><span>감사 범위</span><strong>선택 · 결제 · 전달</strong></div>
          </div>

          <label className="paymentConsent">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
            <span>
              구매 에이전트가 aa-three-factor-v1 정책으로 고른 고정 선결제 금액을 x402 exact +
              ERC-3009 승인으로 한 번만 요청하고, 판단·결제·감사 증거를 같은 purchaseId로
              기록하는 것을 확인했습니다. 제공자 호출과 정산은 모의 구성이며 실제 테스트넷 토큰
              전송은 실행하지 않습니다.
            </span>
          </label>

          {error && <div className="notice risk" role="alert">실행이 완료되지 않았습니다: {error}</div>}

          {checking && (
            <div className="notice" role="status">
              저장된 pending purchaseId를 확인하는 중입니다. 확인이 끝나기 전에는 새 구매를
              만들지도, 기존 구매를 실행하지도 않습니다.
            </div>
          )}

          {blocked && (
            <div className="notice risk" role="alert">
              저장된 AEGIS purchaseId {short(pending?.purchaseId, 10)}를 조회하지 못해 재개
              여부를 확인할 수 없습니다. 확인 전에는 실행하지 않습니다.{" "}
              <button type="button" onClick={() => void resolvePending()}>다시 확인</button>
            </div>
          )}

          {resumable && (
            <div className="notice">
              진행 중인 AEGIS 구매 {short(resumable.purchaseId, 10)}가 있습니다. 저장된 요청과
              같을 때만 이어서 실행하고, 프롬프트·우선순위·예산이 달라지면 새 구매를 만듭니다.
              <small>
                저장된 요청: 프롬프트 해시 {short(resumable.promptHash, 10)} · priority{" "}
                {resumable.originalPriority ?? "미지정(자동 분류)"} · 예산{" "}
                {credits(resumable.budgetUnits)} AEGIS
              </small>
            </div>
          )}

          {pending?.status === "foreign" && (
            <div className="notice">
              저장된 purchaseId {short(pending.purchaseId, 10)}는 aegis-aa-v1 요청이 아니어서
              재개하지 않습니다.{" "}
              <Link href={`/purchases/${encodeURIComponent(pending.purchaseId ?? "")}`}>
                과거 거래 상세 보기 →
              </Link>
            </div>
          )}

          {pending?.status === "completed" && (
            <div className="notice">
              저장된 purchaseId {short(pending.purchaseId, 10)}는 이미 정산·감사가 끝나 다시
              실행하지 않습니다.{" "}
              <Link href={`/purchases/${encodeURIComponent(pending.purchaseId ?? "")}`}>
                거래 상세 보기 →
              </Link>
            </div>
          )}

          {pending?.historical.map((item) => (
            <div className="notice" key={item.key}>
              과거 PBLC 정책으로 남은 purchaseId {short(item.purchaseId, 10)}({item.key})가
              있습니다. 이 기록은 새 AEGIS 요청으로 다시 실행하지 않고 그대로 보존합니다.{" "}
              <Link href={`/purchases/${encodeURIComponent(item.purchaseId)}`}>
                과거 거래 상세 보기 →
              </Link>
            </div>
          ))}

          <div className="experimentActions">
            <button className="primary" type="submit" disabled={!submittable}>{buttonLabel}</button>
            {resumable && (
              <Link href={`/purchases/${encodeURIComponent(resumable.purchaseId)}`}>
                현재 거래 상세 보기 →
              </Link>
            )}
          </div>
        </form>
      </section>

      <aside className="panel modeNotice">
        <p className="eyebrow">BUYER AGENT FLOW</p>
        <h2>구매 에이전트가 수행하는 일</h2>
        <ol className="experimentSteps">
          <li><span><strong>요청 정규화</strong><small>예산과 우선순위를 확정하고 분류 근거를 남깁니다.</small></span></li>
          <li><span><strong>AA 스냅샷 캡처</strong><small>명시적으로 매핑된 모델의 가격·완료시간·성능을 그대로 저장합니다.</small></span></li>
          <li><span><strong>3요소 비교·선택</strong><small>하드 필터 뒤 고정 가중치로 점수를 계산하고 후보와 제외 사유를 기록합니다.</small></span></li>
          <li><span><strong>정확히 한 번 결제</strong><small>결제 실행 모듈이 고정 금액을 서명하고 Facilitator 응답을 상태 근거로 기록합니다.</small></span></li>
          <li><span><strong>결과·감사 반환</strong><small>모의 실행 응답과 감사 결과를 읽기 전용 대시보드에 연결합니다.</small></span></li>
        </ol>
        <p className="panelNote">이 페이지는 구매 요청 전용입니다. 거래 조회와 감사는 대시보드에서 수행합니다.</p>
      </aside>
    </div>
  );
}
