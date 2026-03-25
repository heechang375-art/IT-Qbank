# QuickStartGuide

현재 폴더의 코드와 Kubernetes 매니페스트 기준으로 바로 실행할 수 있도록 정리한 빠른 시작 문서입니다.

---

## 1. 가장 빠른 실행

### Docker Compose

Windows:

```powershell
cd IT-Qbank
copy .env.example .env
```

Linux/macOS:

```bash
cd IT-Qbank
cp .env.example .env
```

필수 확인 값:

- `GEMINI_API_KEY`
- `ADMIN_SECRET_KEY`

권장 확인 값:

- `GEMINI_TIMEOUT`
- `AI_REQUEST_BUDGET_SEC`
- `FRONTEND_PROXY_TIMEOUT`
- `FRONTEND_PROXY_CONNECT_TIMEOUT`

실행:

```bash
docker compose up -d --build
```

확인:

- 프론트엔드: `http://localhost:8080`
- 백엔드 헬스: `http://localhost:5000/api/health`
- AI 헬스: `http://localhost:5000/api/ai/health`

중지:

```bash
docker compose down
```

---

## 2. 로컬 Python 실행

### 2-1. MySQL 준비

```sql
CREATE DATABASE quizdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'quizuser'@'%' IDENTIFIED BY 'quizpassword';
GRANT ALL PRIVILEGES ON quizdb.* TO 'quizuser'@'%';
FLUSH PRIVILEGES;
```

### 2-2. 백엔드 실행

Windows:

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

set DB_HOST=localhost
set DB_PORT=3306
set DB_NAME=quizdb
set DB_USER=quizuser
set DB_PASSWORD=quizpassword
set USE_SQLITE_FALLBACK=false
set GEMINI_API_KEY=YOUR_GEMINI_KEY
set GEMINI_MODEL=gemini-2.5-flash
set GEMINI_MODEL_CANDIDATES=gemini-2.5-flash
set GEMINI_TIMEOUT=60
set AI_REQUEST_BUDGET_SEC=40
set ADMIN_SECRET_KEY=YOUR_STRONG_RANDOM_KEY

python init_db.py
python app.py
```

Linux/macOS:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export DB_HOST=localhost
export DB_PORT=3306
export DB_NAME=quizdb
export DB_USER=quizuser
export DB_PASSWORD=quizpassword
export USE_SQLITE_FALLBACK=false
export GEMINI_API_KEY=YOUR_GEMINI_KEY
export GEMINI_MODEL=gemini-2.5-flash
export GEMINI_MODEL_CANDIDATES=gemini-2.5-flash
export GEMINI_TIMEOUT=60
export AI_REQUEST_BUDGET_SEC=40
export ADMIN_SECRET_KEY=YOUR_STRONG_RANDOM_KEY

python init_db.py
python app.py
```

### 2-3. 프론트엔드 실행

Windows:

```powershell
cd frontend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

set BACKEND_URL=http://localhost:5000
set FRONTEND_PROXY_TIMEOUT=120
set FRONTEND_PROXY_CONNECT_TIMEOUT=5
python app.py
```

Linux/macOS:

```bash
cd frontend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export BACKEND_URL=http://localhost:5000
export FRONTEND_PROXY_TIMEOUT=120
export FRONTEND_PROXY_CONNECT_TIMEOUT=5
python app.py
```

---

## 3. 현재 코드 기준 확인 포인트

기능 확인 체크리스트:

1. `GET /api/health` 응답에 `status=ok` 와 `db_mode`가 포함되는지 확인
2. `GET /api/categories` 응답에 8개 카테고리가 노출되는지 확인
3. 카테고리별 출제 시 `style=concept|practical|cert|mixed`가 반영되는지 확인
4. 혼합 출제 `GET /api/questions/all/mixed`가 정상 동작하는지 확인
5. 사용자 이름을 넣고 두 번 이상 요청했을 때 최근 문제 중복 회피가 동작하는지 확인
6. 결과 제출 후 `GET /api/history/<user>`에서 이력이 보이는지 확인
7. `GET /api/history/<user>/wrong-ids`가 오답 ID를 반환하는지 확인
8. `POST /api/retry-wrong`으로 오답 재출제가 되는지 확인
9. `DELETE /api/admin/purge-questions` 호출 시 `X-Admin-Key` 없으면 실패하는지 확인
10. `GET /api/ai/health?check=1`로 Gemini 네트워크 도달 여부를 확인

예시 호출:

```bash
curl "http://localhost:5000/api/categories"
curl "http://localhost:5000/api/questions/programming?limit=10&difficulty=hard&style=cert&user=tester"
curl "http://localhost:5000/api/questions/all/mixed?limit=10&difficulty=mixed&style=practical&user=tester"
curl "http://localhost:5000/api/history/tester"
curl "http://localhost:5000/api/history/tester/wrong-ids"
curl "http://localhost:5000/api/ai/health?check=1"
```

관리자 API 예시:

```bash
curl -X DELETE "http://localhost:5000/api/admin/purge-questions" \
  -H "X-Admin-Key: YOUR_ADMIN_SECRET_KEY"
```

---

## 4. Kubernetes 빠른 적용

### 4-1. 적용 파일

현재 `k8s` 폴더 기준 핵심 파일:

- `00-namespace.yaml`
- `configmap.yaml`
- `secret.yaml`
- `mysql-pvc.yaml`
- `mysql-deployment.yaml`
- `mysql-service.yaml`
- `backend-deployment.yaml`
- `backend-service.yaml`
- `frontend-deployment.yaml`
- `frontend-service.yaml`
- `network-policy.yaml`
- `gateway.yaml`

### 4-2. 적용 순서

```powershell
.\k8s\apply-ordered.ps1
```

또는:

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

### 4-3. 접속 주소

- NodePort: `http://<NODE_IP>:30080`
- Gateway API: `http://<GATEWAY_IP>:8000`

### 4-4. 확인 명령

```bash
kubectl get pods -n hc-quiz-bank
kubectl get svc -n hc-quiz-bank
kubectl get gateway -n hc-quiz-bank
kubectl logs -n hc-quiz-bank deployment/backend
kubectl logs -n hc-quiz-bank deployment/frontend
```

---

## 5. 트러블슈팅

| 증상 | 확인할 항목 |
|------|-------------|
| 프론트 502/503/504 | `BACKEND_URL`, `FRONTEND_PROXY_TIMEOUT`, `FRONTEND_PROXY_CONNECT_TIMEOUT` |
| AI 호출 실패 | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT`, `AI_REQUEST_BUDGET_SEC` |
| `NETWORK_BLOCKED` | `GET /api/ai/health?check=1` 결과 확인 |
| DB 연결 실패 | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` |
| SQLite로 붙음 | `USE_SQLITE_FALLBACK=true` 여부와 `/api/health`의 `db_mode` 확인 |
| 관리자 API 401 | `X-Admin-Key` 값과 `ADMIN_SECRET_KEY` 일치 여부 확인 |
| k8s 백엔드 미기동 | `backend-secret`, `backend-config`, `mysql-service` 연결 확인 |
| NodePort 접속 불가 | `frontend-service`의 `nodePort: 30080` 노출 여부 확인 |
| Gateway 접속 불가 | Gateway API CRD 및 `gatewayClassName: traefik` 존재 여부 확인 |

---

## 6. 배포 전 최소 점검

1. `.env`가 git 추적 대상이 아닌지 확인
2. `k8s/secret.yaml`에 실제 운영 키를 그대로 커밋하지 않았는지 확인
3. `ADMIN_SECRET_KEY`가 충분히 긴 랜덤 문자열인지 확인
4. 이미지 태그와 배포 이미지 주소가 실제 레지스트리와 일치하는지 확인
5. `GET /api/health`, `GET /api/categories`, `GET /api/ai/health`를 모두 점검
