"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, short } from "@/lib/api";
import type { PurchaseSummary } from "@/lib/types";
import {
  Empty,
  PaymentAmount,
  PurchaseStatusBadge,
  SeverityBadge,
} from "./status";

export function PurchaseList() {
  const [items, setItems] = useState<PurchaseSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api<PurchaseSummary[]>("/purchases")
      .then(setItems)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  return (
    <main>
      <div className="pageHead">
        <p className="eyebrow">거래 기록</p>
        <h1>구매 판단과 결제 목록</h1>
        <p>각 거래의 선택 근거와 온체인 결과를 함께 확인합니다.</p>
      </div>
      {error && <div className="notice risk">{error}</div>}
      <section className="panel">
        {items.length === 0 ? (
          <Empty>표시할 거래가 없습니다.</Empty>
        ) : (
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Purchase ID</th>
                  <th>도메인</th>
                  <th>처리 상태</th>
                  <th>결제 금액</th>
                  <th>감사 결과</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.purchase_id}>
                    <td>
                      <Link href={`/purchases/${item.purchase_id}`}>
                        {short(item.purchase_id, 12)}
                      </Link>
                      <small>{new Date(item.created_at).toLocaleString("ko-KR")}</small>
                    </td>
                    <td>{item.domain}</td>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
