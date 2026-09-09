# AEGIS-02 결과

2026-09-09. branch `feature/aegis-aa-v1`. 로컬 구현·검증만 수행했다. 실제 AA API·Provider API·체인 전송·AWS 호출은 하지 않았고 실제 지갑 키도 사용하지 않았다.

## 무엇이 실제로 도는가

`npm run aegis:smoke` 하나로 신규 기본 구성 전체가 별도 프로세스로 뜬다. 증거 API(실제 MongoDB) + Mock Provider Gateway 3개 + Mock Facilitator + 결제 실행 모듈이며 서로 HTTP로만 통신한다.

`사용자 → POST /purchases → POST /purchases/{id}/run → 결제 실행 모듈 → Provider Gateway → 402 → ERC-3009 서명 → Mock Facilitator verify/settle → 정산 확인 후 Mock 모델 응답 → 증거 기록 → 감사`

세 provider 모두 `REQUESTED → AA_SNAPSHOT_RECORDED → DECIDED → PAYMENT_INTENT_CLAIMED → PAYMENT_AUTHORIZED → PAYMENT_SETTLED → DELIVERED → AUDITED` 순서를 실제로 남긴다.

## 구현 파일

신규
- `services/buyer-audit-api/src/buyer_audit_api/core/aegis_payment.py` — 결정 증거에서 결제 조건 재도출(`read_terms`), purchaseId당 1회 예약, Facilitator 응답 기반 settle/fail, 전달 전제 확인
- `services/commerce-gateway/src/aegis/terms.ts` — RFC 8785 정준 JSON, BigInt 정확 소수 금액, terms binding 재계산
- `services/commerce-gateway/src/aegis/x402.ts` — x402 v2 exact + eip3009 + AEGIS 결정 binding 확장
- `services/commerce-gateway/src/aegis/evidence-client.ts` — 내부 증거 API 전용 클라이언트(직접 Mongo 접근 없음)
- `services/commerce-gateway/src/aegis/providers.ts` — OpenAI/Anthropic/Google Mock adapter, 정확한 provider model ID/version
- `services/commerce-gateway/src/aegis/provider-gateway.ts` — Provider Gateway HTTP, 402 발급, durable intent 결속, Facilitator 응답 검증
- `services/commerce-gateway/src/aegis/facilitator.ts` — 실제 EIP-712 복원과 `(from, nonce)` 단일 사용을 강제하는 Mock Facilitator
- `services/commerce-gateway/src/aegis/signer.ts` — 키 격리 서명 경계와 authorization digest
- `services/commerce-gateway/src/aegis/payment-executor.ts` — 결제 실행 모듈(조건 대조 → 서명 → 정산 기록 → 전달 기록)
- `services/commerce-gateway/src/aegis/main.ts` — provider-gateway / facilitator / payment-executor 3개 프로세스 진입점
- `scripts/aegis_local_stack.mjs`, `scripts/aegis_local_smoke.test.mjs`, `scripts/aegis_local_stack.test.mjs`
- `packages/schemas/fixtures/aa/model-catalog.runtime.json` — 실행용 catalog
- 테스트 `services/buyer-audit-api/tests/test_aegis_payment.py`, `services/commerce-gateway/tests/aegis-{terms,facilitator,runtime,durability}.test.ts`, `tests/aegis-fixtures.ts`

변경
- `core/aa_policy.py` `terms_binding_hash` 단일 정의(도메인 계층이 위임), `domains/ai_inference/aa_models.py` 위임
- `api/app.py` 신규 internal route 5개, 고정 local owner, same-origin/host 경계, Phase 6 표면 404
- `api/schemas.py` 신규 요청/응답 모델, `composition.py`/`settings.py`/`.env.example` 신규 기본 구성
- `adapters/repositories/memory.py` transactionHash 없는 정산의 잘못된 중복 판정 수정(문자열일 때만 비교)
- `domains/ai_inference/aa_catalog.py` `RUNTIME_CATALOG_PATH`
- `package.json` `aegis:stack`/`aegis:smoke` 추가, 구 스크립트는 `legacy:` 접두어로 명시

## 리뷰 지적 처리

1. **Facilitator network 결속(P1)** — settle은 `terms.token.chainId`에서 만든 CAIP network와 정확히 일치할 때만 통과한다. 회귀 `test_a_settlement_on_another_chain_is_refused`.
2. **실행 mode 위장(P2)** — settle/fail/delivery 요청에서 `execution_mode`를 제거했고 서버 설정(`AEGIS_EXECUTION_MODE`, 기본 mock)만 기록한다. `live` 지정 요청은 422. 회귀 `test_a_caller_cannot_label_its_own_execution_mode`.
3. **Facilitator nonce 재사용(P1)** — `(from, nonce)`를 await 이전에 동기 점유하고 authorization/requirements fingerprint가 같은 재요청만 합류시킨다. 다른 context는 `authorization is already used`. 회귀 4건(재요청·다른 금액·다른 window·동시 서로 다른 authorization).
4. **validAfter 경계(P2)** — `validAfter >= now`를 거부해 `AEGISToken.sol`의 `block.timestamp <= validAfter` revert와 맞췄다. 동일 초 경계 회귀 포함.
5. **binding 자기 비교(P2)** — `assertBinding`/`selectBoundRequirement`가 신뢰 실행 mode를 인자로 받는다.
6. **정밀도(조건부)** — evidence-client `exactUnits`가 terms amount/budget과 intent amount를 safe integer로만 받아들이고, `canonicalJson`은 정수 외 숫자를 거부한다. 회귀 `a unit count that lost precision on the wire is refused`.
7. **runner 소유권 3건** — 임의 Mongo URI/DB 옵션 제거(허용 옵션 화이트리스트), `rs.initiate` 전 자식 생존과 serverStatus PID·getCmdLineOpts dbPath 일치 확인, mkdtemp 직후 정리 경로 등록.
8. **Gateway durable 결속(P1, 재현 차단)** — Gateway가 `GET /internal/evidence/payment-intents/{id}`로 durable intent를 읽어 nonce·payer·금액·수취인·EIP-712 digest·상태(AUTHORIZED)를 모두 대조한다. 같은 purchase에 다른 nonce를 제시하면 402로 거부되고, 이미 SETTLED면 결제 없이 결과만 돌려준다. in-memory Map은 캐시일 뿐 권위가 아니다. 회귀 `two different valid authorizations for one purchase settle at most once`, `a second nonce is refused while the reserved attempt is still awaiting settlement`.
9. **재시작 후 결제 완료 재요청(P2)** — 예약 상태가 SETTLED면 실행 모듈은 서명·verify·settle 없이 결과만 회수한다. Gateway와 Facilitator를 모두 새 인스턴스로 교체한 회귀 `a settled purchase is delivered after a gateway and facilitator restart`에서 settle 수는 1로 유지된다.
10. **Facilitator 성공 응답 대조(P2)** — Gateway와 실행 모듈 모두 success 응답의 payer/network/제공된 amount/reference를 요구 조건과 정확히 대조한다. payer 누락은 서명자 주소로 채우지 않고 미확인으로 처리해 reconciliation으로 보낸다. 회귀 4건(payer 누락·다른 payer·다른 network·다른 금액) 모두 settle 0건.
11. **SIGKILL 후 종료 확인(P2)** — `stopAll`이 SIGKILL 이후에도 실제 exit를 기다리고 결과를 반환하며, 확인 실패 시 dbpath를 삭제하지 않고 보존을 알린다.
12. **모순된 success를 실패로 확정하던 문제(P1, 같은 범주 두 번째 수정)** — Gateway가 `success:true`를 `success:false`로 바꿔 402로 내려보내면 실행 모듈이 fail로 처리해 예약을 해제했다. 이제 Gateway는 응답을 그대로 전달하고 불일치를 별도 신호로 HTTP 409에 실어 보낸다. 실행 모듈은 신규 `POST /internal/evidence/aegis/payments/ambiguous`로 **예약을 유지한 채**(`reservation_action=hold`) `PAYMENT_RECONCILIATION_REQUIRED`를 기록하고, 외부 응답의 success/network/payer/amount/transaction을 `facilitatorResponse`에 원문 그대로(누락 필드는 null로) 보존한다. 정상 정산도 동일한 `facilitatorResponse`를 남기고, 응답 amount가 예약 금액과 다르면 서버가 거부한다. payer 누락을 서명자 주소로 채우지 않는다.

## 설계 판단

- **결제 상태 원자성은 기존 repository 재사용.** `claim_payment_intent`/`transition_payment_intent`의 원자 intent·예산 예약·append-only 이벤트를 그대로 쓴다. Gateway와 실행 모듈은 Mongo driver를 갖지 않고 증거 API HTTP만 사용한다.
- **`quote_id`는 협상 견적이 아니라 terms binding hash**다. 신규 이벤트 payload에 `termsBindingHash`로 명시한다.
- **정산 증거에 EVM hash를 쓰지 않는다.** `settlementReference`는 `x402mock:` 접두어이며 `0x` 시작 값은 API가 거부한다. `evidenceSource=SYNTHETIC_LOCAL`, `verificationBasis=facilitator_response`, `executionMode=mock`을 함께 저장한다.
- **Phase 6 표면은 신규 구성에서 404.** seller-execution·reputation outbox·Anchor·독립 receipt/Transfer/AuthorizationUsed 대조·legacy settle/fail·payment-view·seller-quotes가 대상이다. 동시에 composition에서 legacy workflow·RPC balance reader·outbox·terminal coordinator를 구성하지 않는다. `AEGIS_LOCAL_OWNER_ADDRESS`를 비우면 과거 SIWE 구성이 그대로 뜬다(역사 열람용, 수동 전용).
- **TS 코드 위치.** Provider Gateway를 seller-service가 아니라 `services/commerce-gateway/src/aegis/`에 두었다. 두 패키지가 정확 소수 금액·정준 JSON·terms binding을 공유해야 하는데, 새 workspace 패키지 없이 안전하게 공유할 방법이 없어 중복 구현을 피하는 쪽을 택했다. 런타임 프로세스·포트·키 경계는 분리돼 있다. 기존 seller-service는 과거 협상 판매자로 손대지 않았다.

## 검증

명령과 결과(모두 로컬):

- `npm run lint` — ruff, mypy(58 files), seller-service/commerce-gateway/dashboard tsc 통과
- `npm test` — Python 458 passed / 11 deselected(mongo), seller-service 30/30, commerce-gateway 120/120, schema 8 pricing cases 일치, Foundry 26/26
- `npm run test:mongo:local` — 11 passed(실제 격리 mongod)
- `npm run aegis:smoke` — 8/8. 실제 MongoDB + 6개 프로세스. 세 provider E2E, 동시 중복 1회 정산, 결제 완료 후 재실행 무재결제, 미인증 401, Phase 6 404, cross-site 403
- `node --test scripts/aegis_local_stack.test.mjs` — 4/4 runner 소유권 경계
- `npm test --workspace @pbl/dashboard` 4/4, `npm run build --workspace @pbl/dashboard` 성공

주요 negative 검증: 402 금액 변조 거부, 결정 조건과 다른 payload 거부, 잘못된 서명·payer 거부, nonce 재사용 거부, 동시 서로 다른 authorization 1회만 성공, 같은 purchase 다른 nonce 거부, 재시작 후 result-only 회수, 잘못 라우팅된 provider 409, 타임아웃 시 재결제 없이 reconciliation, 정산 후 provider 실패 시 재결제 없음, terminal 구매 재결제 없음, 안전정수 초과 금액 거부, 증거·결과·로그에 키 미노출.

모순된 Facilitator success 4가지(payer 누락·다른 payer·다른 network·다른 amount)는 TS와 Python 양쪽에서 각각 회귀로 덮었다: 상태는 `RECONCILIATION_REQUIRED`, 예약 `reserved_units`는 그대로 유지, `spent_units`는 0, `PAYMENT_SETTLED`/`PAYMENT_FAILED` 없음, 재시도에서 추가 서명·verify·settle 없음, `facilitatorResponse` 원문 보존.

## 남은 게이트와 한계

- AA 실제 인증 API는 여전히 미검증이다. snapshot `mode=fixture`, catalog `mappingProvenance=fixture`로 기록된다. 실행용 catalog의 provider model ID/version은 문서로 확인한 실제 식별자지만, 거기 붙은 AA 가격·시간·index는 synthetic fixture이며 해당 모델의 실측값이 아니다.
- Mock Facilitator 성공은 실제 블록체인 결제가 아니다. 잔액은 조회하지 않았고 어디에도 온체인 잔액으로 표시하지 않는다.
- AEGIS 토큰은 `status=prepared`, 주소는 zero address다. 서명은 그 도메인으로 이뤄지므로 실제 토큰에 재사용될 수 없다. 배포·자산 이동·실제 결제는 승인 경계로 남는다.
- 승인과 정산 사이에는 유효한 ERC-3009 authorization 서명이 intent 문서에 저장된다(기존 설계 유지). 정산 후에는 nonce가 소진돼 재사용할 수 없다.
- 단일 사용자 loopback 데모다. 공개 다중 사용자 인증·접근 제어는 완성하지 않았고 주장하지도 않는다.
- **원문 기반 priority 재분류(`.agent/outbox/aegis-priority-audit-note.md`)는 이번 패킷에서 처리하지 않았다.** 감사 재계산 범위이며 AEGIS-03의 최종 E2E 전에 연결해야 한다.
- AWS 실제 배포는 수행하지 않았다.

## AEGIS-03 연결 메모

- `POST /purchases/{id}/run` 응답의 `payment`는 `payment_status`(settled/settled_delivery_failed/failed/unknown/already_terminal), `execution_mode`, `verification_basis`, `aa_mode`, `aa_mapping_provenance`, `settlement_reference`, `settlement_network`, `provider_result`를 담는다. UI는 이 값으로 Mock Provider·Mock Facilitator·AA fixture 라벨을 그대로 표시하면 된다.
- `settlement_reference`는 `x402mock:` 접두어다. 탐색기 링크로 만들면 안 된다.
- `DELIVERED` payload에는 응답 원문이 없다. `responseHash`/`responseId`/`observedExecutionMs`만 있고 원문은 `/run` 응답으로만 전달된다. 상세 화면에 원문이 필요하면 별도 저장 경로를 03에서 결정해야 한다.
- 신규 구성에서 SIWE 로그인 화면은 대상 서버에 존재하지 않는다(404). `/auth/me`가 고정 owner를 돌려준다.
- E2E는 `startAegisStack()`을 import해 재사용할 수 있다. 저장소는 이 실행이 소유한 임시 replica set으로 고정돼 있어 기존 DB를 건드리지 않는다.
