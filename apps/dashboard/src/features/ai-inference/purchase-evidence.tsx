import { credits, short } from "@/lib/api";
import type { EvidenceEvent } from "@/lib/types";

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          typeof item === "object" && item !== null,
      )
    : [];
}

export function AiInferencePurchaseEvidence({ events }: { events: EvidenceEvent[] }) {
  const quoted = events.find((event) => event.type === "QUOTED");
  const decided = events.find((event) => event.type === "DECIDED");
  if (!quoted || !decided) return null;
  const quotes = records(quoted.payload.signedQuotes);
  const winner =
    typeof decided.payload.winner === "object" && decided.payload.winner !== null
      ? (decided.payload.winner as Record<string, unknown>)
      : {};
  const winnerId = String(winner.quote_id ?? "");
  const rejected = new Map(
    records(decided.payload.rejected).map((item) => [
      String(item.quote_id ?? ""),
      Array.isArray(item.reasons) ? item.reasons.join(", ") : "",
    ]),
  );
  return (
    <section className="panel">
      <div className="sectionHead">
        <div>
          <p className="eyebrow">AI 선택 증거</p>
          <h2>후보 비교와 선택 근거</h2>
        </div>
      </div>
      <div className="tableWrap">
        <table>
          <thead><tr><th>판매 에이전트 / 모델</th><th>가격</th><th>예상 지연</th><th>판정</th></tr></thead>
          <tbody>
            {quotes.map((quote) => {
              const quoteId = String(quote.quote_id ?? "");
              const selected = quoteId === winnerId;
              return (
                <tr key={quoteId}>
                  <td><strong>{String(quote.seller_agent_id ?? "unknown")}</strong><small>{String(quote.provider_id ?? "")} · {String(quote.model_id ?? "")}</small></td>
                  <td>{credits(Number(quote.amount_units ?? 0))} PBLC</td>
                  <td>{Number(quote.expected_latency_ms ?? 0).toLocaleString("ko-KR")} ms</td>
                  <td>{selected ? <span className="badge normal">선택</span> : <span className="badge caution">제외</span>}<small>{selected ? `quote ${short(quoteId, 7)}` : rejected.get(quoteId) || "점수 비교에서 제외"}</small></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="finding normal">
        <strong>결정적 선택 설명</strong>
        <p>{String(decided.payload.explanation ?? "저장된 설명이 없습니다.")}</p>
        {decided.payload.generatedExplanation ? <small>보조 설명: {String(decided.payload.generatedExplanation)}</small> : null}
      </div>
    </section>
  );
}
