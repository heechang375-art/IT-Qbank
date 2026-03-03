# ─────────────────────────────────────────────────────────────────────────────
# Frontend Dockerfile
# - Python 3.11 slim 기반
# - Flask 프론트엔드 서빙 (HTML/CSS/JS) + /api 프록시
# - Gunicorn WSGI
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 먼저 복사 (레이어 캐시 최적화)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# entrypoint 실행 권한
RUN chmod +x entrypoint.sh

# 포트 노출
EXPOSE 8080

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
