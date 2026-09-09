# P6-03 선택 재사용 검토

2026-09-09, read-only inspection. 원본 `/Users/vien/MyProjects/PBL-coder`의 HEAD diff와 untracked 소스를 읽었고 실행·수정하지 않았다. 아래 경로는 모두 이 원본 worktree 기준이다. 전체 복사/merge 대신 명시된 독립 부분만 AEGIS-03에 재사용한다.

## 재사용할 부분

| 원본 경로와 위치 | 재사용 범위 | AEGIS 적용 조건 |
|---|---|---|
| `services/buyer-audit-api/src/buyer_audit_api/scenarios/mongo_guard.py:65` | URI host 파싱 및 loopback 제한 | 테스트 전용 Mongo만 연결, 외부 Atlas 연결·기존 설정 대체 금지 |
| 같은 파일 `:93`, `:328`, `:380` | execution ID와 정확한 DB 이름·deny-list·sentinel attestation 대조 후 그 DB만 cleanup | 새 AEGIS run namespace 적용; 기존 DB drop 없음. 해당 guard negative tests와 함께 가져오기 |
| 같은 파일 `:182`, `:225` | HistorySnapshotDigest 및 read-only capture port | 기존 history count/hash 전후 동등성. 이름에 reputation이 포함된 collection도 역사 보존 대상으로 계속 읽을 수 있음. 이는 평판 실행 경로 재활성화가 아님 |
| `services/buyer-audit-api/src/buyer_audit_api/scenarios/catalog.py:50`, `:59`, `:174`, `:211` | canonical hash, deterministic run ID, oracle comparator, frozen clock | 기존 scenario 목록/정책 기대값을 사용하지 않고 AEGIS catalog로 교체 |
| `services/buyer-audit-api/tests/test_phase6_scenario_http.py:26` | 실제 loopback HTTP server lifecycle shell | SIWE session·old quote driver 대신 신규 로컬 요청→AA→세 Mock Gateway→결제→감사 경로 호출 |
| `services/seller-service/tests/support/scenario-server.ts:430` | loopback-only bind, random port, HTTP transport conversion, close shell | SellerEngine/quote signer를 그대로 구성하지 말고 신규 Gateway transport 사용 |
| `services/buyer-audit-api/src/buyer_audit_api/core/runtime_guard.py`, TS 양 서비스 `src/runtime-guard.ts` | scenario-only 제어 필드/환경 주입을 일반 runtime에서 거부하는 원칙 | AEGIS의 정상 mock demo configuration과 test-only injection을 구분한다. 구 SIWE/RPC/ERC8004 필수 환경 목록을 신규 요구로 옮기지 않는다 |

## 그대로 가져오면 안 되는 부분

- `scenarios/composition.py:20,221,254,278`: PythonSiweVerifier, SIWE 설정, neutral reputation, reputation outbox 구성. 신규 local composition으로 대체한다.
- `scenarios/control.py:239-256,676-726`: SIWE 로그인과 평판 outbox claim/prepared/confirmed lifecycle. 신규 E2E에서 제외한다.
- `scenarios/injections.py:95,367,436,458,513`: Gemini/Nemotron 수동 benchmark/평판 점수, 판매자 signed quote, identity verifier, old five-factor decision decoration. AA fixture/mapping/세 요인 oracle로 교체한다.
- `scenarios/injections.py:657`: SyntheticLifecycleWriter는 실제 receipt가 필수라는 기존 전제를 우회하여 synthetic 이벤트를 직접 작성한다. 신규 Facilitator 응답 경로를 실제 서비스 API로 검증해야 하므로 전체 재사용 금지.
- `scenarios/models.py:107,123` 및 `tools/phase6-e2e/catalog/phase6.v1.json`: Facilitator success-no-Transfer/receiptStatusZero 시나리오의 독립 RPC truth 가정. 신규 검증은 Facilitator 응답 결속·불명확 상태·중복 방지로 개정한다. 실제 Transfer 독립 확인을 oracle에 요구하지 않는다.
- `seller-service/tests/support/scenario-server.ts:42-49,325`: PBLC token name, ERC8004 identity, 고정 견적 금액 및 SellerEngine 구성. 신규 AEGIS fixture identity와 동일 AA snapshot 계산에 맞춰 교체한다.
- `test_phase6_scenario_http.py:153-163`: siweSessionIssued, sellerQuoteCalls=2, balanced baseline 기대값은 폐기하고 신규 local identity/AA/세 provider/default 정책을 검증한다.
- `scripts/validate_schemas.mjs`의 추가 322행 전체를 복사하지 않는다. old catalog/schema 규칙에 종속된 검사이며 신규 AEGIS schema 계약에 필요한 항목만 다시 작성한다.

## outbound 증거의 한계 — 우선 수정

`scenarios/injections.py:160-185`의 ScenarioBoundaryCounters.to_payload는 realProvider/realRpcOrFacilitator/realErc8004/awsOrAtlas 값을 항상 0으로 반환한다. `test_phase6_scenario_http.py:141-148`은 그 고정값을 검사한다. `commerce-gateway/tests/support/local-ledger.ts:68` 역시 outbound increment 경로가 없음을 주석으로 설명한다. **이 코드는 outbound tripwire가 아니며 실제 외부 호출이 없었다는 검증으로 재사용하면 안 된다.**

AEGIS에서는 실제 사용 transport의 outbound 함수를 감싸 non-loopback 시도 시 카운트와 즉시 실패를 발생시키거나 테스트 socket interception을 사용한다. 키 없는 fixture E2E에서는 AA 외부 호출도 거부하고, 별도 live-AA 계약 검증에만 AA host를 허용한다. Mock Provider/Facilitator 및 AWS/chain 비실행 결과는 이 관측값으로 보고한다.

원본 변경사항 보존 기준은 `docs/p6-preservation-baseline.json`을 사용한다. 이 검토는 원본 테스트가 통과함을 보증하지 않으며 이번 작업에서는 아무 테스트도 실행하지 않았다.
