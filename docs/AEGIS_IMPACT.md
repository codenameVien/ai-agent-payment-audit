# 영향 범위와 보존 계획

기준: d084388. 실제 변경은 `/Users/vien/MyProjects/PBL-aegis`에서 한다. `/Users/vien/MyProjects/PBL-coder` P6-03 dirty 상태는 보존하며 유용한 변경을 파일별 검토 후 재구현/선택 적용한다.

| 경계 | 현재 | 신규 영향 | 보존 |
|---|---|---|---|
| AI selection.py/models.py/benchmark.py/workflow.py | signed quote, freshness, reputation, 수동 quality/speed | AA snapshot+정확 mapping+세 요인+신규 priority | 구 schema와 저장 점수 reader |
| buyer core audit/events/projections/payment | 기존 terminal/reputation/anchor 판정 | 신규 policy discriminator, Facilitator 근거, 새로운 evidence 필수 필드 | hash chain, terminal uniqueness, 과거 증거 |
| buyer API/session/composition | SIWE owner session | 고정 local owner와 same-origin 서버 경계 | 내부 인증/키/민감 payload 보호 |
| seller-service | Gemini/Nemotron 견적/협상 | 세 Provider Gateway Mock, 같은 snapshot 금액 검증 | 과거 quote 자료 읽기 |
| commerce-gateway | signer+RPC+ERC8004+Anchor | 결제 실행 모듈, Facilitator 기반 상태 | ERC3009 nonce/예산 예약/중복 방지, historical types |
| infra/contracts/src/DemoTokenV2.sol | PBLC constant name/symbol, non-upgradeable | 별도 AEGIS 계약/배포 plan | 기존 소스·주소·PBLC 거래 |
| dashboard | SIWE/기존 점수·평판 UI | /request 로컬 Mock 흐름, 신규 점수/출처, history branching | read-only dashboard, 원래 history 증거 |
| scripts/Phase6 | RPC/reputation 중심 scenario | 새 3-provider/AA/terms/duplicate oracle | safe isolation/zero-write/outbound 검사 재사용 |
| 문서/AGENTS | 구 신규 runtime invariants | 2026-09-09 정책 우선 표기·역사 구분 | 기존 commit와 완료 이력 |

## 구체적인 설계 근거

`infra/contracts/src/DemoTokenV2.sol:7-10`은 name/symbol/version/decimals 상수이며 5행은 non-upgradeable 계약임을 설명한다. 이름만 바꾸는 배포 계약 mutation은 지원되지 않는다.

기존 selection.py는 reputation import, balanced/quality 프리셋과 freshness 계산을 가진다. 이를 신규 policy에 재활용하면 요구 위반이므로 신규 policy discriminator를 두고 과거 reader와 분리한다.

전체 DB를 migration하지 않는다. 새 필드는 새 event/schema에 추가하며 과거 read에는 없을 수 있음을 허용한다. 테스트 전후 기존 기록 digest/count 동등성은 read-only 조회 가능한 환경에서 검사하며 기존 DB cleanup은 하지 않는다. Local test DB는 별도 sentinel로 제한한다.

## 검증 증거가 주장할 수 있는 범위

- fixture AA 계약 통과는 실제 AA 응답 검증이 아니다.
- mock Facilitator success는 실제 블록체인 결제가 아니다.
- 실제 Facilitator 응답이 있어도 앱의 독립 온체인 검증 완료는 아니다.
- 내부 hash chain 검증은 온체인 기준점이 보증하는 불변성은 아니다.
- mock provider observedExecutionMs는 실제 모델 성능 측정이 아니다.
- local owner 흐름은 공개 다중 사용자 접근 제어 완성이 아니다.
