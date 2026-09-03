"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Nav } from "./Nav";

export function AppShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();

  if (pathname.startsWith("/experiments")) {
    return (
      <>
        <header className="operatorBar">
          <div>
            <span>Operator tool</span>
            <strong>Payment Experiment Runner</strong>
          </div>
          <Link href="/">감사 대시보드로 돌아가기 →</Link>
        </header>
        {children}
        <footer>발표·개발용 로컬 실행 도구 · 사용자 감사 대시보드 메뉴에는 노출되지 않습니다.</footer>
      </>
    );
  }

  return (
    <>
      <Nav />
      {children}
      <footer>Agent Audit · Base Sepolia test environment · 민감 원문과 개인키는 표시하지 않습니다.</footer>
    </>
  );
}
