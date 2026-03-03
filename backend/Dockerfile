# ─────────────────────────────────────────────────────────────────────────────
# Backend Dockerfile
# - Python 3.11 slim 기반
# - Flask API + Gunicorn WSGI
# - DB 초기화 자동 실행 (entrypoint.sh)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# 시스템 패키지 업데이트 & 기본 도구
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
 && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리
WORKDIR /app

# 의존성 먼저 복사 (레이어 캐시 최적화)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# entrypoint 실행 권한
RUN chmod +x entrypoint.sh

# 포트 노출
EXPOSE 5000

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# 컨테이너 시작 시 DB 초기화 → Gunicorn 실행
ENTRYPOINT ["./entrypoint.sh"]
