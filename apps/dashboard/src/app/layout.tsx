import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = { title: "Agent Audit", description: "AI agent M2M payment audit dashboard" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="ko"><body><AppShell>{children}</AppShell></body></html>; }
