# AEGIS 최종 문서 patch 체크리스트

2026-09-09, README.md/README.en.md/docs/ROADMAP.md/docs/HANDOFF.md만 읽고 작성했다. 현재 AEGIS-02 구현 중, 03 대기이므로 runtime/E2E 완료를 미리 기록하지 않는다. 사용자 승인 예산 연장은 남은 로컬 작업용이며 AWS 배포는 원하지 않는다는 지시를 유지한다.

## README.md / README.en.md

- [ ] Features/주요 기능과 구조도를 신규 흐름으로 맞춘다: /request→buyer→세 Mock Provider Gateway→결과; 결제 실행 모듈은 일반 코드, Facilitator 제출. SIWE/협상/평판/Anchor/RPC 독립검증을 현재 기능으로 내세운 문구는 역사 섹션으로 한정한다.
- [ ] 현재 한국어 41행/영어 39행의 실거래 완료 주장을 날짜가 있는 PBLC 역사 증거로 명확히 제한한다. 과거 proof가 신규 AEGIS 배포 또는 Mock settlement의 실거래 증명이 되지 않게 한다.
- [ ] 시작하기의 `/Users/vien/MyProjects/PBL` 경로는 통합 상태에 맞춘다. 아직 통합 전이면 실제 구현 worktree PBL-aegis임을 표기한다. “mock에서 결제·체인 검증은 실제” 문구는 신규 Mock Provider/Mock Facilitator/AA fixture 구분으로 교체한다.
- [ ] setup_keys/input_provider_keys/PROVIDER_MODE=real 안내는 신규 로컬 quickstart에서 제외한다. 세 실제 Provider 키 연결은 이번 범위가 아니다. AA key는 서버 env만, 없으면 fixture와 live-AA 외부 게이트다.
- [ ] 현재 예시의 75/30/35/11/3/1 테스트 수는 낡은 현재 빌드 주장이다. 최종 검증 날짜·SHA·정확한 suite 결과를 AEGIS_VERIFICATION에 연결하고 중복 수치 표를 최소화한다.
- [ ] 신규 네 priority/고정 세 가중치, AA 출처와 500 answer-token benchmark 조건, 명목 1 AEGIS=1 USD, 최대 출력 토큰 선결제·6-decimal ceiling을 간결히 설명한다. AA synthetic 숫자는 실제 해당 모델 측정값으로 표현하지 않는다.
- [ ] SIWE 후 fake-domain 예시를 local /request 실행과 read-only dashboard 예시로 교체하되 원문 암호화와 내부 API 보호 설명은 유지한다. 원문 priority 감사는 실제 03 구현/검증 상태에 맞춰 서술한다.

## docs/HANDOFF.md

- [ ] 첫 “현재 로컬 완료 범위”와 diagram은 02/03 결과로 다시 작성한다. 현재 5행의 독립 영수증·평판·Anchor 완료, Gemini/Nemotron seller는 신규 실행 설명에 부적합하다.
- [ ] 28행 로컬 실행은 검증된 신규 runner/composition으로 바꾼다. 기존 compose agents 명령은 신규 세 Gateway/Mock Facilitator를 실제 구성하는지 확인 전 추천하지 않는다. .env.local source와 기존 지갑 설정을 신규 fixture에 묵시적으로 재사용하지 않는다.
- [ ] 60~66행의 recovery/CAS/단일 writer 설명은 02에서 실제 유지된 경계만 새 역할 명칭으로 갱신한다. 실 RPC tx 회수 요구를 신규 Facilitator 근거와 혼합하지 않는다.
- [ ] **역사 proof 보존:** 89~100행 PBLC/PBLC V2/EvidenceAnchor 주소·배포 tx·ERC8004 등록 링크; 104행 2026-09-02 Permit2 결과; 121행 2026-09-04 Permit2 canonical 거래; 133행 PBLC V2 ERC3009 결과의 purchaseId/tx/amount/당시 잔액/실패·미완결 기록을 그대로 남긴다. 당시 잔액은 현재 잔액이 아니라고 구분한다.
- [ ] 77행 “PBLC smoke 재현”과 143행 재현 명령은 역사 재현 참고로만 보존하고 지금 실행하라는 quickstart로 제공하지 않는다. 102행 “신규 구매 PBLC V2 + RPC + 평판”은 당시 구현 설명으로 수정한다.
- [ ] AWS 인계는 준비 자료 위치만 유지하며 배포 실행/비용 생성 안내를 활성 next step으로 쓰지 않는다. 공개 다중 사용자 인증·접근 제어와 원문 보존 정책은 향후 별도 결정이다.
- [ ] 외부 게이트를 AA live 검증(키 없으면 미완료), AEGIS deploy/asset move/real payment(지갑·방식·예상 주소·가스·금액 승인 필요), Provider real API(이번 범위 제외), AWS(현재 미진행)로 분리한다. 구 Gemini/Nemotron smoke를 신규 필수 완료로 요구하지 않는다.

## docs/ROADMAP.md

- [ ] 기존 완료 Phase1~5와 실거래 하위 항목은 역사 섹션으로 유지한다. 옛 구현 완료 체크를 지우거나 새 정책을 구현한 것처럼 바꾸지 않는다.
- [ ] 맨 위에 AEGIS-01/02/03 상태와 tasks.md 링크를 둔다. 01 완료, 계약 하위 작업 완료, 02/03 최종 상태는 실제 리뷰/E2E 뒤 갱신한다. 예산 연장과 AWS 미진행을 간단히 기록한다.
- [ ] 기존 “신규 PBLC V2만 사용”, SIWE UI, 온체인 확인 문구는 그 phase 당시 정책임을 표시한다. 현재 외부 게이트는 신규 handoff와 일치시킨다.

## 명령을 최종 문서에 넣는 조건

현재 기존 명령 문자열 `npm run setup:python`, `npm run lint`, `npm test`, `npm run test:mongo:local`, `npm run dashboard`는 출처 문서에 있다. `npm run api`는 **02에서 기본 main이 신규 AEGIS local composition임을 확인하고 실행 검증한 뒤에만** quickstart에 넣는다. 신규 별도 runner를 만들었다면 최종 package script의 정확한 이름을 사용하고 추정 이름을 만들지 않는다.

Dashboard build/typecheck/E2E도 03 최종 실행 명령을 그대로 기록한다. 실패/skip/미실행을 통과로 합산하지 않는다. PBLC smoke, erc8004 register/status, token deploy, real-provider key setup, AWS terraform apply는 신규 quickstart 명령에 넣지 않는다. 계획-only AEGIS 명령도 실제 script와 offline 보장을 검증한 결과를 인용한다.
