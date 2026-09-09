# AEGIS-03 UI focused review

apps/dashboard의 미커밋 변경과 신규 aegis helper/renderer/tests만 검토했다. backend 수정·Next build·실제 API·브라우저 실행은 하지 않았다.

## 차단 발견

**[P1] 기존 공용 pending key의 PBLC ID를 신규 구매로 재실행한다.** `src/components/purchase-request.tsx`는 변경 전부터 사용하던 `pbl:purchase-request-id`를 그대로 `purchaseId` state에 넣고 `/purchases/{id}/run`을 호출한다. 과거 여부를 검사하는 GET이나 schema discriminator가 없다. 별도 `pbl:normal-experiment-purchase-id`만 과거 ID로 처리하므로, 이전 /request에서 남은 PBLC pending ID는 보호되지 않는다. 또한 새 실행 성공 시 공용 key가 삭제된다. 사용자의 “과거 pending ID를 신규 AEGIS로 무조건 재실행하지 않고 삭제·덮어쓰지 않는다” 요구에 어긋난다. 신규 전용 storage key를 사용하고 기존 두 key는 읽기 전용 역사 링크로 남기거나, 저장된 ID를 public detail GET으로 확인한 뒤 aegis-aa-v1임이 입증된 경우에만 재개하도록 해야 한다. 확인 실패 중 실행도 막아야 한다.

재현 경로는 코드에서 결정적이다: 기존 버전에서 `pbl:purchase-request-id=legacy-purchase`가 저장된 브라우저 → 새 /request mount → setPurchaseId(legacy-purchase) → 실행 동의/submit → 신규 생성 건너뛰고 legacy-purchase/run POST. 현재 테스트는 storage key 문자열 존재와 legacy 별도 key 처리를 확인할 뿐 이 공용 key 사례를 검사하지 않는다.

## 확인 결과

- 신규 request schema·네 priority·auto 생략은 API 계약과 일치한다. 상세는 저장된 request discriminator로 신규·과거 renderer를 분리한다.
- 새 DECIDED의 source/eligible 점수/가중치/금액 및 snapshot mode를 읽고 모의 정산·벤치마크 참고 시간을 명시한다. 과거 evidence renderer를 유지한다.
- 읽기 dashboard에 실행 폼을 추가하지 않았고 신규 SIWE 안내가 제거됐다. 미조회 잔액은 null 표시를 유지하며 신규 결제 상태를 tx hash로 판정하지 않는다.
- 직접 `npm run test --workspace @pbl/dashboard`: **16/16 PASS**, MODULE_TYPELESS_PACKAGE_JSON warning 1건. UI 실제 동작은 main의 브라우저 검증과 별도이며 테스트 통과가 위 pending 재실행 경계를 입증하지 않는다.

VERDICT block

## Pending-key 수정 재검토

- 기존 P1의 **과거 두 key 보존/재실행 문제는 해결**됐다. `aegis:purchase-request-id`만 신규 pending으로 읽고, 기존 `pbl:purchase-request-id` 및 `pbl:normal-experiment-purchase-id`는 변경하지 않는 역사 링크다.
- 신규 key의 GET 결과로 foreign/completed/blocked/resume을 구분하며, prompt hash·원래 priority·명시적 예산이 달라지면 저장 ID를 재실행하지 않는 처리도 확인했다.
- 직접 `node --test apps/dashboard/tests/pending-request.test.mjs`: **11/11 PASS**. 이 검증은 helper 단위이며, 컴포넌트의 조회 대기 상태를 검사하지 않는다. CSS/nav·build·서버·실제 API는 이번 범위에서 실행/검토하지 않았다.

### 남은 동일 경계 — [P1] 최초 pending GET 대기 중 새 실행 허용

`apps/dashboard/src/components/purchase-request.tsx:50,53-69,91-114,264`에서 초기 `pending=null`은 blocked가 아니다. GET이 끝나지 않아도 동의하면 submit 버튼과 run guard를 통과하고, `resumable=null`이므로 `/purchases`를 새로 생성하여 기존 신규 pending key를 덮어쓴 뒤 실행한다. 예: `aegis:purchase-request-id`에 아직 진행 중인 ID 저장 → 해당 GET 응답 지연 → 같은 요청 입력/동의/submit → 기존 ID 확인 전에 별도 purchaseId 생성·실행. 동일 사용자 요청을 다른 ID로 중복 실행할 수 있어 서버의 purchaseId별 중복 방지로 해결되지 않는다.

최소 수정: 초기/재조회 중임을 명시적으로 구분하고 조회 완료 전 button과 submit handler를 모두 차단한다. 지연된 GET 동안 생성/run POST가 없고 key가 유지되는 회귀 검증을 추가한다. 기존 해결된 과거 key 문제는 다시 열지 않는다.

VERDICT block

## 최종 pending gate 재검토 — 해결

남은 조회 대기 P1도 해결됐다. `canSubmitRequest`가 초기/재조회 `pending=null` 및 blocked 상태를 거부하고, 컴포넌트 버튼과 submit handler 모두 동일 판정을 사용한다. `resolvePending`은 재조회 시작 시 null로 돌아간다. 따라서 앞서 제시한 지연 GET 중 제출 경로가 차단된다.

직접 `node --test apps/dashboard/tests/pending-request.test.mjs`: **13/13 PASS**. 지연 promise 중 gate 차단, 완료 후 재개 가능, blocked/busy/미동의 차단 테스트를 확인했다. 테스트는 helper 동작이며 컴포넌트 연결은 소스에서 확인했다. 이전 두 역사 key와 변경 payload 보호도 유지된다. 이번 리뷰의 구체적 차단 발견은 모두 해결됐다. CSS/nav 실제 브라우저 및 전체 검증은 main 담당으로 이 판정에 포함하지 않았다.

VERDICT approve
