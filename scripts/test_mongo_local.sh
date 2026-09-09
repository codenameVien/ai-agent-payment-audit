#!/usr/bin/env bash
# mongo 마커가 붙은 pytest 를, 이 실행이 소유한 mongod 하나에서만 돌린다.
#
# fail-closed 소유권 규칙:
#   * 이미 사용 중인 포트는 mongod 기동 전에 거부한다. 남의 서버를 재사용하거나
#     initiate / shutdown 하지 않는다.
#   * 이 실행이 spawn 한 자식 프로세스에만 시그널을 보낸다. 포트 기준
#     shutdownServer(), pkill, killall 은 쓰지 않는다.
#   * 이 실행이 만들고 자기 PID 로 도장을 찍은 임시 dbpath 만 삭제한다.
#   * 응답한 서버의 PID 가 내 자식 PID 와 다르면 아무것도 건드리지 않고 멈춘다.
#   * pytest 는 이 루프백 인스턴스에만 접속한다. .env.local 이나 상속된
#     MongoDB URL 은 쓰지 않는다.
#
# macOS 기본 /bin/bash 3.2 에서 동작해야 한다. bash 4 전용 문법 금지.
set -euo pipefail

DIR_PREFIX="pbl-mongo-test."
OWNER_STAMP=".pbl-mongo-test-owner"
REPLICA_SET="pblrs"
MIN_PORT=1024
MAX_PORT=65535
DYNAMIC_PORT_BASE=20000
DYNAMIC_PORT_SPAN=9000
DYNAMIC_PORT_TRIES=64
READY_TRIES=160
READY_DELAY=0.25
STOP_TRIES=40
STOP_DELAY=0.25
readonly DIR_PREFIX OWNER_STAMP REPLICA_SET MIN_PORT MAX_PORT
readonly DYNAMIC_PORT_BASE DYNAMIC_PORT_SPAN DYNAMIC_PORT_TRIES
readonly READY_TRIES READY_DELAY STOP_TRIES STOP_DELAY

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
readonly SCRIPT_DIR REPO_ROOT

# 이 실행이 실제로 획득한 자원만 담는다. 비어 있으면 정리도 하지 않는다.
OWNED_DIR=""
OWNED_PID=""
PORT=""

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*" >&2
}

require_binaries() {
  local binary
  for binary in mongod mongosh uv; do
    command -v "$binary" >/dev/null 2>&1 || fail "${binary} 가 필요합니다."
  done
}

# 종료코드 0 = 루프백 포트에 이미 무언가 listen 중.
port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

pick_dynamic_port() {
  local attempt port
  for (( attempt = 0; attempt < DYNAMIC_PORT_TRIES; attempt++ )); do
    port=$(( DYNAMIC_PORT_BASE + RANDOM % DYNAMIC_PORT_SPAN ))
    if ! port_in_use "$port"; then
      printf '%s\n' "$port"
      return 0
    fi
  done
  fail "빈 포트를 찾지 못했습니다. TEST_MONGO_PORT 로 직접 지정하세요."
}

# 표준출력으로 포트 하나만 내보낸다. 거절은 전부 fail 로 끝난다.
resolve_port() {
  if [[ -z "${TEST_MONGO_PORT+set}" ]]; then
    pick_dynamic_port
    return
  fi

  local requested="$TEST_MONGO_PORT"
  [[ -n "$requested" ]] ||
    fail "TEST_MONGO_PORT 가 비어 있습니다. 자동 포트를 쓰려면 변수를 unset 하세요."
  [[ $requested =~ ^[0-9]+$ ]] ||
    fail "TEST_MONGO_PORT 는 숫자여야 합니다: '${requested}'"
  (( ${#requested} <= 5 )) ||
    fail "TEST_MONGO_PORT 가 범위(${MIN_PORT}-${MAX_PORT})를 벗어났습니다: '${requested}'"

  local port
  port=$(( 10#$requested ))
  (( port >= MIN_PORT && port <= MAX_PORT )) ||
    fail "TEST_MONGO_PORT 가 범위(${MIN_PORT}-${MAX_PORT})를 벗어났습니다: '${requested}'"

  ! port_in_use "$port" ||
    fail "포트 ${port} 는 이미 사용 중입니다. 남의 서버를 건드리지 않고 중단합니다."

  printf '%s\n' "$port"
}

create_owned_dir() {
  local tmp_root dir
  tmp_root="${TMPDIR:-/tmp}"
  tmp_root="${tmp_root%/}"
  [[ -d "$tmp_root" ]] || fail "임시 디렉터리가 없습니다: ${tmp_root}"

  dir="$(mktemp -d "${tmp_root}/${DIR_PREFIX}XXXXXX")" ||
    fail "임시 dbpath 생성에 실패했습니다."
  [[ -n "$dir" && -d "$dir" && ! -L "$dir" ]] ||
    fail "임시 dbpath 가 올바르지 않습니다: '${dir}'"
  case "$dir" in
    /*/${DIR_PREFIX}??????) ;;
    *) fail "예상하지 못한 임시 dbpath 형식이라 중단합니다: '${dir}'" ;;
  esac

  # 이 실행 소유임을 증명하는 도장. 삭제 직전에 다시 검사한다.
  printf '%s\n' "$$" >"${dir}/${OWNER_STAMP}"
  OWNED_DIR="$dir"
}

remove_owned_dir() {
  local dir="$OWNED_DIR"
  [[ -n "$dir" ]] || return 0
  OWNED_DIR=""

  case "$dir" in
    /*/${DIR_PREFIX}??????) ;;
    *) note "안전하지 않은 경로라 삭제하지 않음: '${dir}'"; return 0 ;;
  esac
  if [[ -L "$dir" || ! -d "$dir" ]]; then
    note "디렉터리가 아니라 삭제하지 않음: '${dir}'"
    return 0
  fi

  local stamp="${dir}/${OWNER_STAMP}"
  if [[ ! -f "$stamp" || -L "$stamp" ]]; then
    note "소유 도장이 없어 삭제하지 않음: '${dir}'"
    return 0
  fi
  local owner
  owner="$(cat "$stamp" 2>/dev/null || true)"
  if [[ "$owner" != "$$" ]]; then
    note "이 실행(pid $$)이 만든 디렉터리가 아니라 삭제하지 않음: '${dir}'"
    return 0
  fi

  rm -rf -- "$dir"
}

# 이 실행의 자식이 "지금 실행 중"인가.
# kill -0 은 아직 수거되지 않은 좀비에도 성공하므로, 종료한 mongod 를 살아있다고
# 오판할 수 있다. 상태까지 확인해야 fail-closed 가 성립한다.
child_running() {
  local pid="$1" state
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(ps -o state= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
  case "$state" in
    ""|Z*) return 1 ;;
  esac
  return 0
}

start_owned_mongod() {
  mongod \
    --dbpath "$OWNED_DIR" \
    --port "$PORT" \
    --bind_ip 127.0.0.1 \
    --replSet "$REPLICA_SET" \
    --nounixsocket \
    --logpath "${OWNED_DIR}/mongod.log" \
    --pidfilepath "${OWNED_DIR}/mongod.pid" &
  OWNED_PID="$!"
}

# 이 실행의 자식에게만 TERM -> (필요하면) KILL. 포트 기준 종료는 하지 않는다.
stop_owned_mongod() {
  local pid="$OWNED_PID"
  [[ -n "$pid" ]] || return 0
  OWNED_PID=""
  [[ $pid =~ ^[0-9]+$ ]] || return 0

  if child_running "$pid"; then
    kill -TERM "$pid" 2>/dev/null || true
    local waited=0
    while (( waited < STOP_TRIES )) && child_running "$pid"; do
      sleep "$STOP_DELAY"
      waited=$(( waited + 1 ))
    done
    if child_running "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  wait "$pid" 2>/dev/null || true
}

report_mongod_log() {
  local status="$1" log="${OWNED_DIR}/mongod.log"
  (( status != 0 )) || return 0
  [[ -n "$OWNED_DIR" && -f "$log" && ! -L "$log" && -s "$log" ]] || return 0
  note "--- mongod.log (마지막 20줄) ---"
  tail -n 20 "$log" >&2 || true
  note "--------------------------------"
}

cleanup() {
  local status=$?
  trap - EXIT
  stop_owned_mongod
  report_mongod_log "$status"
  remove_owned_dir
  exit "$status"
}

mongosh_eval() {
  mongosh --quiet --host 127.0.0.1 --port "$PORT" --eval "$1"
}

# 응답한 서버가 보고한 PID. 숫자 한 줄이 아니면 아무것도 내보내지 않는다.
observed_server_pid() {
  local raw
  raw="$(mongosh_eval 'print(db.serverStatus().pid.toString())' 2>/dev/null)" || return 1
  printf '%s\n' "$raw" \
    | tr -d '\r' \
    | sed -n 's/^[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' \
    | sed -n '$p'
}

# 0 준비완료 / 1 자식 조기종료 / 2 남의 서버 응답 / 3 시간초과
wait_for_owned_server() {
  local attempt seen
  for (( attempt = 0; attempt < READY_TRIES; attempt++ )); do
    child_running "$OWNED_PID" || return 1
    seen="$(observed_server_pid)" || seen=""
    if [[ -n "$seen" ]]; then
      [[ "$seen" == "$OWNED_PID" ]] || return 2
      # 응답과 검사 사이에 자식이 죽었을 수 있다. 살아있는 자식만 준비완료다.
      child_running "$OWNED_PID" || return 1
      return 0
    fi
    sleep "$READY_DELAY"
  done
  return 3
}

# 0 primary / 1 자식 조기종료 / 3 시간초과
wait_for_primary() {
  local attempt answer
  for (( attempt = 0; attempt < READY_TRIES; attempt++ )); do
    child_running "$OWNED_PID" || return 1
    answer="$(mongosh_eval 'db.hello().isWritablePrimary' 2>/dev/null)" || answer=""
    case "$answer" in
      *true*) child_running "$OWNED_PID" || return 1; return 0 ;;
    esac
    sleep "$READY_DELAY"
  done
  return 3
}

main() {
  cd -- "$REPO_ROOT"
  require_binaries

  PORT="$(resolve_port)"

  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  create_owned_dir
  note "격리 mongod 기동: 127.0.0.1:${PORT} (dbpath ${OWNED_DIR})"
  start_owned_mongod

  local rc=0
  wait_for_owned_server || rc=$?
  case "$rc" in
    0) ;;
    1) fail "mongod 가 기동 직후 종료했습니다 (포트 ${PORT})." ;;
    2) fail "포트 ${PORT} 에서 이 실행이 만들지 않은 서버가 응답했습니다. 건드리지 않고 중단합니다." ;;
    *) fail "mongod 기동 대기 시간을 초과했습니다 (포트 ${PORT})." ;;
  esac

  mongosh_eval "rs.initiate({_id:'${REPLICA_SET}',members:[{_id:0,host:'127.0.0.1:${PORT}'}]})" \
    >/dev/null || fail "레플리카셋 초기화에 실패했습니다 (포트 ${PORT})."

  rc=0
  wait_for_primary || rc=$?
  case "$rc" in
    0) ;;
    1) fail "primary 승격 전에 mongod 가 종료했습니다 (포트 ${PORT})." ;;
    *) fail "primary 승격 대기 시간을 초과했습니다 (포트 ${PORT})." ;;
  esac

  local uri status=0
  uri="mongodb://127.0.0.1:${PORT}/?replicaSet=${REPLICA_SET}&directConnection=true"
  env -u MONGODB_URI -u MONGO_URI -u MONGO_URL -u MONGODB_URL \
    TEST_MONGODB_URI="$uri" \
    uv run --project services/buyer-audit-api \
    pytest services/buyer-audit-api/tests -m mongo || status=$?
  exit "$status"
}

main "$@"
