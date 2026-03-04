# IT-Qbank (IT 문제은행)

Flask + MySQL 기반의 IT 퀴즈 서비스입니다.  
카테고리별 객관식 문제를 AI(Gemini) + DB 하이브리드 방식으로 출제하고, 사용자별 이력과 상세 리뷰를 제공합니다.

## 핵심 기능
- 카테고리: `network`, `infra`, `linux`
- 문제 수 선택: `5 / 10 / 15 / 20`
- AI 우선 출제 + DB 보강: 부족 시 자동 생성/저장
- 사용자 최근 풀이 문제 해시(`question_hash`) 기반 중복 회피
- 한글 우선 문제 생성/표시
- 사용자별 시도 이력 저장 + 이력 항목별 다시보기

## 페이지 예시
- 메인(설정): ![메인 페이지](docs/images/page-index.svg)
- 퀴즈 진행: ![퀴즈 페이지](docs/images/page-quiz.svg)
- 결과: ![결과 페이지](docs/images/page-result.svg)
- 오답/리뷰: ![리뷰 페이지](docs/images/page-review.svg)
- 이력: ![이력 페이지](docs/images/page-history.svg)

## 프로젝트 구조
```text
IT-Qbank/
├─ backend/
├─ frontend/
├─ db/
├─ docs/images/
├─ docker-compose.yml
├─ .env.example
├─ README.md
└─ QuickStartGuide.md
```

## Kubernetes 배포 다이어그램
```mermaid
flowchart TD
    U[Internet / Browser] --> G[Gateway or Ingress]
    G -->|/api/*| BS[backend-svc:5000]
    G -->|/ , /quiz/* , /result/* , /history/*| FS[frontend-svc:8080]

    BS --> BD[backend Deployment]
    FS --> FD[frontend Deployment]

    BD --> MS[mysql-svc:3306]
    MS --> DB[(MySQL StatefulSet/PVC)]
```

## Kubernetes 기본값
- Namespace: `hc`
- Gateway Host: `quiz-bank.com`
- 프론트 경로: `/`, `/quiz/*`, `/result/*`, `/history/*`
- 백엔드 API 경로: `/api/*`

## Kubernetes 설정 파일(예시)
- ConfigMap 예시: [configmap.example.yaml](c:/Users/campus3S026/IT-Qbank/k8s/examples/configmap.example.yaml)
- Secret 예시: [secret.example.yaml](c:/Users/campus3S026/IT-Qbank/k8s/examples/secret.example.yaml)
- 실제 적용 전 `REPLACE_*` 값을 반드시 변경하세요.

## API Path 동작 방식
- 경로 매칭은 이미지가 자동 판별하는 것이 아니라, `Gateway/Ingress` 규칙이 판단합니다.
- 브라우저가 페이지를 열 때는 주로 `frontend-svc`로 갑니다.
- 문제 조회/채점/이력 조회처럼 데이터가 필요하면 프론트가 `/api/*`를 호출하고, 이 요청은 `backend-svc`로 라우팅됩니다.
- 즉, "문제를 가져와야 하면 API로 간다"가 맞습니다.

## 환경 변수
`.env.example`를 복사해 `.env`를 생성하세요.

주요 변수:
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_API_URL`, `GEMINI_TIMEOUT`
- `USE_SQLITE_FALLBACK`
- `BACKEND_URL`, `FRONTEND_PROXY_TIMEOUT`
- `FLASK_DEBUG`

## 실행
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
- `GET /api/questions/<category>?limit=10&shuffle=1&source=ai&user=<name>`
- `GET /api/questions/<category>/all`
- `POST /api/submit`
- `GET /api/history/<user_name>?limit=20`
- `GET /api/history/<user_name>/<attempt_id>`
- `GET /api/ai/health`
- `POST /api/ai/questions`

## DB 확인 (한글 깨짐 대응)
```bash
chcp 65001
mysql -h localhost -P 3306 -u quizuser -p --default-character-set=utf8mb4 quizdb
```

```sql
SET NAMES utf8mb4;
SELECT id, category, LEFT(question, 80) AS q, created_at
FROM questions
ORDER BY id DESC
LIMIT 10;
```

## 최근 반영 사항
- 사용자별 최근 풀이 문제 해시 제외 로직 적용
- 이력 페이지에서 선택한 시도를 리뷰 화면으로 재조회 가능
- `created_at` 컬럼 자동 보정 로직 추가
- KST 기준 시각 저장/응답(`created_at_kst`) 정리
