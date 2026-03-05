#!/bin/sh
# Backend entrypoint
# - MySQL가 실제로 쿼리를 받을 준비가 될 때까지 대기 후 init_db.py 실행
# - USE_SQLITE_FALLBACK=true 일 때만 연결 실패 시 sqlite로 fallback

set -e

echo "=== DB init start ==="
DB_HOST_CHECK="${DB_HOST:-localhost}"
DB_PORT_CHECK="${DB_PORT:-3306}"
USE_SQLITE="${USE_SQLITE_FALLBACK:-true}"
RETRIES="${DB_WAIT_RETRIES:-20}"
DELAY="${DB_WAIT_DELAY:-5}"

# TCP 포트 확인이 아닌 실제 MySQL 접속 가능 여부를 Python으로 확인
wait_for_mysql() {
  i=0
  while [ "$i" -lt "$RETRIES" ]; do
    i=$((i + 1))
    if python -c "
import pymysql, os, sys
try:
    c = pymysql.connect(
        host=os.getenv('DB_HOST','localhost'),
        port=int(os.getenv('DB_PORT','3306')),
        user=os.getenv('DB_USER','quizuser'),
        password=os.getenv('DB_PASSWORD','quizpassword'),
        db=os.getenv('DB_NAME','quizdb'),
        connect_timeout=3
    )
    c.close()
    sys.exit(0)
except Exception as e:
    print(f'[{$i}/${RETRIES}] MySQL not ready: {e}')
    sys.exit(1)
" 2>&1; then
      echo "[OK] MySQL is ready."
      return 0
    fi
    sleep "$DELAY"
  done
  echo "[FAIL] MySQL did not become ready after $((RETRIES * DELAY))s"
  return 1
}

if [ "$USE_SQLITE" = "true" ]; then
  # sqlite fallback 허용 모드: MySQL 연결 실패해도 계속 진행
  if wait_for_mysql; then
    python init_db.py || echo "=== WARN: init_db.py failed, continuing with app boot ==="
  else
    echo "=== WARN: MySQL unreachable. Booting with sqlite fallback ==="
  fi
else
  # sqlite fallback 금지 모드: MySQL 연결 필수 (실패 시 컨테이너 재시작)
  echo "=== Waiting for MySQL (USE_SQLITE_FALLBACK=false, mandatory) ==="
  wait_for_mysql
  python init_db.py
fi

echo "=== Gunicorn start ==="
exec gunicorn \
  --bind 0.0.0.0:5000 \
  --workers 4 \
  --worker-class sync \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  app:app