# QuickStartGuide

## 1. 가장 빠른 실행 (Docker)
```bash
cd IT-Qbank
copy .env.example .env
# .env에서 GROQ_API_KEY 등 필요 값 설정

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
set GROQ_API_KEY=YOUR_GROQ_KEY

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
2. 메인 화면 카테고리에 `N문제` 표기가 없는지 확인
3. 퀴즈 진행 바에서 `문제 x / y`와 `%`가 겹치지 않는지 확인
4. `source=auto` 호출 시 부족 문제가 자동 보강되는지 확인
5. 같은 질문이 반복(변형 꼬리) 노출되지 않는지 확인

예시 호출:
```bash
curl "http://localhost:5000/api/questions/network?limit=20&shuffle=1&source=auto"
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
SELECT id, category, question FROM questions ORDER BY id DESC LIMIT 20;
```

### 4-2. 사용자 이력 확인
```sql
SELECT * FROM users ORDER BY id DESC;
SELECT attempt_id, user_id, category, total, correct, wrong, score_percent, created_at
FROM quiz_attempts
ORDER BY attempt_id DESC
LIMIT 20;
```

## 5. 문제 해결
- 카테고리 로드 5xx: backend 프로세스/포트/DB 연결 확인
- 한글 깨짐: DB가 아니라 클라이언트 문자셋 문제인지 먼저 확인
- 문제 중복: backend 재시작 후 `/api/questions/<category>?source=auto` 재호출

## 6. 배포 전 최소 점검
1. `.env`가 git 추적 제외인지 확인
2. `python -m py_compile backend/app.py backend/init_db.py frontend/app.py`
3. `git status`로 변경 파일이 문서 2개인지 확인
