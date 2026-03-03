# IT-Qbank (IT 문제은행)

Flask + MySQL 기반의 IT 퀴즈 서비스입니다.
카테고리별 문제를 조회하고 채점/리뷰/사용자 이력을 확인할 수 있습니다.

## 핵심 기능
- 카테고리: `network`, `infra`, `linux`
- 문제 수 선택: 5/10/15/20
- API 자동 보강: 문제가 부족하면 AI(Groq) 생성 시도 후 저장
- 중복 방지: 문제 본문 정규화 해시(`question_hash`) 기준 중복 차단
- 한글 우선: 한국어 문제만 노출
- 사용자 이력 저장: `users`, `quiz_attempts`

## 프로젝트 구조
```text
IT-Qbank/
├─ backend/
│  ├─ app.py
│  ├─ init_db.py
│  ├─ requirements.txt
│  └─ Dockerfile
├─ frontend/
│  ├─ app.py
│  ├─ templates/
│  ├─ static/css/style.css
│  └─ Dockerfile
├─ docker-compose.yml
├─ .env.example
├─ README.md
└─ QuickStartGuide.md
```

## 환경 변수
`.env.example`를 복사해 `.env`를 생성하세요.

주요 변수:
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_API_URL`, `GROQ_TIMEOUT`
- `BACKEND_URL`
- `FLASK_DEBUG`

## 로컬 실행
### 1) DB 초기화
```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
pip install -r requirements.txt
python init_db.py
```

### 2) 백엔드 실행
```bash
cd backend
venv\Scripts\activate
python app.py
# http://localhost:5000
```

### 3) 프론트 실행
```bash
cd frontend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
set BACKEND_URL=http://localhost:5000
python app.py
# http://localhost:8080
```

## Docker Compose 실행
```bash
docker compose up -d --build
```

접속:
- 프론트: `http://localhost:8080`
- 백엔드 헬스: `http://localhost:5000/api/health`

종료:
```bash
docker compose down
```

## 주요 API
- `GET /api/health`
- `GET /api/categories`
- `GET /api/questions/<category>?limit=10&shuffle=1&source=auto`
- `GET /api/questions/<category>/all`
- `POST /api/submit`
- `GET /api/history/<user_name>?limit=20`
- `GET /api/ai/health`
- `POST /api/ai/questions`

## 인코딩(한글 깨짐) 체크
DB 저장은 `utf8mb4` 기준입니다. CLI에서 한글이 깨지면 클라이언트 인코딩 문제입니다.

MySQL CLI 권장:
```bash
chcp 65001
mysql -h localhost -P 3306 -u quizuser -p --default-character-set=utf8mb4 quizdb
```

접속 후:
```sql
SET NAMES utf8mb4;
SHOW VARIABLES LIKE 'character_set_%';
```

## 최근 반영 사항
- 카테고리 버튼의 DB 문제 수 텍스트 제거
- 진행 텍스트와 퍼센트 간격 조정
- `(...번 변형)` 꼬리 제거 및 중복 차단 강화
- 문제 조회 시 해시 기준 중복 제거
