# IT-Qbank

Flask + MySQL 기반의 IT 문제은행 서비스입니다.  
현재 코드 기준으로 AI(Gemini) 문제 생성, DB 캐시 재사용, 사용자별 풀이 이력, 오답 재출제, Docker Compose 실행, Kubernetes 배포를 지원합니다.

---

## 핵심 기능

- IT 카테고리 8종 지원: `programming`, `web`, `database`, `network`, `linux`, `cloud`, `kubernetes`, `security`
- 문제 수 선택: `5 / 10 / 15 / 20` (API는 최대 50)
- 난이도 선택: `easy / mixed / hard`
- 문제 형식 선택: `concept / practical / cert / mixed`
- AI 우선 생성 + DB 보강 하이브리드 출제
- 사용자별 최근 출제 문제 해시 기반 중복 회피
- 풀이 결과 저장, 시도별 상세 리뷰, 누적 오답 재출제
- Docker Compose 로컬 실행 지원
- Kubernetes NodePort / Gateway API 배포 지원

---

## 프로젝트 구조

```text
IT-Qbank/
|-- backend/               # Flask API 서버
|-- frontend/              # Flask 프론트엔드 + /api 프록시
|-- db/                    # MySQL 이미지 빌드 파일
|-- mysql/                 # MySQL 설정
|-- docs/images/           # README 이미지
|-- k8s/                   # Kubernetes 매니페스트
|   |-- 00-namespace.yaml
|   |-- configmap.yaml
|   |-- secret.yaml
|   |-- mysql-*.yaml
|   |-- backend-*.yaml
|   |-- frontend-*.yaml
|   |-- gateway.yaml
|   |-- network-policy.yaml
|   `-- apply-ordered.ps1
|-- docker-compose.yml
|-- .env.example
|-- README.md
`-- QuickStartGuide.md
```

---

## 현재 코드 기준 주요 설정

### Backend API

- 헬스체크: `GET /api/health`
- AI 상태 확인: `GET /api/ai/health`
- 카테고리 목록: `GET /api/categories`
- 단일 카테고리 출제: `GET /api/questions/<category>`
- 전체 혼합 출제: `GET /api/questions/all/mixed`
- 오답 재출제: `POST /api/retry-wrong`
- 제출/채점: `POST /api/submit`
- 이력 조회: `GET /api/history/<user>`
- 시도 상세: `GET /api/history/<user>/<attempt_id>`
- 누적 오답 ID: `GET /api/history/<user>/wrong-ids`
- 관리자 초기화: `DELETE /api/admin/purge-questions`

### Frontend

- 기본 포트: `8080`
- `/api/*` 요청은 `BACKEND_URL`로 프록시
- 프록시 타임아웃 환경변수:
  - `FRONTEND_PROXY_TIMEOUT`
  - `FRONTEND_PROXY_CONNECT_TIMEOUT`

---

## 환경 변수

`.env.example`를 복사해 `.env`를 만든 뒤 필요한 값만 수정하면 됩니다.

### 공통/DB

- `MYSQL_ROOT_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `FLASK_DEBUG`
- `USE_SQLITE_FALLBACK`

### Gemini / AI

- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_MODEL_CANDIDATES`
- `GEMINI_API_URL`
- `GEMINI_TIMEOUT`
- `AI_REQUEST_BUDGET_SEC`

### 부팅/정리 옵션

- `PURGE_DEFAULT_ON_BOOT`
- `PURGE_SHORT_ON_BOOT`
- `INIT_DB_SEED`
- `ADMIN_SECRET_KEY`

### Frontend 프록시

- `BACKEND_URL`
- `FRONTEND_PROXY_TIMEOUT`
- `FRONTEND_PROXY_CONNECT_TIMEOUT`

### 이미지 태그

- `DOCKER_HUB_USERNAME`
- `BACKEND_IMAGE_TAG`
- `FRONTEND_IMAGE_TAG`

---

## 로컬 실행

### Docker Compose

Windows:

```powershell
cd IT-Qbank
copy .env.example .env
docker compose up -d --build
```

Linux/macOS:

```bash
cd IT-Qbank
cp .env.example .env
docker compose up -d --build
```

접속 주소:

- 프론트엔드: `http://localhost:8080`
- 백엔드 헬스: `http://localhost:5000/api/health`
- AI 상태: `http://localhost:5000/api/ai/health`

종료:

```bash
docker compose down
```

### Python 로컬 실행

1. MySQL 준비

```sql
CREATE DATABASE quizdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'quizuser'@'%' IDENTIFIED BY 'quizpassword';
GRANT ALL PRIVILEGES ON quizdb.* TO 'quizuser'@'%';
FLUSH PRIVILEGES;
```

2. 백엔드 실행

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python init_db.py
python app.py
```

3. 프론트엔드 실행

```bash
cd frontend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

로컬 직접 실행 시에는 `DB_*`, `GEMINI_*`, `ADMIN_SECRET_KEY`, `BACKEND_URL` 등을 환경변수로 먼저 설정해야 합니다. 자세한 예시는 [QuickStartGuide.md](/c:/Users/campus3S026/IT-Qbank/QuickStartGuide.md)에서 확인할 수 있습니다.

---

## Kubernetes 배포

### 현재 매니페스트 기준 구성

- Namespace: `hc-quiz-bank`
- Backend Service: `backend-service:5000`
- Frontend Service: `frontend-service:8080`
- Frontend NodePort: `30080`
- Gateway 이름: `quiz-gateway`
- Gateway 포트: `8000`
- GatewayClass: `traefik`

### 적용 순서

PowerShell 스크립트:

```powershell
.\k8s\apply-ordered.ps1
```

수동 적용:

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/mysql-pvc.yaml
kubectl apply -f k8s/mysql-deployment.yaml
kubectl apply -f k8s/mysql-service.yaml
kubectl apply -f k8s/backend-deployment.yaml
kubectl apply -f k8s/backend-service.yaml
kubectl apply -f k8s/frontend-deployment.yaml
kubectl apply -f k8s/frontend-service.yaml
kubectl apply -f k8s/network-policy.yaml
kubectl apply -f k8s/gateway.yaml
```

### 접속 방법

NodePort:

- `http://<NODE_IP>:30080`

Gateway API:

- `http://<GATEWAY_IP>:8000`

상태 확인:

```bash
kubectl get pods -n hc-quiz-bank
kubectl get svc -n hc-quiz-bank
kubectl get gateway -n hc-quiz-bank
```

---

## 보안 주의사항

- `k8s/secret.yaml`에는 실제 운영 키를 직접 커밋하지 않는 것이 안전합니다.
- `ADMIN_SECRET_KEY`는 반드시 강한 랜덤 문자열을 사용하세요.
- 관리자 API 호출 시 `X-Admin-Key: <ADMIN_SECRET_KEY>` 헤더가 필요합니다.
- 실제 배포 전에는 `.env`, `k8s/secret.yaml`, 이미지 태그, 외부 노출 포트를 다시 점검하세요.

---

## 참고 문서

- 빠른 실행 및 점검: [QuickStartGuide.md](/c:/Users/campus3S026/IT-Qbank/QuickStartGuide.md)
