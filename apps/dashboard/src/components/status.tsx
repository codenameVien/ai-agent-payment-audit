import { credits } from "@/lib/api";

/**
 * `aegis` purchases settle against a Mock Facilitator answer and never carry an EVM
 * transaction hash, so they must not reuse the historical on-chain wording.
 */
export type PurchasePolicy = "legacy" | "aegis";

const purchaseStatusLabels: Record<string, string> = {
  REQUESTED: "요청 접수",
  QUOTED: "견적 수집",
  DECIDED: "선택 완료",
  PAYMENT_INTENT_CLAIMED: "결제 준비",
  PAYMENT_AUTHORIZED: "결제 승인됨",
  PAYMENT_SUBMISSION_IDENTIFIED: "거래 확인 중",
  PAYMENT_SETTLED: "결제 완료",
  PAYMENT_RECONCILIATION_REQUIRED: "결제 확인 필요",
  PAYMENT_FAILED: "결제 실패",
  DELIVERY_STAGED: "응답 전달 준비",
  DELIVERED: "응답 완료",
  AUDITED: "처리 완료",
  REPUTATION_RECORDED: "평판 기록 완료",
};

function purchaseStatusTone(value: string): string {
  if (value === "PAYMENT_RECONCILIATION_REQUIRED") return "caution";
  if (value === "PAYMENT_FAILED") return "risk";
  if (["PAYMENT_SETTLED", "DELIVERED", "AUDITED", "REPUTATION_RECORDED"].includes(value)) {
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
  transactionHash,
  status,
  policy = "legacy",
  paymentStatus,
}: {
  amountUnits: number | null;
  transactionHash: string | null;
  status: string;
  policy?: PurchasePolicy;
  paymentStatus?: string;
}) {
  if (amountUnits === null) return <span className="muted">—</span>;
  if (policy === "aegis") {
    const settled = (paymentStatus ?? status) === "PAYMENT_SETTLED";
    return (
      <span className={`paymentAmount ${settled ? "confirmed" : "unconfirmed"}`}>
        <strong>
          {settled ? "" : "예정 "}
          {credits(amountUnits)} AEGIS
        </strong>
        <small>
          {settled
            ? "Facilitator 응답 기준 정산 · 모의 결제"
            : "정산 미확정 · 모의 결제"}
        </small>
      </span>
    );
  }
  const confirmed =
    transactionHash !== null &&
    ["PAYMENT_SETTLED", "DELIVERY_STAGED", "DELIVERED", "AUDITED", "REPUTATION_RECORDED"].includes(
      status,
    );
  return (
    <span className={`paymentAmount ${confirmed ? "confirmed" : "unconfirmed"}`}>
      <strong>
        {confirmed ? "" : "예정 "}
        {credits(amountUnits)} PBLC
      </strong>
      <small>
        {confirmed
          ? "온체인 결제 확인"
          : transactionHash
            ? "거래 해시 존재 · 결제 미확정"
            : "결제 미확인"}
      </small>
    </span>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
