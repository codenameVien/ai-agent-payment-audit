import { credits, paymentTokenSymbol } from "@/lib/api";

/**
 * Purchases use a Facilitator response as their settlement basis. A live settlement
 * may carry the transaction reference the Facilitator submitted; that is intentionally
 * distinct from application-side receipt verification.
 */

const purchaseStatusLabels: Record<string, string> = {
  REQUESTED: "요청 접수",
  DECIDED: "선택 완료",
  PAYMENT_INTENT_CLAIMED: "결제 준비",
  PAYMENT_AUTHORIZED: "결제 승인됨",
  PAYMENT_SETTLED: "결제 완료",
  PAYMENT_RECONCILIATION_REQUIRED: "결제 확인 필요",
  PAYMENT_FAILED: "결제 실패",
  DELIVERED: "응답 완료",
  AUDITED: "처리 완료",
};

function purchaseStatusTone(value: string): string {
  if (value === "PAYMENT_RECONCILIATION_REQUIRED") return "caution";
  if (value === "PAYMENT_FAILED") return "risk";
  if (["PAYMENT_SETTLED", "DELIVERED", "AUDITED"].includes(value)) {
    return "normal";
  }
  return "pending";
}

export function PurchaseStatusBadge({ value }: { value: string }) {
  return (
    <span className={`badge ${purchaseStatusTone(value)}`}>
      {purchaseStatusLabels[value] ?? value.replaceAll("_", " ")}
    </span>
  );
}

export function SeverityBadge({ value }: { value: string | null }) {
  const normalized = (value ?? "PENDING").toLowerCase();
  const label =
    value === "NORMAL"
      ? "정상"
      : value === "CAUTION"
        ? "주의"
        : value === "RISK"
          ? "위험"
          : "감사 미실행";
  return <span className={`badge ${normalized}`}>{label}</span>;
}

export function PaymentAmount({
  amountUnits,
  status,
  paymentStatus,
  executionMode,
  tokenAddress,
}: {
  amountUnits: number | null;
  status: string;
  paymentStatus?: string;
  executionMode?: string | null;
  tokenAddress?: string | null;
}) {
  if (amountUnits === null) return <span className="muted">—</span>;
  const settled = (paymentStatus ?? status) === "PAYMENT_SETTLED";
  const live = executionMode === "live";
  const mock = executionMode === "mock";
  return (
    <span className={`paymentAmount ${settled ? "confirmed" : "unconfirmed"}`}>
      <strong>
        {settled ? "" : "예정 "}
        {credits(amountUnits)} {paymentTokenSymbol(tokenAddress)}
      </strong>
      <small>
        {settled
          ? live
            ? "Facilitator 응답 기준 정산 · 실제 토큰 전송 제출"
            : mock
              ? "Facilitator 응답 기준 정산 · Mock 결제"
              : "Facilitator 응답 기준 정산"
          : live
            ? "정산 미확정 · 실제 결제 모드"
            : mock
              ? "정산 미확정 · Mock 결제"
              : "정산 미확정"}
      </small>
    </span>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
