import { ExperimentRunner } from "@/components/experiment-runner";

export default function ExperimentsPage() {
  return (
    <main>
      <div className="pageHead">
        <p className="eyebrow">분리된 실행 환경</p>
        <h1>거래를 만들고, 감사 화면에서 확인하세요.</h1>
        <p>
          이 페이지에서만 실제 거래를 시작합니다. 개요·거래·경고 화면은 계속 읽기 전용으로
          유지됩니다.
        </p>
      </div>
      <ExperimentRunner />
    </main>
  );
}
