# QuickStartGuide

## 1. 가장 빠른 실행 (Docker)
```bash
cd IT-Qbank
copy .env.example .env
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

## 2. 로컬 실행 (컨테이너 없이)

### 2-1. MySQL 준비
```sql
CREATE DATABASE quizdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'quizuser'@'%' IDENTIFIED BY 'quizpassword';
GRANT ALL PRIVILEGES ON quizdb.* TO 'quizuser'@'%';
FLUSH PRIVILEGES;
```

### 2-2. 백엔드
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

python init_db.py
python app.py
```

### 2-3. 프론트
```bash
cd frontend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

set BACKEND_URL=http://localhost:5000
python app.py
```

## 3. 동작 확인 체크리스트
1. `GET /api/health`가 200인지 확인
2. 문제 선택 수(5/10/15/20)와 실제 출제 수가 동일한지 확인
3. 진행 바의 `문제 x / y`와 `%`가 겹치지 않는지 확인
4. `source=ai` 호출 시 AI 생성 + DB 보강이 정상 동작하는지 확인
5. 이력에서 특정 시도를 눌렀을 때 리뷰 화면으로 이동되는지 확인

예시 호출:
```bash
curl "http://localhost:5000/api/questions/linux?limit=10&shuffle=1&source=ai&user=tester"
```

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

## 5. 문제 해결
- AI 403/404: `GEMINI_API_KEY`, `GEMINI_API_URL`, 모델명(`GEMINI_MODEL`) 확인
- 한글 깨짐: DB 저장이 아닌 터미널 문자셋 문제인지 먼저 확인
- 출제 수 부족: backend 로그에서 AI 응답 파싱 오류/타임아웃 확인

## 6. 배포 전 최소 점검
1. `.env`가 git 추적 제외인지 확인
2. `python -m py_compile backend/app.py backend/init_db.py frontend/app.py`
3. `GET /api/history/<user>/<attempt_id>` 응답이 정상인지 확인
4. `git status`로 문서/이미지 변경 파일 확인

## 7. Kubernetes 배포 (hc 네임스페이스 기준)
기본값:
- Namespace: `hc-quiz-bank`
- Gateway Host: `quiz-bank.com`

예시 파일 복사:
```bash
copy k8s\examples\configmap.example.yaml k8s\configmap.yaml
copy k8s\examples\secret.example.yaml k8s\secret.yaml
```

적용 순서:
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
kubectl apply -f k8s/gateway.yaml
```
