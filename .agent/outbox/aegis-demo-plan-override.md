# 발표용 로컬 데모 — 최소 추가 override

확인: AGENTS.md의 assurance 설명과 tasks.md의 Coder Opus·전체 suite·광범위 완료 조건이 남아 있다. 기존 이력은 그대로 두고 아래 블록을 각 문서 제목 바로 뒤에 추가하면 된다. 새 packet·review·승인 단계는 없다.

## AGENTS.md 추가 문구

```markdown
## Current user override — 2026-09-10

The current goal is the presentation local demo defined in `.agent/DEMO_SCOPE.md`; it supersedes conflicting completion/assurance gates below. Coder is Terra (`gpt-5.6-terra`); Planner/Reviewer remain Astra Light. Finish only three Mock Provider request→AA selection→Mock x402 payment→result→audit E2Es, over-budget rejection, 402-condition mismatch rejection, duplicate-success prevention per purchaseId, dashboard build/basic lint, and minimal documentation. Preserve existing implementation, uncommitted changes and historical evidence.

Defer full Python outbound instrumentation, broad historical zero-write regression, Mongo failure-cleanup stress tests, broad-suite repeats and additional hardening. These are reported limitations, not demo completion blockers; do not claim they passed. Use owned disposable storage only. Do not invoke real AA/Provider APIs, deploy tokens, move assets, make real testnet payments or deploy AWS. Prior broader task checklists remain history, not mandatory work for this reduced goal.
```

## aidlc-docs/inception/tasks.md 추가 문구

```markdown
## 현재 실행 계약 우선 — 2026-09-10

사용자가 목표를 발표용 로컬 데모로 축소했다. `.agent/DEMO_SCOPE.md`가 아래 기존 완료 조건·assurance·Opus 배정보다 우선한다. Coder는 Terra(`gpt-5.6-terra`), Planner/Reviewer는 Astra Light다. 이전 구현/검증 이력과 미커밋 변경은 보존하며 새 packet이나 추가 리뷰 단계를 만들지 않는다.

남은 완료 기준은 세 Mock Provider 각각의 요청→AA 선택→Mock x402 결제→결과→감사 E2E, 예산 초과 결제 전 중단, 402 조건 불일치 중단, purchaseId별 중복 성공 결제 방지, dashboard build·기본 lint, README 양 언어·구조도·project log·handoff 최소 갱신과 실제/모의/미검증 구분 보고다. 필요한 기존 focused tests만 실행한다.

전체 Python outbound 계측, 광범위 역사 zero-write 회귀, Mongo 실패 cleanup 스트레스, broad suite 반복, 추가 hardening은 유예한다. 아래 미완료 체크박스를 억지로 완료 표시하지 않으며 이번 데모의 차단 조건으로 재활성화하지 않는다. 기존 데이터는 보존하고 소유한 임시 저장소만 사용한다. 실제 AA/Provider 호출·토큰 배포·자산 이동·실제 테스트넷 결제·AWS 배포는 수행하지 않는다.
```
