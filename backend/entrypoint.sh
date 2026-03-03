#!/bin/sh
# Backend entrypoint
# - Try MySQL init first
# - If DB is unavailable, continue boot with sqlite fallback mode

set -e

echo "=== DB init start ==="
DB_HOST_CHECK="${DB_HOST:-localhost}"
DB_PORT_CHECK="${DB_PORT:-3306}"
USE_SQLITE="${USE_SQLITE_FALLBACK:-true}"

if [ "$USE_SQLITE" = "true" ]; then
  if ! python -c "import socket; socket.create_connection(('${DB_HOST_CHECK}', int('${DB_PORT_CHECK}')), timeout=1).close()"; then
    echo "=== WARN: MySQL unreachable. Skip init_db.py and use sqlite fallback ==="
  else
    if ! python init_db.py; then
      echo "=== WARN: init_db.py failed. Continue with app boot (fallback DB mode) ==="
    fi
  fi
else
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
