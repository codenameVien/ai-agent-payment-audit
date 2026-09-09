export interface MeResponse {
  owner_address: string;
  buyer_wallet_address: string | null;
}

export interface WalletView {
  owner_address: string;
  buyer_wallet_address: string | null;
  token: string | null;
  token_balance_units: number | null;
  balance_status: string;
  policy_date: string | null;
  daily_limit_units: number | null;
  spent_units: number;
  reserved_units: number;
}

export interface PurchaseSummary {
  purchase_id: string;
  created_at: string;
  domain: string;
  request_summary: Record<string, unknown>;
  status: string;
  amount_units: number | null;
  token: string | null;
  transaction_hash: string | null;
  audit_severity: "NORMAL" | "CAUTION" | "RISK" | null;
  finding_count: number;
  lifecycle_status: string;
  payment_status: string;
  audit_status: string;
  audit_covers_head: boolean;
}

export interface EvidenceEvent {
  event_id: string;
  sequence: number;
  type: string;
  occurred_at: string;
  actor: Record<string, unknown>;
  payload: Record<string, unknown>;
  event_hash: string;
  evidence_refs: string[];
  redacted: boolean;
}

export interface AuditFinding {
  code: string;
  severity: "NORMAL" | "CAUTION" | "RISK";
  title: string;
  detail: string;
  evidence_refs: string[];
  authority: string;
}

export interface AuditReport {
  report_id: string;
  purchase_id: string;
  severity: "NORMAL" | "CAUTION" | "RISK";
  findings: AuditFinding[];
  evidence_head_event_hash: string;
  audit_bundle_hash: string;
}

export interface PurchaseDetail {
  summary: PurchaseSummary;
  events: EvidenceEvent[];
  audit: AuditReport | null;
}

export interface AgentSummary {
  seller_agent_id: string;
  erc8004_agent_id: string | null;
  signer_address: string | null;
  identity_verified: boolean;
  quote_count: number;
  reputation_status: string;
  objective_feedback_value: number | null;
  reputation_transaction_hash: string | null;
}
