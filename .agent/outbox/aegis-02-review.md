# AEGIS-02 provisional review

현재 완성된 `core/aegis_payment.py` 및 `api/app.py`/`api/schemas.py` 신규 internal route만 검토했다. Gateway/Signer/composition은 구현 중이므로 미작성 기능을 결함으로 판단하지 않는다. 최종 artifact/commit 승인 판정은 보류한다.

## 현재 발견

- **[P1] Facilitator network와 선택한 chain의 결속 누락.** `core/aegis_payment.py:356` (`AegisPaymentService.settle`)은 network가 비어 있지 않은지만 검사한다. `terms.token.chainId`와 다른 network의 응답도 SETTLED 전이에 전달된다. 선택 조건과 Facilitator 응답 대조 요구를 충족하려면 정확한 예상 CAIP network와 비교해야 한다.
- **[P2] Mock 실행을 요청값으로 live 표시 가능.** `core/aegis_payment.py:55,327`, `api/schemas.py:845,853,866` 및 `api/app.py:2185`는 settle/fail/delivery의 실행 모드를 클라이언트가 `live`로 지정하게 한다. 별도 서버 mode 결속이 없고 settle 증거는 동시에 `SYNTHETIC_LOCAL`이다. 이번 mock-only runtime에서는 서버에서 mock으로 고정하거나 불일치를 거부해야 한다.

순수 로컬 method 재현: 실제 fixture decision의 chainId=84532를 사용하고 `_transition_context`와 repository transition만 AsyncMock으로 대체했다. `settle(facilitator_network='eip155:1', execution_mode='live', ...)`를 호출하면 거부 없이 repository transition에 `facilitatorNetwork=eip155:1, executionMode=live, evidenceSource=SYNTHETIC_LOCAL`이 전달된다. DB·네트워크·키 접근 없음.

## 조건부 관찰 — Gateway 완료 시 확인

`api/schemas.py:815`의 AegisPaymentTermsResponse.amount_units/budget_units 및 기존 중첩 PaymentIntentResponse는 JSON integer다. Python에서 안전한 uint256도 JS JSON Number로 읽으면 2^53 이상에서 달라진다. 완료된 Gateway/Signer가 Number.isSafeInteger로 거부하거나 exact string/lossless parse 경로를 쓰는지 확인할 때까지 이 항목은 최종 결함으로 확정하지 않는다. Number로 반올림한 다음 BigInt로 바꾸는 처리는 불가하다.

## 현재 확인한 보존 경계

새 internal route마다 require_internal을 호출하며 server bearer 비교를 유지한다. reserve는 기존 repository의 원자 intent/budget/event claim을 사용하고 terminal 전이는 성공/실패 상태를 재개방하지 않는다. 실제 Mongo 경합 검증이나 전체 suite는 이번 provisional review에서 실행하지 않았다.

최종 판정 보류: 구현 진행 중.

## TS 모듈 후속 잠정 검토

검토 범위: `services/commerce-gateway/src/aegis/{terms,evidence-client,x402,providers,signer,facilitator}.ts`. server/runner/test 미작성 여부는 판단하지 않았다.

- **[P1] Mock Facilitator nonce 재사용 성공 응답이 요청 조건에 결속되지 않는다.** `facilitator.ts:135-139`는 nonce만 같으면 서명·payer·amount·network·recipient 검증 전에 과거 success를 반환한다. 한 번 성공한 nonce를 복사해 signature나 amount를 바꾼 요청도 성공으로 응답한다. 동일 immutable authorization/context 재요청만 idempotent 성공이어야 하며 다른 context는 거부해야 한다. 또한 `verify`와 `beforeSettle`의 await 뒤 `#used.set` 전에 원자 nonce claim이 없어 같은 nonce의 서로 다른 유효 authorization이 경합하면 둘 다 success를 반환할 수 있다. Mock E2E가 ERC-3009 단일 사용을 입증하려면 fingerprint 결속 및 concurrent claim 회귀가 필요하다.
- **[P2] validAfter 경계가 준비된 실제 계약과 다르다.** `facilitator.ts:95`는 `validAfter > now`만 거부해 같은 시각을 허용한다. AEGISToken.sol은 `block.timestamp <= validAfter`에서 거부한다. Mock이 실제 계약에서 실패할 authorization을 통과시키므로 `validAfter >= now`를 거부하고 동일 초 경계 회귀로 맞춰야 한다.
- 기존 실행 mode 관찰의 TS 경로: `x402.ts:198-199`의 assertBinding은 신뢰해야 할 expected 실행 모드를 입력 `binding.executionMode`로 다시 구성해 그 필드가 항상 자기 자신과 같게 된다. API mode 수정과 함께 이 검증도 server mock 정책에 결속되는지 최종 확인해야 한다.

정밀도 조건부 관찰 업데이트: `terms.ts::requireInteger`는 Number.isSafeInteger를 검사한 **후** BigInt 변환하고 `termsBindingHash`도 unsafe amount를 거부한다. deriveTerms를 경유하는 금액은 조용히 반올림해 서명하지 않는다. evidence-client의 raw amount/budget number는 검증되지 않으므로 최종 executor 소비 경로에서 budget/intent도 같은 경계를 갖는지 아직 확인해야 한다.

Signer는 기존 EIP-712 타입으로 name/version/chainId/token 및 authorization 모든 필드를 hash/sign한다. 키는 private field 안에 있고 provider mock은 결과에 mock을 명시한다. 이번 단계에서는 source 검사만 했으며 TS build/광범위 suite/외부 실행은 하지 않았다.

## 로컬 스택 runner 소유권 검토

`scripts/aegis_local_stack.mjs`를 읽기만 했으며 runner/실제 mongod/네트워크는 실행하지 않았다. 기존 DB 손상 관측은 없다.

- **[P1] arbitrary Mongo URI로 기존 DB에 쓰기가 가능하다.** `startAegisStack`의 `options.mongodbUri ? ... : startOwnedMongo`는 외부·기존 DB URI를 제한 없이 받아 evidence API를 시작하고 wallet binding/policy를 쓴다. `options.database`도 임의 이름이다. 따라서 “기존 기록은 열지도 쓰지도 않는다”라는 범위와 달리 호출 옵션만으로 기존 DB에 신규 인덱스/기록을 작성한다. 이번 runner는 자체 소유 임시 mongod만 허용하거나, 외부 테스트 DB 입력을 허용할 경우 검증된 테스트 소유권/고유 DB 경계로 제한해야 한다.
- **[P1] rs.initiate 대상 PID 소유권 검증이 없다.** `startOwnedMongo`는 freePort probe를 닫고 mongod를 spawn한 뒤 해당 port에 즉시 mongosh를 실행한다. spawned mongod가 exit했거나 포트 경쟁에서 졌는지 검사하지 않는다. 다른 로컬 mongod가 그 사이 port를 취하면 사용자의 mongod에 rs.initiate를 보낼 수 있다. 준비 상태 전에 자식 생존과 대상 mongod PID/dbPath 일치를 확인한 뒤에만 변경 명령을 보내야 한다. 완료된 별도 Mongo runner의 소유권 경계를 재사용하는 것이 적절하다.
- **[P2] startup 실패 때 임시 dbPath 회수가 누락된다.** `mongo = await startOwnedMongo(children)`가 반환하기 전에 실패하면 외부 mongo 변수는 null이다. catch는 자식을 중단하지만 `removeOwnedDir(mongo?.dbPath)`에 경로가 없어 생성된 임시 DB를 남긴다. mkdtemp 직후 cleanup 소유권을 등록하고 child 종료 확인 뒤 회수해야 한다.

키 격리는 확인했다: baseEnv는 선택한 환경변수만 복사하고 `AEGIS_SIGNER_PRIVATE_KEY`는 payment-executor child env에만 주입한다. evidence/provider/facilitator child로 공유 secrets를 spread하지 않는다. runner 자체는 키를 생성하는 부모이므로 “runner never sees the key” 주석은 기술적으로 정확하지 않지만 다른 역할 프로세스에 키를 전달하는 결함은 확인되지 않았다.

## runner 세 발견 수정 재확인 (source only)

- arbitrary URI/DB P1 해결: 허용 옵션을 두 예산 값으로 제한하고 그 외 옵션은 자식 시작 전에 거부한다. DB 이름은 고정, startOwnedMongo만 실행한다.
- rs.initiate 소유권 P1 해결: 실제 응답의 serverStatus PID와 getCmdLineOpts dbPath가 spawn한 child.pid 및 생성 경로와 일치해야만 변경 명령으로 진행한다. 자식 exit/signal도 준비 루프에서 검사한다.
- startup 경로 유실 P2 해결: mkdtemp 직후 owned.dbPath를 등록하고 catch에서도 이 경로를 사용한다.
- **같은 cleanup 경계의 잔여 P2:** `ChildProcesses.stopAll`은 SIGTERM 4초 대기 이후 SIGKILL을 보내고 자식 exit를 기다리지 않은 채 반환한다. 이어서 stop/catch의 removeOwnedDir가 즉시 DB 경로를 삭제한다. “모든 자식 종료 후 삭제” 주석과 달리 Mongo가 종료됐다는 보장이 없는 경로다. SIGKILL 이후에도 해당 자식 exit를 확인하고 그 확인 실패 시 디렉터리를 보존해야 한다. 정상 SIGTERM 종료 경로에는 문제가 없다.

runner·mongod·네트워크·추가 테스트는 실행하지 않았다. 나머지 AEGIS02 코드는 이번 재확인에서 읽지 않았다.

## 완성 runtime 범위 재검토

### 해결된 기존 발견

- Python settle network는 terms.network와 정확히 비교한다. 실행 mode는 request에서 제거하고 서버 설정을 사용한다. 402 binding도 expected executionMode를 별도로 받는다.
- Facilitator는 `(from, nonce)` synchronous claim 및 context fingerprint로 동일 재요청만 합류시킨다. validAfter 동시각도 거부한다. 관련 순수 focused 회귀 통과.
- evidence-client exactUnits가 terms amount/budget/intent amount 모두 safe integer인지 확인한다. deriveTerms의 안전정수 검사와 함께 unsafe JSON Number를 BigInt로 조용히 바꿔 서명하지 않는다.
- runner arbitrary URI/DB, Mongo PID/dbPath 전검증, startup 경로 등록 수정은 유지된다. key는 executor child만 전달받는다. 새 기본 local composition은 legacy workflow/outbox/RPC verifier를 제외한다.

### 남은 차단

1. **[P1] Gateway가 durable intent의 payer/nonce/state를 결속하지 않는다.** `services/commerce-gateway/src/aegis/provider-gateway.ts`의 `#assertPaid`, `#settle`은 snapshot/금액/outer decision binding은 확인하지만 paymentIntent를 읽지 않는다. 따라서 같은 purchaseId에 서로 다른 유효 nonce를 제출하면 각각 settlement가 가능하다. local Map은 restart에서 사라지고 await 전후 경합도 막지 못한다. **순수 재현 성공:** 같은 fixture decision, 같은 실제 테스트 signer, 서로 다른 nonce `11…`, `22…`, 각각 새 Gateway 인스턴스, 같은 MockFacilitator를 사용했다. 두 응답이 모두 HTTP 200이고 settle 호출도 2회였다. 실제 HTTP/DB가 아닌 Request/Response 및 주입 fetch만 사용했다. Gateway가 저장된 단일 intent의 payer/nonce/authorization digest/state와 대조하고, 성공/불명확 상태에서는 새 settle을 하지 않도록 해야 한다.

2. **[P2] 재시작 후 이미 결제된 결과 재요청이 실패 또는 재settle로 흐른다.** `payment-executor.ts::execute`는 SETTLED에서 별도 결과 재조회 경로로 나가지 않고 다시 sign/paid call을 한다. Gateway cache만 비고 Facilitator가 살아 있으면 `/verify`가 used nonce를 거부하고 executor는 FAILED 기록을 시도하여 SETTLED conflict를 만든다. 둘 다 재시작하면 MockFacilitator 사용 nonce도 사라져 다시 success를 만든다. durable 결제 성공 증거를 읽어 결과만 반환해야 한다. 이 부분은 위 P1의 durable 상태 결속과 함께 해결할 수 있다.

3. **[P2] Facilitator 성공 금액을 대조하지 않고 결과를 제공한다.** Gateway `#settle`은 `settled.success===true`만으로 cache하고 provider를 실행하며 `payment-executor.ts`도 success 응답의 `amount`를 비교하지 않는다. Evidence settle API는 intent 금액을 쓰므로 다른 응답 amount가 저장/감사에서 사라진다. 선택 조건과 Facilitator 응답 대조를 위해 success network/payer 및 제공된 amount의 exact 값 일치를 확인하고 원본 응답 근거를 유지해야 한다. payer가 없는 응답을 executor signer 주소로 대체하는 것도 원본 확인으로 표현하면 안 된다.

4. **[P2] SIGKILL 이후 child 종료 확인 누락 유지.** `scripts/aegis_local_stack.mjs::ChildProcesses.stopAll`은 여전히 강제 종료를 보낸 즉시 반환한다. 뒤따른 removeOwnedDir 전에 실제 exit를 확인해야 한다.

Reviewer focused 실행: 이미 생성된 dist의 `aegis-terms.test.js`, `aegis-facilitator.test.js` **14/14 PASS**. Gateway 동일 purchase 두 nonce 재현은 **2 success**로 실패를 확인했다. main의 7/7 isolated smoke 및 전체 패키지 결과는 보고받았으나 직접 재실행하지 않았다. 외부 API·실DB·key 파일·광범위 suite 접근 없음. AEGIS03 UI/priority-original 재검증은 범위 밖이다.

VERDICT block

## 최종 네 범주 수정 재검토

- durable nonce/payer/value/recipient/digest/state 결속 해결. 새 Gateway는 Evidence API intent를 읽으며 예약되지 않은 두 번째 nonce를 거부한다.
- SETTLED 재시작 처리 해결. executor는 sign 이전 result-only 경로로 나가고 Gateway도 durable SETTLED이면 verify/settle 없이 provider 결과만 제공한다.
- SIGKILL cleanup 해결. stopAll이 강제 종료 이후 실제 종료 여부를 기다려 boolean을 반환하고 reclaim은 false이면 경로를 보존한다.
- Facilitator provided payer/network/amount 대조는 추가됐지만 같은 범주의 불명확 결과 처리·원본 보존은 아직 미완료다.

**남은 [P1] contradictory success를 확정 실패로 바꿔 예약을 해제한다.** `provider-gateway.ts::#settle`은 Facilitator가 `success:true`로 반환했으나 금액/네트워크/payer가 맞지 않을 때 `{...settled, success:false, errorReason}`를 만든다. handle은 이를 HTTP402로 반환한다. `payment-executor.ts::execute`는 이 응답을 fail 경로로 보내고 Python fail은 예약을 release한다. 실제 정산이 일어났을 수 있는 성공 응답 불일치는 unknown/reconciliation이어야 하며 실패로 확정하면 안 된다. 외부 응답의 원래 success/amount/payer/network/reference를 감사 증거에 그대로 보존해야 한다. 현재 settle API는 transaction/network/payer만 받고 fail API는 reason만 받아 원래 success와 불일치 수치가 저장되지 않는다. 이 조치는 기존 Facilitator exact provided fields/raw evidence 차단의 잔여 사항이다.

직접 focused 실행: `aegis-durability.test.js` + `aegis-facilitator.test.js` **16/16 PASS**. 같은 purchase 두 nonce 거부 및 재시작 result-only 회귀 통과를 확인했다. 당시 mismatch 회귀는 provider 결과를 제공하지 않는 것만 확인하며 최종 intent/reconciliation/예약 잔액/원본 응답 보존을 검증하지 않았다. 외부DB/API 및 전체 suite 재실행 없음.

VERDICT block

## 마지막 mismatch 범주 재검토 — 해결

Gateway가 원래 `success:true` 응답을 변조하지 않고 HTTP409와 함께 전달하며 provider를 호출하지 않는 것을 확인했다. Executor는 이 응답을 unknown-settlement 경로로 보내고 API는 `RECONCILIATION_REQUIRED` 전이 및 `reservation_action="hold"`를 사용한다. `facilitatorResponse`에 원래 success/amount/network/payer/transaction을 구조화해 보존하며 누락 payer/amount는 null로 남긴다. 재요청은 unknown 상태에서 종료해 추가 sign/settle을 만들지 않는다. 정상 settlement도 Facilitator amount를 별도로 기록한다.

Reviewer 직접 검증: `node --test .../aegis-durability.test.js` **8/8 PASS**; Python `test_aegis_payment.py -k 'contradictory or settlement_records or settlement_amount'` **6 PASS, 20 deselected**, 기존 deprecation warning 2건. 앞선 Python 검색어 `mismatch or ambiguous` 실행은 26 deselected/exit5였으며 테스트 성공으로 집계하지 않았다. 외부DB/API·전체 suite·UI는 실행하지 않았다.

이로써 기존 AEGIS02 차단 범주는 모두 해결됐다. 이전 세 범주의 해결 판정은 유지한다.

VERDICT approve
