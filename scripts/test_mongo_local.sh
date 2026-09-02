#!/usr/bin/env bash
# Runs the real Mongo repository test against an isolated native mongod.
set -euo pipefail

command -v mongod >/dev/null || { echo "mongod가 필요합니다." >&2; exit 1; }
command -v mongosh >/dev/null || { echo "mongosh가 필요합니다." >&2; exit 1; }
command -v uv >/dev/null || { echo "uv가 필요합니다." >&2; exit 1; }

TEST_PORT="${TEST_MONGO_PORT:-27019}"
TEST_DIR="$(mktemp -d /private/tmp/pbl-mongo-test.XXXXXX)"
MONGO_PID=""

cleanup() {
  mongosh --quiet --port "$TEST_PORT" \
    --eval 'db.getSiblingDB("admin").shutdownServer()' >/dev/null 2>&1 || true
  if [[ -n "$MONGO_PID" ]]; then
    wait "$MONGO_PID" 2>/dev/null || true
  fi
  case "$TEST_DIR" in
    /private/tmp/pbl-mongo-test.*) rm -rf -- "$TEST_DIR" ;;
    *) echo "안전하지 않은 임시 경로라 삭제하지 않음: $TEST_DIR" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

mongod \
  --dbpath "$TEST_DIR" \
  --port "$TEST_PORT" \
  --replSet pblrs \
  --bind_ip 127.0.0.1 \
  --logpath "$TEST_DIR/mongod.log" \
  --pidfilepath "$TEST_DIR/mongod.pid" &
MONGO_PID="$!"

for _attempt in {1..30}; do
  if mongosh --quiet --port "$TEST_PORT" --eval 'db.runCommand({ping:1}).ok' \
    2>/dev/null | grep -q 1; then
    break
  fi
  sleep 0.25
done

mongosh --quiet --port "$TEST_PORT" \
  --eval 'rs.initiate({_id:"pblrs",members:[{_id:0,host:"127.0.0.1:'"$TEST_PORT"'"}]})' \
  >/dev/null

for _attempt in {1..30}; do
  if mongosh --quiet --port "$TEST_PORT" --eval 'db.hello().isWritablePrimary' \
    2>/dev/null | grep -q true; then
    break
  fi
  sleep 0.25
done

TEST_MONGODB_URI="mongodb://127.0.0.1:${TEST_PORT}/?replicaSet=pblrs&directConnection=true" \
  uv run --project services/buyer-audit-api \
  pytest services/buyer-audit-api/tests/test_mongo_repository.py -q
