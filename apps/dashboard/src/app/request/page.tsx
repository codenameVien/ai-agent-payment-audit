import { PurchaseRequest } from "@/components/purchase-request";

export const metadata = { robots: { index: false, follow: false } };

export default function RequestPage() {
  return <main className="requestMain"><PurchaseRequest /></main>;
}
