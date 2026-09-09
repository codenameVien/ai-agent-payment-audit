# AEGIS 계획 검토

- 검토 기준: 계획 commit `44295ba`, 사용자 확정 요구 2026-09-09.
- 범위: `AGENTS.md`, `aidlc-docs/inception/{requirements,design,tasks}.md`, `docs/AA_API_CONTRACT_CHECK.md`, 기존 `DemoTokenV2.sol`의 명칭 불변성.
- 이번 결과는 계획 일관성 검토이며 진행 중인 구현 및 실행 테스트 승인이 아니다.

구체적인 차단 이슈 없음.

가격은 6 decimals에서 `ceil(inputTokens × inputPrice + maxOutputTokens × outputPrice)`로 환산되고 십진 정밀 연산·uint256 범위·0 결제 중단이 명시돼 있다. hard filter 이후 후보만 세 요인의 분모로 사용하며 고정 가중치, 정확한 동점 비교, 결정적 문자열 순서가 요구사항과 일치한다.

AA root index version·pagination·명시 mapping·누락 중단·원본 해시가 계획에 포함돼 있고 fixture/live와 공식 문서 확인/실제 인증 응답 검증을 구분한다. Mock 모델 mapping을 실제 검증으로 주장하지 않는 경계도 명시돼 있다.

SIWE 제거 이후 loopback/same-origin 구성·서버 고정 owner·내부 인증 및 origin/host 보호를 유지한다. 결제 키 격리와 증거 API의 MongoDB 단일 소유권을 보존하며, 변경 없는 과거 reader와 테스트 DB 분리를 요구한다.

결제는 신뢰 snapshot에 따른 Gateway 재계산과 buyer/실행 모듈 대조, 원자 예약과 성공 유일성, 불명확 상태 재결제 금지로 연결된다. 신규 결제 상태 근거는 Facilitator 응답이며 독립 온체인 검증이나 Anchor 무결성 보장으로 표현하지 않는다.

기존 PBLC V2의 constant name/symbol과 변경 함수 부재는 신규 계약 준비 방향을 뒷받침한다. 실제 배포·자산 이동·테스트넷 결제 및 AWS 배포 경계가 분리돼 있다.

VERDICT approve
