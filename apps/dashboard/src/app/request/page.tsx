import { PurchaseRequest } from "@/components/purchase-request";

export const metadata = { robots: { index: false, follow: false } };

export default function RequestPage() {
  return (
    <main>
      <div className="pageHead">
        <p className="eyebrow">구매 요청</p>
        <h1>요청은 여기서, 감사 결과는 대시보드에서 확인하세요.</h1>
        <p>
          구매 에이전트가 요구·예산·우선순위에 따라 Artificial Analysis 스냅샷의 모델을
          비교하고 고정 선결제를 요청합니다. 제공자 실행과 정산은 모의 구성입니다.
        </p>
      </div>
      <PurchaseRequest />
    </main>
  );
}
