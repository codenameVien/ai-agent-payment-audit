"use client";

import { useEffect, useState } from "react";

import { AiInferencePurchaseEvidence } from "@/features/ai-inference/purchase-evidence";
import { api, short } from "@/lib/api";
import type { PurchaseDetail as Detail } from "@/lib/types";
import {
  Empty,
  PaymentAmount,
  PurchaseStatusBadge,
  SeverityBadge,
} from "./status";

export function PurchaseDetail({ purchaseId }: { purchaseId: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Detail>(`/purchases/${encodeURIComponent(purchaseId)}`)
      .then(setDetail)
      .catch((reason: Error) => setError(reason.message));
  }, [purchaseId]);

  if (error) return <main><div className="notice risk">{error}</div></main>;
  if (!detail) return <main><Empty>증거를 불러오는 중입니다…</Empty></main>;

  const paymentNeedsReview =
    detail.summary.status === "PAYMENT_RECONCILIATION_REQUIRED";

  return (
    <main>
      <div className="pageHead">
        <p className="eyebrow">거래 상세</p>
        <h1>{short(purchaseId, 14)}</h1>
        <div className="detailMeta">
          <PurchaseStatusBadge value={detail.summary.status} />
          <SeverityBadge value={detail.summary.audit_severity} />
          <PaymentAmount
            amountUnits={detail.summary.amount_units}
            transactionHash={detail.summary.transaction_hash}
            status={detail.summary.status}
          />
          {detail.summary.transaction_hash && (
            <a
              href={`https://sepolia.basescan.org/tx/${detail.summary.transaction_hash}`}
              target="_blank"
              rel="noreferrer"
            >
              BaseScan 거래 ↗
            </a>
          )}
        </div>
      </div>
      <section className="twoCol">
        <article className="panel">
          <p className="eyebrow">사용자 요청</p>
          <pre className="evidenceJson">
            {JSON.stringify(detail.summary.request_summary, null, 2)}
          </pre>
        </article>
        <article className="panel">
          <p className="eyebrow">감사 결과</p>
          {detail.audit ? (
            <>
              <h2>
                <SeverityBadge value={detail.audit.severity} />{" "}
                {detail.audit.findings.length
                  ? `${detail.audit.findings.length}개 발견`
                  : "위험 없음"}
              </h2>
              <p className="hash">Bundle {short(detail.audit.audit_bundle_hash, 12)}</p>
              {detail.audit.findings.map((item) => (
                <div className={`finding ${item.severity.toLowerCase()}`} key={item.code}>
                  <strong>{item.title}</strong>
                  <p>{item.detail}</p>
                  <small>{item.code} · {item.authority}</small>
                </div>
              ))}
            </>
          ) : (
            <Empty>
              {paymentNeedsReview
                ? "결제 결과가 확정되지 않아 감사를 실행하지 않았습니다."
                : "아직 감사 보고서가 없습니다."}
            </Empty>
          )}
        </article>
      </section>
      {detail.summary.domain === "ai_inference" ? (
        <AiInferencePurchaseEvidence events={detail.events} />
      ) : null}
      <section className="panel">
        <div className="sectionHead">
          <div>
            <p className="eyebrow">증거 타임라인</p>
            <h2>Append-only lifecycle</h2>
          </div>
        </div>
        <ol className="timeline">
          {detail.events.map((event) => (
            <li key={event.event_id}>
              <i />
              <div>
                <div className="timelineHead">
                  <strong>{event.type}</strong>
                  <time>{new Date(event.occurred_at).toLocaleString("ko-KR")}</time>
                </div>
                <p className="hash">
                  #{event.sequence} · {short(event.event_hash, 13)}{" "}
                  {event.redacted && "· 민감 필드 가림"}
                </p>
                <details>
                  <summary>구조화 증거 보기</summary>
                  <pre className="evidenceJson">{JSON.stringify(event.payload, null, 2)}</pre>
                </details>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
