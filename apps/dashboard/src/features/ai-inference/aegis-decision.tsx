import {
  AEGIS_PRIORITY_LABELS,
  AEGIS_PRIORITY_REASON_LABELS,
  describeRejection,
  readAegisDecision,
  readAegisDelivery,
  readAegisSnapshot,
  shortDecimal,
} from "@/lib/aegis";
import { short, tokenAmount } from "@/lib/api";
import type { EvidenceEvent } from "@/lib/types";

const outcomeBadge: Record<string, { tone: string; label: string }> = {
  winner: { tone: "normal", label: "선택" },
  eligible: { tone: "pending", label: "후보" },
  rejected: { tone: "caution", label: "제외" },
  unscored: { tone: "pending", label: "점수 미기록" },
};

function snapshotBadge(mode: string | null): { tone: string; label: string } {
  if (mode === "live") return { tone: "normal", label: "AA live 스냅샷" };
  if (mode === "fixture") return { tone: "caution", label: "AA fixture 스냅샷" };
  return { tone: "pending", label: "AA 스냅샷 상태 확인 불가" };
}

export function AegisPurchaseDecision({ events }: { events: EvidenceEvent[] }) {
  const decision = readAegisDecision(events);
  if (decision === null) return null;
  const snapshot = readAegisSnapshot(events);
  const delivery = readAegisDelivery(events);
  const decimals = decision.token?.decimals ?? 6;
  const symbol = decision.token?.symbol ?? "단위";
  const priorityLabel =
    decision.priority.effective === null
      ? "—"
      : AEGIS_PRIORITY_LABELS[decision.priority.effective] ?? decision.priority.effective;
  const reasonLabel =
    decision.priority.reason === null
      ? "사유 미기록"
      : AEGIS_PRIORITY_REASON_LABELS[decision.priority.reason] ?? decision.priority.reason;
  const source = snapshotBadge(snapshot?.mode ?? null);

  return (
    <section className="panel">
      <div className="sectionHead">
        <div>
          <p className="eyebrow">구매 에이전트 선택 증거</p>
          <h2>가격 · 완료시간 · AA 성능 3요소 비교</h2>
        </div>
        <div className="detailMeta">
          <span className="badge pending">{decision.scoringPolicyVersion}</span>
          <span className={`badge ${source.tone}`}>{source.label}</span>
        </div>
      </div>

      <dl className="walletList">
        <div>
          <dt>적용 우선순위</dt>
          <dd>
            {priorityLabel} · {reasonLabel}
          </dd>
        </div>
        <div>
          <dt>고정 가중치(가격/완료시간/성능)</dt>
          <dd>
            {decision.weights.price ?? "—"} / {decision.weights.completionTime ?? "—"} /{" "}
            {decision.weights.intelligence ?? "—"}
          </dd>
        </div>
        <div>
          <dt>원 요청 priority</dt>
          <dd>{decision.priority.original ?? "미지정(자동 판단)"}</dd>
        </div>
        <div>
          <dt>분류 근거 키워드</dt>
          <dd>
            {decision.priority.matchedKeywords.length === 0
              ? "없음"
              : decision.priority.matchedKeywords.join(", ")}
          </dd>
        </div>
        <div>
          <dt>고정 선결제 금액</dt>
          <dd>
            {tokenAmount(decision.amountUnits, decimals)} {symbol}
          </dd>
        </div>
        <div>
          <dt>결제 토큰</dt>
          <dd title={decision.token?.address ?? undefined}>
            {decision.token?.name ?? "—"} · {decision.token?.status ?? "상태 미기록"}
          </dd>
        </div>
        <div>
          <dt>입력 토큰 추정</dt>
          <dd>
            {decision.tokens?.estimatedInputTokens ?? "—"} 입력 /{" "}
            {decision.tokens?.maxOutputTokens ?? "—"} 최대 출력
            {decision.tokens?.isEstimate ? " · 추정치" : ""}
          </dd>
        </div>
        <div>
          <dt>토큰 추정 방식</dt>
          <dd>{decision.tokens?.estimationMethod ?? "—"}</dd>
        </div>
        <div>
          <dt>AA 스냅샷</dt>
          <dd title={decision.snapshotHash ?? undefined}>
            {short(decision.snapshotId, 8)} · {short(decision.snapshotHash, 8)}
          </dd>
        </div>
        <div>
          <dt>모델 매핑 카탈로그</dt>
          <dd>
            {decision.catalogVersion ?? "—"}
            {snapshot === null || snapshot.catalogProvenance.length === 0
              ? ""
              : ` · ${snapshot.catalogProvenance.join(", ")}`}
          </dd>
        </div>
        <div>
          <dt>결제 조건 바인딩 해시</dt>
          <dd title={decision.termsBindingHash ?? undefined}>
            {short(decision.termsBindingHash, 8)}
          </dd>
        </div>
        <div>
          <dt>관측 실행시간(모의 실행)</dt>
          <dd>
            {delivery === null || delivery.observedExecutionMs === null
              ? "실행 기록 없음"
              : `${delivery.observedExecutionMs.toLocaleString("ko-KR")} ms · ${delivery.executionMode}`}
          </dd>
        </div>
      </dl>

      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              <th>모델 ID · 버전</th>
              <th>고정 가격</th>
              <th>예상 완료시간</th>
              <th>AA 성능 지수</th>
              <th>점수 합계</th>
              <th>판정</th>
            </tr>
          </thead>
          <tbody>
            {decision.rows.map((row) => {
              const badge = outcomeBadge[row.outcome];
              return (
                <tr key={row.key}>
                  <td>
                    <strong>
                      {row.providerId ?? "unknown"} / {row.providerModelId ?? "unknown"}
                    </strong>
                    <small>
                      version {row.modelVersion ?? "—"} · AA {row.aaModelId ?? "매핑 없음"} ·
                      매핑 {row.mappingProvenance ?? "—"}
                    </small>
                  </td>
                  <td>
                    {tokenAmount(row.amountUnits, decimals)} {symbol}
                    <small>
                      AA 단가 입력 {row.inputPricePerMillion ?? "—"} · 출력{" "}
                      {row.outputPricePerMillion ?? "—"} (USD/1M tokens)
                    </small>
                  </td>
                  <td title={row.estimatedCompletionMs ?? undefined}>
                    {shortDecimal(row.estimatedCompletionMs, 0)} ms
                    <small>
                      AA median {row.medianEndToEndSeconds ?? "—"}s 환산 · 벤치마크 참고 추정치
                    </small>
                  </td>
                  <td title={row.intelligenceIndex ?? undefined}>
                    {shortDecimal(row.intelligenceIndex)}
                    <small>AA Intelligence Index</small>
                  </td>
                  <td title={row.scores?.total ?? undefined}>
                    {row.scores === null ? "—" : shortDecimal(row.scores.total)}
                    <small>
                      가격 {shortDecimal(row.scores?.price ?? null)} · 시간{" "}
                      {shortDecimal(row.scores?.completionTime ?? null)} · 성능{" "}
                      {shortDecimal(row.scores?.intelligence ?? null)}
                    </small>
                  </td>
                  <td>
                    <span className={`badge ${badge.tone}`}>{badge.label}</span>
                    <small>
                      {row.outcome === "rejected"
                        ? describeRejection(row.rejectionReasons)
                        : row.rank === null
                          ? "순위 미기록"
                          : `순위 ${row.rank}`}
                    </small>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="finding normal">
        <strong>결정적 선택 설명</strong>
        <p>{decision.explanation ?? "저장된 설명이 없습니다."}</p>
        {decision.generatedExplanation === null ? null : (
          <small>보조 설명(정책을 바꾸지 못함): {decision.generatedExplanation}</small>
        )}
      </div>

      <p className="panelNote">
        예상 완료시간은 Artificial Analysis가 공개한 median end-to-end 값을 환산한 벤치마크 참고
        추정치이며 이 실행의 응답 지연 보장이 아닙니다. fixture 스냅샷의 가격·시간·성능 값은 저장된
        고정 표본이고 이번 실행에서 측정한 값이 아닙니다. 실행과 결제는 Mock Provider와 Mock
        Facilitator 응답을 기준으로 기록되며, 체인 독립 검증 결과가 아닙니다.
      </p>
    </section>
  );
}
