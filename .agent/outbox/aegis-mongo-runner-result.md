# AEGIS-03 Mongo 테스트 러너 격리 (완료, 커밋 없음)

## 소유 파일 (이 두 개만 수정/생성)

- `scripts/test_mongo_local.sh` — 전면 재작성 (재적용 완료. 12:37:54 `git checkout` 되돌림 이후 md5 `1190e81b...` 기준에서 좀비 판정 수정 1건 추가)
- `scripts/test_mongo_runner.test.mjs` — 신규 전용 안전성 테스트 8개
- `.agent/outbox/aegis-mongo-runner-result.md` — 이 보고서

`git status` 상 그 외 변경(AA 소스/스키마/문서/`tests/test_aegis_*.py`)은 전부 다른 Opus 소유이며 손대지 않았다.

## 고친 버그 (측정된 것)

기존 스크립트는 기동 성공 여부와 무관하게 cleanup 을 걸고, cleanup 이 **포트 기준**으로
`db.getSiblingDB("admin").shutdownServer()` 를 보냈다. 27019 를 이미 쓰고 있던 사용자의 다른
mongod 가 있으면 그 서버를 붙잡아 쓰고, 종료까지 시켰다.

새 러너의 fail-closed 규칙:

1. **점유 포트 선거부** — `/dev/tcp/127.0.0.1/PORT` 로 기동 전 확인. 사용 중이면 mongod/mongosh/uv 를 아예 실행하지 않고 중단.
2. **소유 PID 만 시그널** — `shutdownServer()`·`pkill`·`killall` 없음. 이 실행이 `$!` 로 받은 자식에게만 TERM → (10초 후) KILL → `wait`.
3. **소유 서버 확인** — 준비 판정은 `db.serverStatus().pid` 가 내 자식 PID 와 일치할 때만. 다르면 `rs.initiate` 도 pytest 도 하지 않고 즉시 중단.
4. **좀비 오판 차단** — `kill -0` 은 아직 수거 안 된 좀비에도 성공한다. `child_running()` 이 `ps -o state=` 로 `Z`/부재를 걸러낸다. (전용 테스트가 실제로 이 결함을 잡아냈다. 이 검사가 없으면 이미 죽은 mongod 를 "준비완료"로 보고 `rs.initiate` 를 쏜다.)
5. **소유 경로만 삭제** — `mktemp -d "$TMPDIR/pbl-mongo-test.XXXXXX"` 로 매 실행 고유 dbpath 생성. 삭제 전 절대경로·접두사·심링크 아님·`.pbl-mongo-test-owner` 도장의 PID == `$$` 를 모두 재검사. 하나라도 어긋나면 지우지 않고 경고만 남긴다.
6. **URI 격리** — `.env.local` 미사용. `env -u MONGODB_URI -u MONGO_URI -u MONGO_URL -u MONGODB_URL TEST_MONGODB_URI=mongodb://127.0.0.1:<port>/?replicaSet=pblrs&directConnection=true` 로만 pytest 를 띄운다. `--bind_ip 127.0.0.1`, `--nounixsocket`.
7. **포트** — 기본은 20000-28999 중 빈 포트 동적 선택. `TEST_MONGO_PORT` 명시도 지원하되 빈 문자열/비숫자/1024 미만/65535 초과/5자리 초과는 거부.
8. **종료코드** — pytest 종료코드를 그대로 전달. SIGINT 130, SIGTERM 143. 실패 시 삭제 전에 `mongod.log` 마지막 20줄 출력.

## 대상 테스트 확장

`pytest services/buyer-audit-api/tests -m mongo` (단일 `test_mongo_repository.py` 지정 폐기).
다른 Opus 가 방금 추가한 `tests/test_aegis_mongo.py` 도 `pytestmark = pytest.mark.mongo` 라 자동 포함된다.

## 실행한 검증

- `bash -n scripts/test_mongo_local.sh` → OK
- `node --test scripts/test_mongo_runner.test.mjs` → **8/8 pass**, 2회 연속 동일 (약 2.7초)
  1. 잘못된 `TEST_MONGO_PORT` 9종(``/` `/`abc`/`27019x`/`-1`/`0`/`80`/`65536`/`123456`) 거부 + 외부 명령 0회 실행
  2. 점유 포트: 거부, 호출 로그 비어 있음, 실제 리스너 생존·재접속 성공
  3. 기동 실패: 소유 dbpath 삭제, 소유 자식 종료, 이웃 `pbl-mongo-test.dec001` 디렉터리 보존, 무관한 `sleep` 프로세스 생존, `shutdownServer` 미발신, pytest 미실행
  4. 남의 서버가 다른 PID 보고: `rs.initiate`/`uv` 미실행, 중단
  5. pytest 실패코드 7 그대로 전달 + 상속 `MONGODB_URI`(live.example.invalid) 가 자식 env 에서 `<unset>`
  6. 정상 종료(0): 소유 자식/경로만 정리, 이웃 자원 보존
  7. 명시 포트 그대로 사용 + 동적 포트가 실제로 비어 있음
  8. 프로세스 그룹 SIGINT → 130, 소유 자원만 정리
- 실행 후 `$TMPDIR`·`/private/tmp` 에 `pbl-mongo-test.*` 잔여물 없음

테스트는 PATH 앞에 `mongod`/`mongosh`/`uv` 스텁을 깔고 `TMPDIR` 을 샌드박스로 돌린다.
실제 mongod/mongosh/uv·네트워크·사용자 DB 접속 없음.

## 한계 / 남은 것

- 실제 Mongo 통합(`npm run test:mongo:local`)은 **미실행**. AA 구현이 끝난 뒤 오케스트레이터가 돌려야 한다(지시대로 미완성 Python 테스트는 건드리지 않았다).
- `package.json` 은 소유 밖이라 그대로다. `test:mongo:local` → `bash scripts/test_mongo_local.sh` 경로가 그대로라 동작한다. 러너 테스트를 npm 스크립트로 묶으려면 오케스트레이터가 `"test:mongo:runner": "node --test scripts/test_mongo_runner.test.mjs"` 를 추가해야 한다.
- 점유 감지는 bash 의 `/dev/tcp` 에 의존(이 Mac 의 `/bin/bash` 3.2 에서 확인). 미지원 bash 라면 2번 테스트가 실패하며 드러난다. 그 경우에도 3·4번 규칙(자식 PID 일치 확인) 때문에 남의 서버를 initiate/종료하는 일은 없다.
- 커밋 없음. `PBL-coder`(P6-03) 미접촉.
