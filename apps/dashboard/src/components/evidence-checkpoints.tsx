import type { EvidenceEvent } from "@/lib/types";

function label(phase: string) { return phase === "decision" ? "구매 결정 · 결제 전" : "실행 결과 · 규칙 감사 후"; }
function object(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function EvidenceCheckpoints({ events }: { events: EvidenceEvent[] }) {
  const active = events.some(event => event.type.startsWith("OBSERVER_") || event.type.startsWith("CHECKPOINT_"));
  if (!active) return null;
  return <section className="panel">
    <p className="eyebrow">판단 기록과 감사 증거</p>
    <h2>Qwen 관찰 · 블록체인 체크포인트</h2>
    <p className="panelNote">LLM 검토는 의심 사유를 제안하는 자문입니다. 고정 규칙 감사·결제 권한을 대신하지 않습니다.</p>
    <div className="twoCol">
      {["decision", "audit"].map(phase => {
        const observation = events.find(event => event.type.startsWith("OBSERVER_") && event.payload.phase === phase);
        const checkpoint = events.find(event => event.type === "CHECKPOINT_RECORDED" && event.payload.phase === phase);
        const findings = Array.isArray(observation?.payload.findings) ? observation.payload.findings : [];
        const tx = checkpoint?.payload.transactionHash;
        const live = checkpoint?.payload.mode === "live" && typeof tx === "string" && /^0x[0-9a-fA-F]{64}$/.test(tx);
        return <article key={phase}>
          <h3>{label(phase)}</h3>
          <p>{!observation ? "관찰 기록 없음" : observation.type === "OBSERVER_UNAVAILABLE" ? "관찰 미완료 · 오류 또는 시간 초과" :
            observation.payload.mode === "mock" ? "Mock 관찰 완료 · 실제 Qwen 검사 아님" :
              observation.payload.mode === "off" ? "LLM 관찰 비활성" : "로컬 Qwen 관찰 완료"}</p>
          {typeof observation?.payload.model === "string" && <p className="panelNote">모델: {observation.payload.model}</p>}
          {findings.map((finding, i) => {
            const f = object(finding);
            return <div className="finding" key={i}>
              <strong>{String(f.severity ?? "검토")} · {String(f.code ?? "관찰")}</strong>
              <p>{String(f.detail ?? "")}</p>
              <small>대상 이벤트: {Array.isArray(f.eventIds) ? f.eventIds.join(", ") : "기록 없음"}</small>
            </div>;
          })}
          {observation?.type === "OBSERVER_COMPLETED" && findings.length === 0 && <p className="panelNote">추가 의심 사항 보고 없음. 정상 행위임을 보증하는 표시는 아닙니다.</p>}
          <p>{live ? "온체인 체크포인트 확인 기록" : checkpoint?.payload.mode === "mock" ? "Mock 체크포인트 · 블록체인 미기록" : "온체인 체크포인트 미완료/비활성"}</p>
          {checkpoint && <p className="hash">대상 #{String(checkpoint.payload.eventCount)} · {String(checkpoint.payload.headEventHash)}</p>}
          {live && <a href={`https://sepolia.basescan.org/tx/${tx}`} target="_blank" rel="noreferrer">Anchor 거래 보기 ↗</a>}
        </article>;
      })}
    </div>
    <p className="panelNote">원문은 MongoDB에, 해시는 체인에 기록합니다. 해시는 등록 후 변경·누락의 대조 기준이며 최초 기록의 진실성이나 삭제된 원문의 복구를 보장하지 않습니다.</p>
  </section>;
}
