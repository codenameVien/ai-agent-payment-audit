import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = { title: "Agent Audit", description: "AI agent M2M payment audit dashboard" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="ko"><body><Nav />{children}<footer>Agent Audit · Base Sepolia test environment · 민감 원문과 개인키는 표시하지 않습니다.</footer></body></html>; }
