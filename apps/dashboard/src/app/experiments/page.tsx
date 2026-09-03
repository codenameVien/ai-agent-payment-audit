import { ExperimentRunner } from "@/components/experiment-runner";

export const metadata = { robots: { index: false, follow: false } };

export default function ExperimentsPage() {
  return (
    <main>
      <div className="pageHead">
        <p className="eyebrow">발표·개발용 실행 도구</p>
        <h1>거래를 만들고, 감사 화면에서 확인하세요.</h1>
        <p>
          사용자용 감사 대시보드와 분리된 운영 화면입니다. 이곳에서만 실제 거래를 시작하며,
          개요·거래·경고 화면은 계속 읽기 전용으로 유지됩니다.
        </p>
      </div>
      <ExperimentRunner />
    </main>
  );
}
