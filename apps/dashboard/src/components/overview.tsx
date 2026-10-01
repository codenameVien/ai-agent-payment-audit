"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, credits, short } from "@/lib/api";
import { AEGIS_PRIORITY_LABELS } from "@/lib/aegis";
import type { AuditFinding, PurchaseSummary, WalletView } from "@/lib/types";
import {
  Empty,
  PaymentAmount,
  PurchaseStatusBadge,
  SeverityBadge,
} from "./status";

const balanceStatusLabel: Record<string, string> = {
  base_sepolia_verified: "온체인 잔액 조회 완료",
  rpc_not_configured: "잔액 조회 안 함 · RPC 미구성",
  rpc_unavailable: "잔액 조회 실패 · RPC 응답 없음",
};

function requestLabel(item: PurchaseSummary): string {
  const effective = item.request_summary.effective_priority;
  const original = item.request_summary.original_priority;
  const label =
    typeof effective === "string"
      ? AEGIS_PRIORITY_LABELS[effective] ?? effective
      : "우선순위 미기록";
  return `AI 모델 선택 · ${label}${original === null ? " (자동 분류)" : ""}`;
}

export function Overview() {
  const [wallet, setWallet] = useState<WalletView | null>(null);
  const [purchases, setPurchases] = useState<PurchaseSummary[]>([]);
  const [alerts, setAlerts] = useState<AuditFinding[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextWallet, nextPurchases, nextAlerts] = await Promise.all([
        api<WalletView>("/wallet"),
        api<PurchaseSummary[]>("/purchases"),
        api<AuditFinding[]>("/audit-alerts"),
      ]);
      setWallet(nextWallet);
      setPurchases(nextPurchases);
      setAlerts(nextAlerts);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "조회 실패");
    }
  }, []);

  useEffect(() => {
    void load();
    const listener = () => void load();
    window.addEventListener("pbl:evidence", listener);
    return () => window.removeEventListener("pbl:evidence", listener);
  }, [load]);

  if (error?.startsWith("401")) {
    return (
      <main>
        <Empty>
          로컬 감사 API에 접근하지 못했습니다(401). API 서버 실행과 로컬 소유자 설정을
          확인해 주세요. 이 화면은 공개 다중 사용자 인증을 구현하지 않습니다.
        </Empty>
      </main>
    );
  }

  const risk = alerts.filter((item) => item.severity === "RISK").length;
  const caution = alerts.filter((item) => item.severity === "CAUTION").length;
  const settled = purchases.filter((item) => item.payment_status === "PAYMENT_SETTLED");
  const audited = purchases.filter((item) => item.audit_severity !== null);
  const balanceLabel =
    wallet === null
      ? "조회 중"
      : balanceStatusLabel[wallet.balance_status] ?? wallet.balance_status;

  return (
    <main className="auditMain">
      {error && (
        <div className="notice risk" role="alert">
          데이터가 최신이 아닐 수 있습니다: {error}{" "}
          <button onClick={() => void load()}>다시 시도</button>
        </div>
      )}

      <section className="auditMetrics" aria-label="감사 요약">
        <article>
          <span>구매 에이전트 토큰 잔액</span>
          <strong>
            {wallet?.token_balance_units == null
              ? "—"
              : credits(wallet.token_balance_units)}
          </strong>
          <small>{balanceLabel}</small>
        </article>
        <article>
          <span>정산 완료 거래</span>
          <strong>{settled.length}</strong>
          <small>Facilitator 응답 기준 정산</small>
        </article>
        <article>
          <span>감사 완료</span>
          <strong>{audited.length}</strong>
          <small>계정 거래 {purchases.length}건 중</small>
        </article>
        <article>
          <span>열린 경고</span>
          <strong className={risk > 0 ? "riskText" : ""}>
            {risk} 위험 <b>·</b> {caution} 주의
          </strong>
          <small>
            <Link href="/alerts">경고 전체 보기 →</Link>
          </small>
        </article>
      </section>

      <section className="auditLayout">
        <div className="auditPrimary">
          <article className="panel transactionPanel">
            <div className="sectionHead">
              <div>
                <p className="eyebrow">RECENT TRANSACTIONS</p>
                <h2>최근 판단·결제 기록</h2>
              </div>
              <Link href="/purchases">전체 기록 →</Link>
            </div>
            {purchases.length === 0 ? (
              <Empty>
                이 소유자에 연결된 거래가 아직 없습니다. 다른 사용자의
                테스트 거래는 섞어서 표시하지 않습니다.
              </Empty>
            ) : (
              <div className="tableWrap">
                <table>
                  <thead>
                    <tr>
                      <th>요청·Purchase ID</th>
                      <th>처리 상태</th>
                      <th>결제 금액</th>
                      <th>감사 결과</th>
                      <th>정산 기준</th>
                    </tr>
                  </thead>
                  <tbody>
                    {purchases.slice(0, 6).map((item) => (
                      <tr key={item.purchase_id}>
                        <td>
                          <Link href={`/purchases/${item.purchase_id}`}>
                            {requestLabel(item)}
                          </Link>
                          <small>
                            {short(item.purchase_id, 8)} ·{" "}
                            {new Date(item.created_at).toLocaleString("ko-KR")}
                          </small>
                        </td>
                        <td>
                          <PurchaseStatusBadge value={item.status} />
                        </td>
                        <td>
                          <PaymentAmount
                            amountUnits={item.amount_units}
                            status={item.status}
                            paymentStatus={item.payment_status}
                            tokenAddress={item.token}
                          />
                        </td>
                        <td>
                          <SeverityBadge value={item.audit_severity} />
                        </td>
                        <td>
                          <span className="muted">Facilitator 응답 기준</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </article>

          <article className="panel accountPanel">
            <div className="sectionHead">
              <div>
                <p className="eyebrow">ACCOUNT SCOPE</p>
                <h2>감사 대상 지갑</h2>
              </div>
            </div>
            <dl className="walletList">
              <div>
                <dt>로컬 소유자</dt>
                <dd title={wallet?.owner_address ?? undefined}>
                  {short(wallet?.owner_address, 9)}
                </dd>
              </div>
              <div>
                <dt>구매 에이전트</dt>
                <dd title={wallet?.buyer_wallet_address ?? undefined}>
                  {short(wallet?.buyer_wallet_address, 9)}
                </dd>
              </div>
              <div>
                <dt>결제 토큰</dt>
                <dd title={wallet?.token ?? undefined}>
                  {short(wallet?.token, 9)}
                </dd>
              </div>
              <div>
                <dt>일일 사용 / 한도</dt>
                <dd>
                  {credits(wallet?.spent_units ?? 0)} /{" "}
                  {credits(wallet?.daily_limit_units ?? null)}
                </dd>
              </div>
            </dl>
            <p className="panelNote">
              로컬 단일 사용자 데모입니다. 공개 다중 사용자 인증은 구현하지 않았고, 서명 키는
              결제 실행 모듈에만 격리되어 브라우저로 오지 않습니다. 구매 에이전트는 승인된 한도
              안에서만 x402 결제를 요청합니다.
            </p>
          </article>
        </div>

        <aside className="auditSidebar">
          <article className="panel">
            <div className="sectionHead">
              <div>
                <p className="eyebrow">AUDIT ALERTS</p>
                <h2>확인이 필요한 항목</h2>
              </div>
              <Link href="/alerts">전체 →</Link>
            </div>
            {alerts.length === 0 ? (
              <div className="clearState">
                <span>✓</span>
                <div>
                  <strong>열린 경고 없음</strong>
                  <small>현재 계정의 감사 결과 기준</small>
                </div>
              </div>
            ) : (
              <div className="compactAlerts">
                {alerts.slice(0, 4).map((item, index) => (
                  <div key={`${item.code}-${index}`}>
                    <SeverityBadge value={item.severity} />
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.code}</small>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </article>

        </aside>
      </section>
    </main>
  );
}
