"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, credits, short } from "@/lib/api";
import type { AuditFinding, PurchaseSummary, WalletView } from "@/lib/types";
import {
  Empty,
  PaymentAmount,
  PurchaseStatusBadge,
  SeverityBadge,
} from "./status";

const priorityLabel: Record<string, string> = {
  balanced: "균형 우선",
  quality: "품질 우선",
  price: "가격 우선",
  speed: "속도 우선",
};

function requestLabel(item: PurchaseSummary): string {
  const priority = item.request_summary.priority;
  const capabilities = item.request_summary.required_capabilities;
  const suffix =
    typeof priority === "string"
      ? priorityLabel[priority] ?? priority
      : "정책 확인";
  if (Array.isArray(capabilities) && capabilities.length > 0) {
    return `${capabilities.slice(0, 2).join(" · ")} · ${suffix}`;
  }
  return `AI 모델 선택 · ${suffix}`;
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
          로그인이 필요합니다. <Link href="/login">MetaMask로 로그인</Link>
        </Empty>
      </main>
    );
  }

  const risk = alerts.filter((item) => item.severity === "RISK").length;
  const caution = alerts.filter((item) => item.severity === "CAUTION").length;
  const settled = purchases.filter((item) => item.transaction_hash !== null);
  const audited = purchases.filter((item) => item.audit_severity !== null);
  const totalPaid = settled.reduce(
    (sum, item) => sum + (item.amount_units ?? 0),
    0,
  );
  const chainReady = wallet?.balance_status === "base_sepolia_verified";

  return (
    <main className="auditMain">
      <section className="auditIntro">
        <div>
          <p className="eyebrow">AUDIT OVERVIEW</p>
          <h1>AI 에이전트 결제 감사</h1>
          <p>
            현재 로그인 계정의 판단 증거, PBLC 결제, 온체인 결과와 경고를
            읽기 전용으로 확인합니다.
          </p>
        </div>
        <div className="scopePill">
          <i className={chainReady ? "ok" : "warn"} />
          <span>
            <strong>{chainReady ? "BASE SEPOLIA LIVE" : "RPC 확인 필요"}</strong>
            <small>계정 범위 데이터 · 구매 실행 없음</small>
          </span>
        </div>
      </section>

      {error && (
        <div className="notice risk" role="alert">
          데이터가 최신이 아닐 수 있습니다: {error}{" "}
          <button onClick={() => void load()}>다시 시도</button>
        </div>
      )}

      <section className="auditMetrics" aria-label="감사 요약">
        <article>
          <span>구매 에이전트 PBLC</span>
          <strong>
            {wallet?.token_balance_units == null
              ? "—"
              : credits(wallet.token_balance_units)}
          </strong>
          <small>
            {chainReady ? "온체인 잔액" : "RPC 연결 후 확인"} · PBLC
          </small>
        </article>
        <article>
          <span>검증된 결제</span>
          <strong>{settled.length}</strong>
          <small>합계 {credits(totalPaid)} PBLC</small>
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
                이 로그인 계정에 연결된 거래가 아직 없습니다. 다른 사용자의
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
                      <th>온체인</th>
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
                            transactionHash={item.transaction_hash}
                            status={item.status}
                          />
                        </td>
                        <td>
                          <SeverityBadge value={item.audit_severity} />
                        </td>
                        <td>
                          {item.transaction_hash ? (
                            <a
                              className="chainLink"
                              href={`https://sepolia.basescan.org/tx/${item.transaction_hash}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {short(item.transaction_hash, 5)} ↗
                            </a>
                          ) : (
                            <span className="muted">거래 해시 없음</span>
                          )}
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
              <Link href="/login">계정 변경</Link>
            </div>
            <dl className="walletList">
              <div>
                <dt>로그인 사용자</dt>
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
                <dt>PBLC 토큰</dt>
                <dd title={wallet?.token ?? undefined}>
                  {short(wallet?.token, 9)}
                </dd>
              </div>
              <div>
                <dt>일일 사용 / 한도</dt>
                <dd>
                  {credits(wallet?.spent_units ?? 0)} /{" "}
                  {credits(wallet?.daily_limit_units ?? null)} PBLC
                </dd>
              </div>
            </dl>
            <p className="panelNote">
              MetaMask는 로그인 소유자를 확인하고, 구매 에이전트 지갑은 승인된
              정책 안에서만 x402 결제를 수행합니다.
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

          <article className="panel evidencePanel">
            <p className="eyebrow">EVIDENCE STATUS</p>
            <h2>실제 증거 연결 상태</h2>
            <ul className="evidenceChecks">
              <li>
                <i className={chainReady ? "ok" : "warn"} />
                <span>
                  <strong>PBLC 잔액 RPC</strong>
                  <small>
                    {chainReady
                      ? "온체인 조회 완료"
                      : wallet?.balance_status ?? "조회 중"}
                  </small>
                </span>
              </li>
              <li>
                <i className={settled.length > 0 ? "ok" : "idle"} />
                <span>
                  <strong>Base Sepolia 결제 해시</strong>
                  <small>{settled.length}건 연결됨</small>
                </span>
              </li>
              <li>
                <i className={audited.length > 0 ? "ok" : "idle"} />
                <span>
                  <strong>MongoDB 감사 증거</strong>
                  <small>{audited.length}건 감사 완료</small>
                </span>
              </li>
            </ul>
            <p className="panelNote">
              세부 이벤트 해시 체인과 감사 bundle은 각 거래 상세에서 확인합니다.
            </p>
          </article>

          <article className="panel readOnlyPanel">
            <span className="readOnlyIcon">⌁</span>
            <div>
              <strong>이 화면은 모니터 전용입니다</strong>
              <p>
                정상·비정상 거래 생성 도구는 감사 기록과 분리된 시연 화면으로
                추가할 예정입니다.
              </p>
            </div>
          </article>
        </aside>
      </section>
    </main>
  );
}
