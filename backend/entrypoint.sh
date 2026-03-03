#!/bin/sh
# 백엔드 컨테이너 시작 스크립트
# DB 초기화 후 Gunicorn 실행

set -e

echo "=== DB 초기화 시작 ==="
python init_db.py

echo "=== Gunicorn 시작 ==="
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 4 \
    --worker-class sync \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    app:app
