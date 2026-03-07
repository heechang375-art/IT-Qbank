# QuickStartGuide

## 1. 가장 빠른 실행 (Docker)

**Windows:**
```bash
cd IT-Qbank
copy .env.example .env
# .env에서 GEMINI_API_KEY 값 입력

docker compose up -d --build
```

**Linux/macOS:**
```bash
cd IT-Qbank
cp .env.example .env
# .env에서 GEMINI_API_KEY 값 입력

docker compose up -d --build
```

확인:
- 프론트: `http://localhost:8080`
- 백엔드: `http://localhost:5000/api/health`

중지:
```bash
docker compose down
```

---

## 2. 로컬 실행 (컨테이너 없이)

### 2-1. MySQL 준비
```sql
CREATE DATABASE quizdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'quizuser'@'%' IDENTIFIED BY 'quizpassword';
GRANT ALL PRIVILEGES ON quizdb.* TO 'quizuser'@'%';
FLUSH PRIVILEGES;
```

### 2-2. 백엔드 (Windows)
```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

set DB_HOST=localhost
set DB_PORT=3306
set DB_NAME=quizdb
set DB_USER=quizuser
set DB_PASSWORD=quizpassword
set GEMINI_API_KEY=YOUR_GEMINI_KEY
set GEMINI_MODEL=gemini-2.5-flash
set GEMINI_TIMEOUT=120
set AI_REQUEST_BUDGET_SEC=200

python init_db.py
python app.py
```

### 2-2. 백엔드 (Linux/macOS)
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
export GEMINI_API_KEY=YOUR_GEMINI_KEY
export GEMINI_MODEL=gemini-2.5-flash
export GEMINI_TIMEOUT=120
export AI_REQUEST_BUDGET_SEC=200

python init_db.py
python app.py
```

### 2-3. 프론트 (Windows)
```bash
cd frontend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

set BACKEND_URL=http://localhost:5000
set FRONTEND_PROXY_TIMEOUT=300
python app.py
```

### 2-3. 프론트 (Linux/macOS)
```bash
cd frontend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export BACKEND_URL=http://localhost:5000
export FRONTEND_PROXY_TIMEOUT=300
python app.py
```

---

## 3. 동작 확인 체크리스트
1. `GET /api/health`가 200인지 확인
2. 문제 선택 수(5/10/15/20)와 실제 출제 수가 동일한지 확인
3. 헤더의 `문제 x / y`와 `%` 진행률이 정상 표시되는지 확인
4. 난이도 선택(쉬움/혼합/어려움)이 정상 동작하는지 확인
5. `source=ai` 호출 시 AI 생성 + DB 보강이 정상 동작하는지 확인
6. 이력에서 특정 시도를 눌렀을 때 리뷰 화면으로 이동되는지 확인
7. 이력 페이지에서 누적 오답 N문제 출제가 정상 동작하는지 확인

예시 호출:
```bash
# 기본 출제
curl "http://localhost:5000/api/questions/linux?limit=10&shuffle=1&source=ai&user=tester"

# 난이도 지정 출제
curl "http://localhost:5000/api/questions/network?limit=10&difficulty=hard&source=ai&user=tester"

# 누적 오답 ID 조회
curl "http://localhost:5000/api/history/tester/wrong-ids"
```

---

## 4. DB 확인 (한글 깨짐 대응 포함)

### 4-1. CLI 접속 권장 방식
```bash
chcp 65001
mysql -h localhost -P 3306 -u quizuser -p --default-character-set=utf8mb4 quizdb
```

```sql
SET NAMES utf8mb4;
SHOW VARIABLES LIKE 'character_set_%';
SHOW VARIABLES LIKE 'collation_%';
```

### 4-2. 문제/이력 확인
```sql
SELECT id, category, LEFT(question, 80) AS q, created_at
FROM questions
ORDER BY id DESC
LIMIT 20;

SELECT attempt_id, user_id, category, total, correct, wrong, score_percent, created_at
FROM quiz_attempts
ORDER BY attempt_id DESC
LIMIT 20;
```

---

## 5. 문제 해결

| 증상 | 확인 사항 |
|------|----------|
| AI 403/404 | `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-2.5-flash` 확인 |
| AI 429 | 요청 한도 초과 — 잠시 후 재시도 |
| AI 503 | Gemini 서버 과부하 — 잠시 후 재시도 |
| AI 타임아웃 | `GEMINI_TIMEOUT` 값 증가 (기본 120초), 문제 수 줄이기 |
| AI NETWORK_BLOCKED | `GET /api/ai/health?check=1`으로 TCP 443 egress 차단 여부 확인 (Kubernetes NetworkPolicy 점검) |
| 한글 깨짐 | DB 저장이 아닌 터미널 문자셋 문제인지 먼저 확인 (`chcp 65001`) |
| 출제 수 부족 | backend 로그에서 AI 응답 파싱 오류/타임아웃 확인 |
| SQLite 사용 중 | `USE_SQLITE_FALLBACK=true` 상태에서 MySQL 연결 불가 시 자동 전환 — `GET /api/health` 응답의 `db_mode` 필드로 확인 |
| 프론트 502 | `FRONTEND_PROXY_TIMEOUT` 값 확인 (기본 300초) |

---

## 6. 배포 전 최소 점검
1. `.env`가 git 추적 제외인지 확인
2. `python -m py_compile backend/app.py backend/init_db.py frontend/app.py`
3. `GET /api/history/<user>/<attempt_id>` 응답이 정상인지 확인
4. `GET /api/history/<user>/wrong-ids` 응답이 정상인지 확인
5. `git status`로 문서/이미지 변경 파일 확인

---

## 7. Kubernetes 배포

### 7-1. 공통 준비

예시 파일 복사 후 `REPLACE_*` 값 수정:
```bash
copy k8s\examples\configmap.example.yaml k8s\configmap.yaml
copy k8s\examples\secret.example.yaml k8s\secret.yaml
```

공통 리소스 적용 (방법 A/B 모두 동일):
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/hc-rq.yaml
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
```

> **이미지 재빌드가 필요한 경우** (코드/HTML/CSS 변경 시):
> ```bash
> docker build -t <registry>/it-qbank-backend:latest ./backend
> docker build -t <registry>/it-qbank-frontend:latest ./frontend
> docker push <registry>/it-qbank-backend:latest
> docker push <registry>/it-qbank-frontend:latest
> kubectl rollout restart deployment/backend-deployment -n hc-quiz-bank
> kubectl rollout restart deployment/frontend-deployment -n hc-quiz-bank
> ```

---

### 7-2. 방법 A: NodePort만 사용 (Gateway 없음, 테스트용)

Rancher Desktop 등 로컬 환경에서 `gateway.yaml` 없이 테스트하는 방법입니다.

**추가 작업 없음** — 위 공통 리소스만 적용하면 됩니다.

트래픽 흐름:
```
브라우저 → NodeIP:30080 (frontend NodePort)
           └─ /api/* → 프론트엔드 프록시 → backend-service:5000
```

접속 확인:
```bash
# 노드 IP 확인 (Rancher Desktop은 보통 127.0.0.1)
kubectl get nodes -o wide

# 서비스 확인
kubectl get svc -n hc-quiz-bank
```

접속 URL:
```
http://<노드IP>:30080
```

동작 확인:
```bash
curl http://<노드IP>:30080/api/health
curl http://<노드IP>:30080/api/categories
```

> **주의:** 이 방법에서는 `/api/*` 요청이 프론트엔드 컨테이너를 경유합니다.  
> `frontend-config` ConfigMap의 `BACKEND_URL=http://backend-service:5000`이 올바르게 설정되어 있어야 합니다.

---

### 7-3. 방법 B: Gateway API 사용 (운영 권장)

클러스터에 Gateway API CRD와 `traefik` gatewayClassName이 준비된 환경에서 사용합니다.

**공통 리소스 적용 후 gateway.yaml 추가 적용:**
```bash
kubectl apply -f k8s/gateway.yaml
```

트래픽 흐름:
```
브라우저 → Gateway:8000
  ├─ /api/*  → backend-service:5000   (Gateway가 직접 라우팅)
  └─ /       → frontend-service:8080
```

Gateway 상태 확인:
```bash
kubectl get gateway -n hc-quiz-bank
kubectl get httproute -n hc-quiz-bank
kubectl get svc -n hc-quiz-bank
```

Gateway IP 확인 및 접속:
```bash
kubectl get gateway quiz-gateway -n hc-quiz-bank -o jsonpath='{.status.addresses}'
```

접속 URL:
```
http://<Gateway-IP>:8000
```

동작 확인:
```bash
curl http://<Gateway-IP>:8000/api/health
curl http://<Gateway-IP>:8000/api/categories
curl http://<Gateway-IP>:8000/
```

> **gatewayClassName 변경이 필요한 경우:**  
> `k8s/gateway.yaml`에서 `gatewayClassName: traefik`을 환경에 맞게 변경하세요.  
> 예: `nginx`, `istio`, `cilium`, `kong` 등

> **Gateway API CRD 미설치 시:**  
> `kubectl apply -f k8s/gateway.yaml` 실패 → 방법 A(NodePort)로만 사용하거나  
> 클러스터 관리자에게 Gateway API CRD 설치를 요청하세요.

---

### 7-4. 방법 비교

| 항목 | 방법 A (NodePort) | 방법 B (Gateway API) |
|------|-------------------|----------------------|
| 접속 포트 | `30080` | `8000` |
| API 라우팅 | 프론트엔드 프록시 경유 | Gateway 직접 라우팅 |
| Gateway CRD 필요 | 불필요 | 필요 |
| 적합한 환경 | 로컬/테스트 | 운영/스테이징 |
| gateway.yaml 적용 | 불필요 | 필요 |

---

## 8. Kubernetes 파드 상태 확인

```bash
kubectl get pods -n hc-quiz-bank
kubectl logs -n hc-quiz-bank deployment/backend-deployment
kubectl logs -n hc-quiz-bank deployment/frontend-deployment
```

AI 네트워크 연결 확인 (backend에서 Gemini API 접근 가능 여부):
```bash
# NodePort 환경
curl "http://<노드IP>:30080/api/ai/health?check=1"

# Gateway 환경
curl "http://<Gateway-IP>:8000/api/ai/health?check=1"
```