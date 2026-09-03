import { PurchaseRequest } from "@/components/purchase-request";

export const metadata = { robots: { index: false, follow: false } };

export default function RequestPage() {
  return (
    <main>
      <div className="pageHead">
        <p className="eyebrow">구매 요청</p>
        <h1>요청은 여기서, 감사 결과는 대시보드에서 확인하세요.</h1>
        <p>구매 에이전트가 요구·예산·우선순위에 따라 판매 에이전트를 비교하고 결제를 실행합니다.</p>
      </div>
      <PurchaseRequest />
    </main>
  );
}
