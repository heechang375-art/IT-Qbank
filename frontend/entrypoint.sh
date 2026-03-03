#!/bin/sh
set -e
exec gunicorn \
    --bind 0.0.0.0:8080 \
    --workers 2 \
    --worker-class sync \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    app:app
