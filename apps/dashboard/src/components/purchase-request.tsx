"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

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

/** PBLC V2 is a 6-decimal token, so one unit is 1e-6 nominal USD. */
const PBLC_UNITS_PER_TOKEN = 1_000_000;

type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

type Phase = "ready" | "invalid" | "creating" | "running";
type RuntimeMode = "checking" | "mock" | "live" | "unavailable";

function toBudgetUnits(value: FormDataEntryValue | null): number | null {
  const text = String(value ?? "").trim();
  if (text === "") return null;
  const amount = Number(text);
  if (!Number.isFinite(amount) || amount <= 0 || amount > 1) {
    throw new Error("예산은 0보다 크고 결제 토큰 1개 이하여야 합니다.");
  }
  const units = Math.round(amount * PBLC_UNITS_PER_TOKEN);
  if (units <= 0) throw new Error("예산은 최소 0.000001 토큰입니다.");
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [messageDraft, setMessageDraft] = useState("");
  const [budget, setBudget] = useState("");
  const [priority, setPriority] = useState("auto");
  const [phase, setPhase] = useState<Phase>("ready");
  const [pending, setPending] = useState<PendingResolution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runtimeMode, setRuntimeMode] = useState<RuntimeMode>("checking");
  const [tokenSymbol, setTokenSymbol] = useState("토큰");

  const resolvePending = useCallback(async () => {
    // User-approved PBLC record cleanup. The new AEGIS pending key is untouched.
    for (const key of ["aegis:purchase-request-id", "pbl:purchase-request-id", "pbl:normal-experiment-purchase-id"]) {
      window.localStorage.removeItem(key);
    }
    // The stored id is only trusted after a public detail GET proves what it is. Keys
    // written by the superseded PBLC runtime were removed explicitly above.
    // `null` re-enters the checking state, so a re-check blocks the run exactly like
    // the first one does.
    setPending(null);
    setPending(await resolvePendingRequest(window.localStorage, loadPurchase));
  }, []);

  useEffect(() => {
    void resolvePending();
  }, [resolvePending]);

  useEffect(() => {
    let active = true;
    void api<{ execution_mode?: unknown; token_symbol?: string }>("/health")
      .then((health) => {
        if (active) setRuntimeMode(health.execution_mode === "live" ? "live" : "mock");
        if (active) setTokenSymbol(health.token_symbol || "토큰");
      })
      .catch(() => {
        if (active) setRuntimeMode("unavailable");
      });
    return () => { active = false; };
  }, []);

  const busy = phase === "creating" || phase === "running";
  const checking = pending === null;
  const blocked = pending?.status === "blocked";
  const resumable = pending?.status === "resume" ? pending.pending : null;
  const prompt = useMemo(
    () => messages.filter((message) => message.role === "user").map((message) => message.text).join("\n\n"),
    [messages],
  );
  const hasUnsentMessage = messageDraft.trim() !== "";
  const submittable = canSubmitRequest(pending, {
    busy,
    acknowledged: acknowledged && prompt.trim() !== "" && !hasUnsentMessage,
  });

  function revokeConsent() {
    setAcknowledged(false);
  }

  function sendMessage() {
    const text = messageDraft.trim();
    if (text === "" || busy) return;
    revokeConsent();
    setMessages((current) => [
      ...current,
      { id: Date.now(), role: "user", text },
      {
        id: Date.now() + 1,
        role: "assistant",
        text: "초안 안내: 이 메시지를 구매 요청 초안에 추가했습니다. 예산·우선순위와 실행 동의를 다시 확인한 뒤에만 구매 실행을 선택할 수 있습니다.",
      },
    ]);
    setMessageDraft("");
  }

  function startNewConversation() {
    if (busy) return;
    revokeConsent();
    setMessages([]);
    setMessageDraft("");
  }

  async function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!submittable) return;

    setError(null);
    let budgetUnits: number | null;
    try {
      budgetUnits = toBudgetUnits(budget);
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
          ? resumable.settled
            ? "정산된 구매의 결과·감사 이어서 받기"
            : "같은 요청이면 기존 구매 이어서 실행"
          : pending?.status === "completed"
            ? "정산·감사 완료 — 거래 상세 확인"
          : "구매 에이전트 실행";

  return (
    <div className="experimentLayout">
      <section className="panel">
        <div className="sectionHead">
          <div>
            <p className="eyebrow">PURCHASE REQUEST</p>
            <h2>AI 구매 요청</h2>
          </div>
          <span className={`badge ${runtimeMode === "live" ? "normal" : "caution"}`}>
            {runtimeMode === "live"
              ? `실제 ${tokenSymbol} 결제 · Mock Provider`
              : runtimeMode === "checking"
                ? "결제 모드 확인 중"
                : runtimeMode === "unavailable"
                  ? "결제 모드 확인 불가"
                  : `Mock 결제 · ${tokenSymbol} 조건`}
          </span>
        </div>

        <form className="experimentForm" onSubmit={run}>
          <input name="prompt" type="hidden" value={prompt} readOnly />

          <section className="requestChat" aria-label="구매 요청 대화 초안">
            <div className="requestChatHead">
              <div>
                <span>구매 요청 대화</span>
                <small>메시지를 보내도 구매·서명·결제는 생성되지 않습니다.</small>
              </div>
              <button type="button" onClick={startNewConversation} disabled={busy}>새 대화</button>
            </div>
            <div className="chatTranscript" aria-live="polite">
              {messages.length === 0 ? (
                <p className="chatEmpty">초안 도우미: 아래에 요청을 입력해 대화를 시작하세요. 이 화면은 모델 호출이나 구매를 하지 않습니다.</p>
              ) : messages.map((message) => (
                <article className={`chatMessage ${message.role}`} key={message.id}>
                  <strong>{message.role === "user" ? "나" : "초안 도우미"}</strong>
                  <p>{message.text}</p>
                </article>
              ))}
            </div>
            <label className="chatComposer">
              <span>메시지</span>
              <textarea
                value={messageDraft}
                onChange={(event) => {
                  revokeConsent();
                  setMessageDraft(event.target.value);
                }}
                rows={3}
                placeholder="추가 조건이나 원하는 답변 형식을 입력하세요"
              />
            </label>
            <div className="chatComposerActions">
              <button type="button" onClick={sendMessage} disabled={busy || messageDraft.trim() === ""}>메시지 보내기</button>
              <small>보내기는 화면의 대화 초안만 갱신합니다.</small>
            </div>
          </section>

          <label>
            <span>예산({tokenSymbol}, 선택)</span>
            <input name="budget" type="number" min="0.000001" max="1" step="0.000001" value={budget} onChange={(event) => { revokeConsent(); setBudget(event.target.value); }} placeholder="비워두면 지갑의 1회 한도 적용" />
            <small>
              {tokenSymbol}는 소수점 6자리이고 1 {tokenSymbol}는 명목 1 USD로 환산합니다. 명목 환산값이며 실제
              화폐 가치가 아닙니다.
            </small>
          </label>

          <label>
            <span>우선순위</span>
            <select name="priority" value={priority} onChange={(event) => { revokeConsent(); setPriority(event.target.value); }}>
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
            <div><span>결제 자산</span><strong>{tokenSymbol} · 표준 작업 최대 8,000 출력 토큰 기준</strong></div>
            <div><span>실행·정산</span><strong>{runtimeMode === "live" ? "Mock Provider · 실제 x402 Facilitator 정산" : "Mock Provider · Mock Facilitator"}</strong></div>
            <div><span>감사 범위</span><strong>선택 · 결제 · 전달</strong></div>
          </div>

          <label className="paymentConsent">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
            <span>
              현재 대화 초안과 예산·우선순위를 확인했으며, 구매 에이전트가 aa-three-factor-v1 정책으로 고른 모델별 표준 작업 선결제 상한을 x402 exact +
              ERC-3009 승인으로 한 번만 요청하고, 판단·결제·감사 증거를 같은 purchaseId로
              기록하는 것을 확인했습니다. 서버가 <strong>실제 결제 모드</strong>로 구성된 경우에는
              이 실행이 선택된 판매자 지갑에 실제 Base Sepolia {tokenSymbol}를 한 번 전송할 수 있으며,
              Provider 응답은 계속 모의 실행으로 표시됩니다. Mock 결제 모드에서는 토큰을 전송하지
              않습니다.
            </span>
          </label>

          {hasUnsentMessage && (
            <div className="notice" role="status">
              작성 중인 메시지를 먼저 보내거나 지워 주세요. 보내지 않은 내용은 구매 요청 초안에 포함되지 않습니다.
            </div>
          )}

          {error && <div className="notice risk" role="alert">실행이 완료되지 않았습니다: {error}</div>}

          {checking && (
            <div className="notice" role="status">
              저장된 pending purchaseId를 확인하는 중입니다. 확인이 끝나기 전에는 새 구매를
              만들지도, 기존 구매를 실행하지도 않습니다.
            </div>
          )}

          {blocked && (
            <div className="notice risk" role="alert">
              저장된 신규 정책 purchaseId {short(pending?.purchaseId, 10)}를 조회하지 못해 재개
              여부를 확인할 수 없습니다. 확인 전에는 실행하지 않습니다.{" "}
              <button type="button" onClick={() => void resolvePending()}>다시 확인</button>
            </div>
          )}

          {resumable && (
            <div className="notice">
              {resumable.settled
                ? "정산은 이미 기록됐습니다. 같은 요청으로 결과와 감사를 이어서 받을 수 있으며, 이 동작은 새 서명·Facilitator 호출·토큰 전송을 하지 않습니다."
                : "진행 중인 구매가 있습니다. 저장된 요청과 같을 때만 이어서 실행하고, 프롬프트·우선순위·예산이 달라지면 새 구매를 만듭니다."}{" "}
              {short(resumable.purchaseId, 10)}
              <small>
                저장된 요청: 프롬프트 해시 {short(resumable.promptHash, 10)} · priority{" "}
                {resumable.originalPriority ?? "미지정(자동 분류)"} · 예산{" "}
                {credits(resumable.budgetUnits)} {tokenSymbol}
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
              있습니다. 이 기록은 새 정책 요청으로 다시 실행하지 않고 그대로 보존합니다.{" "}
              <Link href={`/purchases/${encodeURIComponent(item.purchaseId)}`}>
                과거 거래 상세 보기 →
              </Link>
            </div>
          ))}

          <div className="experimentActions">
            <button className="primary" type="submit" disabled={!submittable}>명시적으로 동의하고 {buttonLabel}</button>
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
          <li><span><strong>정확히 한 번 결제</strong><small>결제 실행 모듈이 고정 금액을 서명하고, 실제 모드에서는 Facilitator가 정산합니다.</small></span></li>
          <li><span><strong>결과·감사 반환</strong><small>Mock Provider 응답과 결제·감사 결과를 읽기 전용 대시보드에 연결합니다.</small></span></li>
        </ol>
        <p className="panelNote">이 페이지는 구매 요청 전용입니다. 거래 조회와 감사는 대시보드에서 수행합니다.</p>
      </aside>
    </div>
  );
}
