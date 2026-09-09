# Opus — AEGIS-03 UI 한정 구현

작업 위치 `/Users/vien/MyProjects/PBL-aegis`. 이는 승인된 03의 UI 부분이며 독립 packet을 늘리지 않는다. 02 coder가 backend/TS Gateway/scripts를 수정 중이다. **쓰기 소유권은 `apps/dashboard/**`만**이다. backend/core/shared schemas/scripts/root package/lockfile/docs/spec 수정 금지. commit/push/merge는 오케스트레이터에 맡긴다. 새 dependency 설치 없이 기존 스타일·컴포넌트를 재사용한다.

먼저 `apps/dashboard/AGENTS.md`와 작업에 필요한 `node_modules/next/dist/docs/` 가이드를 읽는다. Next 내부 convention을 추정하지 않는다. 시크릿·실제 API·AWS 사용 금지.

## 확인된 public 계약

현재 `api/app.py`는 local_owner 구성 시 SIWE 없이 owner를 결정하고 host/origin/sec-fetch-site를 제한한다. `/auth/siwe/*` 신규 경로는 등록하지 않는다. `/me`, `/wallet`, `/purchases`, `/purchases/{id}`, events, `/purchases/{id}/run` 공용 형태는 유지된다. UI는 기존 `/backend` same-origin rewrite를 사용하며 내부 API/token을 브라우저로 가져오지 않는다.

`POST /purchases` body는 `{domain:"ai_inference",request:{requestSchema:"aegis-aa-v1",prompt,...optional priority},budget_units?,policy:{}}`. request discriminator는 `domains/ai_inference/aa_request.py`에서 확인한다. priority 미지정은 필드를 생략한다. 응답의 `purchase_id`를 저장한 뒤 POST `/purchases/{id}/run`; run 응답은 `purchase_id,payment,audit`다. 결제 내부 payload 세부는 02 변경 중이므로 UI 로직을 이에 결합하지 말고 기존 상세 GET/events를 사용한다.

`/wallet`은 현재 balance_status=`rpc_not_configured`와 null balance를 반환할 수 있다. 이를 0 또는 실잔액으로 표시하지 않는다. source code를 read-only로 재확인하되 불확실한 필드를 만들지 말고 handoff에 남긴다.

신규 DECIDED는 `scoringPolicyVersion=aa-three-factor-v1`, `priority`, `weights`, `winner`, `candidates`, `rejected`, `tokens`, `token`, `snapshotId`, `snapshotHash`, `termsBindingHash`를 가진다. 정확한 nested keys는 `aa_models.py::to_payload` 및 `aa_policy.py`를 읽고 구현한다. `estimatedCompletionMs`와 점수는 decimal string일 수 있다. AA snapshot event가 주는 실제 공개 필드만 보여주고 공개 raw snapshot endpoint가 있다고 가정하지 않는다.

## 구현 범위

1. `/request`: 기존 prompt/budget/실행 동의를 유지하고 네 priority(default/price/speed/intelligence)와 별도 “요청에서 자동 판단” 선택을 제공한다. 자동은 priority 생략, 명시 default는 default 전송. 별도 가중치 승인 단계 없음. AEGIS budget, Mock 실행/명목 USD 환산을 명시한다. invalid/creating/running/retry 상태와 같은 purchaseId 재개를 유지한다. 기존 localStorage의 과거 PBLC pending ID를 신규 AEGIS로 무조건 재실행하지 말고 역사 상세로 안내하며 삭제·덮어쓰지 않는다.
2. 신규 UI에서 SIWE 로그인/MetaMask·ERC8004 평판 실행·Anchor·RPC 독립검증 완료를 요구하지 않는다. 401/403은 local API 설정/접근 실패로 정직하게 안내한다. public 다중 사용자 인증이 구현됐다는 문구 없음.
3. 대시보드는 읽기 중심을 유지한다. 구매 폼/실험 runner는 /request에만 둔다. 공통 shell/overview의 새 정책 설명은 Mock Provider/Facilitator와 조회 안 한 잔액 상태를 드러낸다.
4. 상세는 정책 discriminator로 분기한다. 신규: 가격·완료시간·AA 성능 점수와 최종합, priority/weights, 모델 ID/version, source/fixture 상태, 고정 선결제 금액, 시간은 benchmark 참고 추정치임을 표시한다. 관측시간은 모의 실행시간. 기존 PBLC 거래는 기존 token/address/저장 점수/평판/Anchor 증거를 과거 정책으로 유지한다. fixture AA를 실제 측정이라고 표현하지 않는다.
5. 기존 `/experiments` redirect와 keyboard/labels/mobile 스타일을 보존한다. 불확실한 read API 정보는 unavailable로 보여주고 backend 기능을 만들어 넣지 않는다.

## 확인과 handoff

소유 영역 focused UI 테스트를 갱신하고 `npm run test --workspace @pbl/dashboard`, `npm run lint --workspace @pbl/dashboard`, `npm run build --workspace @pbl/dashboard`를 실행한다. 같은 경로에서 02가 파일을 수정하지 않는지 확인한다. 원문 priority 감사/backend 연결/full E2E/outbound 검증은 이 작업에서 하지 않는다.

보고: 변경 파일, UI test/typecheck/build 결과, backend 필드 불확실성, /request→상세 연결의 실제 브라우저 미검증 범위를 명시한다. “전체 03 완료”가 아니라 “UI 부분 완료”로 보고한다. 02 fixes 통합 후 원문 priority 감사와 전체 E2E를 이어간다.
