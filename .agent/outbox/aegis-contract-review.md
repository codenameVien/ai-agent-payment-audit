# AEGIS 계약 준비 focused review

검토 범위는 AEGISToken.sol 및 테스트, aegis_token_plan.mjs 및 테스트, AEGIS_TOKEN_PREPARATION.md다. AA 진행 중 코드·환경변수·키·네트워크에는 접근하지 않았다.

## 수정 필요

- [P2] `docs/AEGIS_TOKEN_PREPARATION.md` §7 제목의 `승인 패킷 체크리스트 (사용자 승인 후 채운다)`는 사용자의 “사용 지갑, 배포 방식과 예상 주소, 가스 추정, 테스트 금액을 제시하고 승인을 받는다”와 순서가 반대다. 패킷은 승인 요청 전에 채워 제시하고, 그 패킷에 대한 승인 이후에만 실행하도록 고쳐야 한다. 현재 내용대로면 구체 지갑·주소·비용을 보지 못한 상태의 포괄 승인을 먼저 받게 된다. 이 수정은 배포나 RPC 실행 승인을 요구하지 않으며 문구를 바로잡는 것으로 충분하다.

## 통과 근거

- EIP-712 name/version/chainId/verifyingContract 결속, authorizer nonce 1회 소비, 시간 경계, low-s/v 검사와 revert rollback을 확인했다. 계약 코드에서 차단 결함은 발견하지 않았다.
- planner는 읽기와 오프라인 계산만 하고 배포·서명·네트워크 경로가 없다. 예상 주소와 로컬 gas 값은 실제 RPC 검증과 구분되고 미제공 값은 UNKNOWN이다.
- PBLC V1/V2 source의 `git diff 44295ba`가 비어 있어 보존됨을 확인했다.
- Reviewer 직접 실행: `forge test --root infra/contracts --match-contract AEGISTokenTest` 15/15 PASS, `node --test scripts/aegis_token_plan.test.mjs` 11/11 PASS. 광범위 테스트 및 실제 체인 검증은 실행하지 않았다.

## 재검토 — 해결됨

§7이 승인 요청 전에 지갑·예상 주소·gas·테스트 금액을 채워 제시하고 이후 승인·실행하도록 수정됐다. nonce 변경 시 주소 재제시, 로컬 참고 gas와 실제 입력 기반 추정/quote/추가 수수료 구분, 미확정 값에 대한 포괄 승인 금지까지 확인했다. 위 P2는 해결됐다. 코드 변경이 없는 문서 수정이므로 기존 26개 focused 테스트 결과는 유효하며 재실행하지 않았다.

VERDICT approve
