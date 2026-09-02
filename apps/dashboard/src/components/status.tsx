export function SeverityBadge({ value }: { value: string | null }) {
  const normalized = (value ?? "PENDING").toLowerCase();
  const label = value === "NORMAL" ? "정상" : value === "CAUTION" ? "주의" : value === "RISK" ? "위험" : "진행 중";
  return <span className={`badge ${normalized}`}>{label}</span>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
