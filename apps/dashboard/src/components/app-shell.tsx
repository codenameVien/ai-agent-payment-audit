"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Nav } from "./Nav";

export function AppShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();

  if (pathname.startsWith("/request")) {
    return (
      <>
        <header className="operatorBar">
          <div>
            <span>Purchase surface</span>
            <strong>Buyer Agent Request</strong>
          </div>
          <Link href="/">감사 대시보드로 돌아가기 →</Link>
        </header>
        {children}
        <footer>
          구매 요청 전용 화면 · 감사 대시보드와 실행 책임을 분리했습니다 · Mock Provider ·
          결제 모드는 요청 화면과 거래 증거에서 확인
        </footer>
      </>
    );
  }

  return (
    <>
      <Nav />
      {children}
      <footer>
        Agent Audit · 로컬 단일 사용자 데모 · Provider 응답은 Mock이며 결제 모드는 거래 증거에 기록됩니다 ·
        민감 원문과 개인키는 표시하지 않습니다.
      </footer>
    </>
  );
}
