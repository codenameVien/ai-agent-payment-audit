import Link from "next/link";

import { LiveStatus } from "./live-status";

export function Nav() {
  return (
    <header className="topbar">
      <Link className="brand" href="/" aria-label="감사 대시보드 홈">
        <span className="brandMark">A</span>
        <span><strong>Agent Audit</strong><small>x402 exact · 모의 정산</small></span>
      </Link>
      <nav aria-label="주요 메뉴">
        <Link href="/">개요</Link>
        <Link href="/purchases">거래</Link>
        <Link href="/agents">과거 판매 에이전트</Link>
        <Link href="/alerts">감사 경고</Link>
      </nav>
      <LiveStatus />
    </header>
  );
}
