"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, short } from "@/lib/api";
import type { AuditFinding } from "@/lib/types";
import { Empty, SeverityBadge } from "./status";
type Alert = AuditFinding & { purchase_id: string; report_id: string };
export function Alerts() { const [items, setItems] = useState<Alert[]>([]); useEffect(() => { void api<Alert[]>("/audit-alerts").then(setItems); }, []); return <main><div className="pageHead"><p className="eyebrow">감사 경고</p><h1>확인이 필요한 위험 요소</h1><p>결정적 규칙이 권위 판정을 소유하며 의미 분석은 추가 경고만 만듭니다.</p></div><section className="alertList">{items.length === 0 ? <Empty>현재 열린 주의·위험 경고가 없습니다.</Empty> : items.map((item) => <article className={`panel alert ${item.severity.toLowerCase()}`} key={`${item.report_id}-${item.code}`}><SeverityBadge value={item.severity} /><div><h2>{item.title}</h2><p>{item.detail}</p><small>{item.code} · {item.authority} · 증거 {item.evidence_refs.map((ref) => short(ref, 5)).join(", ")}</small></div><Link href={`/purchases/${item.purchase_id}`}>거래 보기 →</Link></article>)}</section></main>; }
