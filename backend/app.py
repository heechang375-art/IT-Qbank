"""
Network/Infrastructure/Linux Quiz Bank - Backend API
"""

import hashlib
import json
import os
import random
import re
import socket
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

load_dotenv()

# ──────────────────────────────────────────────────────────
# Flask 앱 초기화 및 CORS 설정
# 모든 Origin에 대해 CORS 허용 (Gateway/Ingress 뒤에서 동작하므로 프론트 도메인 제한 없음)
# ──────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ──────────────────────────────────────────────────────────
# 환경 변수에서 DB / Gemini AI 설정값 로드
# .env 또는 Kubernetes ConfigMap/Secret에서 주입됨
# ──────────────────────────────────────────────────────────
# DB config
DB_USER = os.getenv("DB_USER", "quizuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "quizpassword")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "quizdb")
USE_SQLITE_FALLBACK = os.getenv("USE_SQLITE_FALLBACK", "true").lower() == "true"

# Gemini(AI) config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")
GEMINI_API_URL = os.getenv("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/models")
GEMINI_MODEL_CANDIDATES = os.getenv(
    "GEMINI_MODEL_CANDIDATES",
    "gemini-1.5-flash-latest,gemini-1.5-flash",
)
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "8"))
AI_REQUEST_BUDGET_SEC = int(os.getenv("AI_REQUEST_BUDGET_SEC", "20"))

# Maintenance config
PURGE_DEFAULT_ON_BOOT = os.getenv("PURGE_DEFAULT_ON_BOOT", "true").lower() == "true"
PURGE_SHORT_ON_BOOT = os.getenv("PURGE_SHORT_ON_BOOT", "true").lower() == "true"

VALID_CATEGORIES = {"network", "infra", "linux"}
CATEGORY_LABEL = {
    "network": "\ub124\ud2b8\uc6cc\ud06c",
    "infra": "\uc778\ud504\ub77c",
    "linux": "\ub9ac\ub205\uc2a4",
}

KST = timezone(timedelta(hours=9))
KO_RE = re.compile(r"[\uac00-\ud7a3]")


def now_kst_naive():
    return datetime.now(KST).replace(tzinfo=None)


def is_korean_text(text):
    return bool(KO_RE.search(str(text or "")))


def _is_tcp_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# ──────────────────────────────────────────────────────────
# DB 연결 분기
# MySQL 포트가 열려있으면 MySQL 사용, 아니면 SQLite fallback
# Kubernetes 환경에서는 'db' 호스트명이 없을 경우 localhost로 fallback
# ──────────────────────────────────────────────────────────
if DB_HOST == "db":
    try:
        socket.gethostbyname("db")
    except socket.gaierror:
        DB_HOST = "localhost"

if _is_tcp_open(DB_HOST, DB_PORT):
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
    )
    ACTIVE_DB = "mysql"
elif USE_SQLITE_FALLBACK:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///quiz_local.db"
    ACTIVE_DB = "sqlite_fallback"
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
    )
    ACTIVE_DB = "mysql_unreachable"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# [MySQL 문자셋 설정] DB 연결 시마다 SET NAMES utf8mb4 실행 - 한글 깨짐 방지
@event.listens_for(Engine, "connect")
def set_mysql_utf8mb4(dbapi_connection, _):
    try:
        cur = dbapi_connection.cursor()
        cur.execute("SET NAMES utf8mb4")
        cur.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# ORM 모델 정의
# Question: 문제 저장소 (카테고리/보기/정답/해설/해시)
# User: 사용자 (이름 기준 식별)
# QuizAttempt: 시도 이력 (채점 결과 + 답안 JSON 저장)
# ──────────────────────────────────────────────────────────
class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    choice_a = db.Column(db.Text, nullable=False)
    choice_b = db.Column(db.Text, nullable=False)
    choice_c = db.Column(db.Text, nullable=False)
    choice_d = db.Column(db.Text, nullable=False)
    answer = db.Column(db.String(1), nullable=False)
    explanation = db.Column(db.Text, nullable=True)
    question_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=now_kst_naive, nullable=False, index=True)

    def to_dict(self, hide_answer=True):
        data = {
            "id": self.id,
            "category": self.category,
            "question": self.question,
            "choices": {"A": self.choice_a, "B": self.choice_b, "C": self.choice_c, "D": self.choice_d},
        }
        if not hide_answer:
            data["answer"] = self.answer
            data["explanation"] = self.explanation
        return data


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=now_kst_naive, nullable=False)


class QuizAttempt(db.Model):
    __tablename__ = "quiz_attempts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False)
    total = db.Column(db.Integer, nullable=False)
    correct = db.Column(db.Integer, nullable=False)
    wrong = db.Column(db.Integer, nullable=False)
    score_percent = db.Column(db.Float, nullable=False)
    answers_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=now_kst_naive, nullable=False)


# [텍스트 정규화] 불필요한 공백 제거 (연속 공백 → 단일 공백)
def _normalize_text(value):
    return " ".join(str(value or "").split())


# [문제 텍스트 정제] "(변형 1)", "(2)" 등 변형 표기 제거 정규식
VARIANT_SUFFIX_RE = re.compile(r"\s*\([^)]*\d+[^)]*\)\s*$")
TRAILING_BRACKET_RE = re.compile(r"\(\s*\d+\s*\)\s*$")


# [문제 텍스트 정제] 변형 접미사 제거 후 정규화 - 중복 해시 판별용
def _sanitize_question_text(value):
    text = _normalize_text(value)
    text = VARIANT_SUFFIX_RE.sub("", text)
    text = TRAILING_BRACKET_RE.sub("", text)
    return _normalize_text(text)


# ──────────────────────────────────────────────────────────
# 문제 중복 방지: SHA-256 해시로 동일 문제 판별
# 카테고리+문제 텍스트 정규화 후 해시 생성
# ──────────────────────────────────────────────────────────
def _question_hash(category, question):
    base = f"{_normalize_text(category).lower()}|{_sanitize_question_text(question).lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _cleanup_legacy_variant_questions():
    rows = Question.query.order_by(Question.id.asc()).all()
    keep_by_hash = {}
    delete_ids = []

    for row in rows:
        cleaned_question = _sanitize_question_text(row.question)
        new_hash = _question_hash(row.category, cleaned_question)

        if new_hash in keep_by_hash:
            delete_ids.append(row.id)
            continue

        keep_by_hash[new_hash] = row.id
        if row.question != cleaned_question:
            row.question = cleaned_question
        if row.question_hash != new_hash:
            row.question_hash = new_hash

    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
    db.session.commit()


def _text_score(value):
    text = _normalize_text(value)
    if not text:
        return 0
    # Ignore whitespace and common separators for rough quality scoring.
    return len(re.sub(r"[\s\-\_\.\,\(\)\[\]\:\/]+", "", text))


# [품질 필터] 문제/보기/해설 길이 기준으로 저품질 문제 판별 (너무 짧은 문항 제외)
def _is_low_quality_question(row):
    q_len = _text_score(row.question)
    e_len = _text_score(row.explanation)
    c_lens = [_text_score(row.choice_a), _text_score(row.choice_b), _text_score(row.choice_c), _text_score(row.choice_d)]
    short_choices = sum(1 for x in c_lens if x <= 5)
    avg_choice_len = sum(c_lens) / max(1, len(c_lens))

    # Heuristic for very short one-liner items.
    if e_len <= 8 and q_len <= 22:
        return True
    if q_len <= 22 and short_choices >= 3 and avg_choice_len <= 9:
        return True
    return False


# [저품질 문제 일괄 삭제] DB에서 품질 기준 미달 문항 전체 제거 (부팅 시 자동 실행)
def _purge_low_quality_questions():
    rows = Question.query.all()
    delete_ids = [r.id for r in rows if _is_low_quality_question(r)]
    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
        db.session.commit()
    return len(delete_ids)


# [스키마 마이그레이션] questions 테이블에 created_at 컬럼이 없으면 자동 추가 (MySQL/SQLite 대응)
def _ensure_questions_created_at_column():
    driver = str(db.engine.url.drivername)

    if "mysql" in driver:
        exists_sql = db.text(
            """
            SELECT COUNT(*) AS cnt
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :schema
              AND TABLE_NAME = 'questions'
              AND COLUMN_NAME = 'created_at'
            """
        )
        exists = db.session.execute(exists_sql, {"schema": DB_NAME}).scalar() or 0
        if int(exists) == 0:
            try:
                db.session.execute(
                    db.text(
                        "ALTER TABLE questions "
                        "ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    )
                )
                db.session.commit()
            except Exception as e:
                # Concurrent/legacy schema paths can hit duplicate-column race.
                if "Duplicate column name 'created_at'" in str(e) or "(1060" in str(e):
                    db.session.rollback()
                else:
                    raise
        return

    if "sqlite" in driver:
        rows = db.session.execute(db.text("PRAGMA table_info(questions)")).fetchall()
        names = {str(r[1]) for r in rows}
        if "created_at" not in names:
            db.session.execute(
                db.text("ALTER TABLE questions ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
            )
            db.session.commit()


# [기본 문제 해시 목록] 초기 시드/하드코딩 문제의 해시값 생성 - 부팅 시 자동 삭제 대상 식별용
def _default_bank_hashes():
    base_by_category = {
        "network": [
            "OSI 怨꾩링 以??쇱슦?낆쓣 ?대떦?섎뒗 怨꾩링??",
            "HTTPS 湲곕낯 ?ы듃??",
            "?쒕툕??留덉뒪??/24?먯꽌 ?ъ슜 媛?ν븳 ?몄뒪???섎뒗?",
            "DNS 湲곕낯 ?ы듃??",
            "UDP???뱀쭠?쇰줈 ?녹? 寃껋??",
            "?쇱슦?곌? 二쇰줈 ?숈옉?섎뒗 OSI 怨꾩링??",
        ],
        "infra": [
            "Kubernetes??理쒖냼 諛고룷 ?⑥쐞??",
            "濡쒕뱶諛몃윴?쒖쓽 二쇱슂 ??븷??",
            "而⑦뀒?대꼫? VM??李⑥씠濡?留욌뒗 寃껋??",
            "Kubernetes Ingress????븷??",
            "Prometheus??二??⑸룄??",
            "Terraform?????踰붿＜??",
        ],
        "linux": [
            "?ㅽ뻾 以묒씤 ?꾨줈?몄뒪瑜??뺤씤?섎뒗 紐낅졊??",
            "chmod 755 沅뚰븳 ?ㅻ챸?쇰줈 留욌뒗 寃껋??",
            "?붿뒪???ъ슜?됱쓣 蹂닿린 醫뗭? ?뺥깭濡??뺤씤?섎뒗 紐낅졊??",
            "?ㅼ떆媛?濡쒓렇 異붿쟻 紐낅졊??",
            "?ы듃 由ъ뒪???곹깭 ?뺤씤??二쇰줈 ?곕뒗 紐낅졊??",
            "沅뚰븳 蹂寃?紐낅졊??",
            "SSH 怨듦컻??濡쒓렇?몄뿉 ?ъ슜?섎뒗 ?뚯씪??",
        ],
    }
    prefixes = ["?먭? 臾몄젣:", "?ㅻТ 臾몄젣:", "?묒슜 臾몄젣:", "湲곗큹 臾몄젣:", "?ы솕 臾몄젣:", "媛쒕뀗 臾몄젣:"]

    out = set()
    for category, questions in base_by_category.items():
        for q in questions:
            out.add(_question_hash(category, q))
            for p in prefixes:
                out.add(_question_hash(category, f"{p} {q}"))
    return out


# [기본 문제 일괄 삭제] 하드코딩 시드 문제를 해시 기반으로 DB에서 제거 (PURGE_DEFAULT_ON_BOOT=true 시 실행)
def _purge_default_questions():
    purge_hashes = _default_bank_hashes()
    rows = Question.query.filter(Question.question_hash.in_(list(purge_hashes))).all()
    delete_ids = [r.id for r in rows]
    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
        db.session.commit()
    return len(delete_ids)


# [DB 스키마 초기화] 앱 시작 시 테이블 생성, 마이그레이션, 중복/저품질 문제 정리까지 순서대로 실행
def _ensure_schema():
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            # Multi-worker bootstrap race (especially sqlite fallback) can hit table-already-exists.
            if "already exists" not in str(e).lower():
                raise
        _ensure_questions_created_at_column()
        _cleanup_legacy_variant_questions()
        purged = 0
        if PURGE_DEFAULT_ON_BOOT:
            purged = _purge_default_questions()
        if PURGE_SHORT_ON_BOOT:
            _purge_low_quality_questions()
        return purged


# [JSON 추출] AI 응답에 포함된 마크다운 코드펜스(```json) 제거 후 순수 JSON 텍스트 반환
def _extract_json_text(value):
    text = _normalize_text(value)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


# [안전한 JSON 파싱] 표준 파싱 실패 시 중괄호 경계 탐색 / trailing comma 정규화로 재시도
def _safe_parse_questions_json(raw_text):
    text = _extract_json_text(raw_text)
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to extract first balanced JSON object boundaries.
    start = text.find("{")
    if start != -1:
        depth = 0
        end = -1
        in_string = False
        escaped = False
        for idx, ch in enumerate(text[start:], start=start):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break

        if end != -1:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except Exception:
                pass

    # Last resort: normalize common trailing comma issue.
    normalized = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(normalized)


# [로컬 폴백 비활성화] 하드코딩 문제 제거 정책 - AI API 기반 출제만 사용 (항상 빈 리스트 반환)
def _build_local_fallback_questions(category, count, start_index=1):
    """하드코딩 fallback 제거 - AI API 문제 출제만 사용."""
    return []


# [모델명 정규화] "models/gemini-xxx:generateContent" 형식에서 순수 모델명 추출
def _normalize_model_name(model_name):
    m = _normalize_text(model_name)
    if not m:
        return ""
    if m.endswith(":generateContent"):
        m = m[: -len(":generateContent")]
    if "/" in m:
        m = m.split("/")[-1]
    return m


# [Gemini Base URL 정규화] 환경변수 URL 끝 슬래시 제거 및 '/models' 경로 자동 보완
def _normalized_gemini_base_url():
    base_url = _normalize_text(GEMINI_API_URL).rstrip("/")
    if not base_url:
        return "https://generativelanguage.googleapis.com/v1beta/models"
    if "/models" not in base_url:
        base_url = f"{base_url}/models"
    return base_url


# ──────────────────────────────────────────────────────────
# Gemini API 호출: 카테고리별 한국어 객관식 문제 생성
# NetworkPolicy로 TCP 443 egress가 차단된 경우 즉시 오류 반환
# 모델 후보 순서대로 시도 (GEMINI_MODEL → GEMINI_MODEL_CANDIDATES)
# maxOutputTokens=16384 로 20문제도 잘림 없이 수신 가능
# ──────────────────────────────────────────────────────────
def _call_gemini_generate_questions(category, count, difficulty):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    # 외부 HTTPS 연결 가능 여부 사전 확인 (NetworkPolicy 차단 감지용)
    # connect timeout을 짧게(3초) 잡아서 차단된 경우 즉시 명확한 에러를 냄
    if not _is_tcp_open("generativelanguage.googleapis.com", 443, timeout=3.0):
        raise RuntimeError(
            "NETWORK_BLOCKED: Cannot reach generativelanguage.googleapis.com:443. "
            "Check NetworkPolicy backend-egress-mysql-and-https allows TCP 443 egress."
        )

    prompt = (
        f"Category: {CATEGORY_LABEL.get(category, category)} ({category})\n"
        f"Difficulty: {difficulty}\n"
        f"QuestionCount: {count}\n"
        "Requirements:\n"
        "- Generate Korean multiple-choice questions only.\n"
        "- Output JSON only with key 'questions'.\n"
        "- Each item must include question, choices(A/B/C/D), answer, explanation.\n"
        "- answer must be one of A/B/C/D.\n"
        "- No duplicate questions.\n"
        "- Mix choice style: not only one-word choices.\n"
        "- At least half of questions should include sentence-style choices.\n"
        "- Make questions look like real certification exams (Korean practical exam style).\n"
        "- Prefer scenario-based stems and practical operation/troubleshooting context.\n"
        "- Include plausible distractors that are technically close, not obvious wrong answers.\n"
        "- Keep exactly one best answer.\n"
        "- Do not include labels like '(variation n)', 'example', 'sample' in question text.\n"
        "- Keep explanation concise but exam-oriented: why correct and why other choices are wrong.\n"
    )

    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": "Return exactly one JSON object. Schema: {\"questions\":[{\"question\":\"...\",\"choices\":{\"A\":\"...\",\"B\":\"...\",\"C\":\"...\",\"D\":\"...\"},\"answer\":\"A\",\"explanation\":\"...\"}]}. All output text must be Korean. Use realistic exam tone and avoid any markdown/code fences."
                }
            ]
        },
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
            "maxOutputTokens": 16384,
        },
    }

    model_candidates = []
    configured_candidates = [x.strip() for x in GEMINI_MODEL_CANDIDATES.split(",") if x.strip()]
    for m in [GEMINI_MODEL] + configured_candidates:
        m = _normalize_model_name(m)
        if m and m not in model_candidates:
            model_candidates.append(m)

    body = None
    errors = []
    base_url = _normalized_gemini_base_url()
    for model_name in model_candidates:
        url = f"{base_url}/{model_name}:generateContent?{urllib.parse.urlencode({'key': GEMINI_API_KEY})}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = ""
            errors.append(f"HTTP {e.code} model={model_name} {detail[:180]}")
            if e.code in (401, 403, 429):
                # Auth/quota errors are not improved by trying many more models.
                break
        except Exception as e:
            errors.append(f"model={model_name} {e}")

    if body is None:
        if any("429" in e for e in errors):
            raise RuntimeError(next(e for e in errors if "429" in e))
        if any("403" in e for e in errors):
            raise RuntimeError(next(e for e in errors if "403" in e))
        raise RuntimeError(errors[-1] if errors else "Gemini request failed")

    candidates = body.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {body.get('promptFeedback', {})}")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise RuntimeError("Gemini returned empty content")

    raw_text = parts[0].get("text", "")
    try:
        parsed = _safe_parse_questions_json(raw_text)
    except Exception as e:
        # Retry once with a smaller request when JSON is malformed.
        if count > 3:
            retry_rows = _call_gemini_generate_questions(category, max(3, count // 2), difficulty)
            if retry_rows:
                return retry_rows
        raise RuntimeError(str(e))

    out = []
    for item in parsed.get("questions", []):
        choices = item.get("choices", {})
        answer = str(item.get("answer", "")).upper()
        q_text = _sanitize_question_text(item.get("question", ""))
        if answer not in {"A", "B", "C", "D"}:
            continue
        if not is_korean_text(q_text):
            continue
        if not all(k in choices and _normalize_text(choices[k]) for k in ("A", "B", "C", "D")):
            continue
        out.append(
            {
                "category": category,
                "question": q_text,
                "choices": {
                    "A": _normalize_text(choices["A"]),
                    "B": _normalize_text(choices["B"]),
                    "C": _normalize_text(choices["C"]),
                    "D": _normalize_text(choices["D"]),
                },
                "answer": answer,
                "explanation": _normalize_text(item.get("explanation", "")),
            }
        )

    if not out:
        # Retry once with a smaller request to reduce malformed long JSON.
        if count > 3:
            retry_rows = _call_gemini_generate_questions(category, max(3, count // 2), difficulty)
            if retry_rows:
                return retry_rows
        raise RuntimeError("Gemini returned no valid Korean questions")
    return out


# [AI 생성 문제 DB 저장] 중복 해시 체크 후 신규 문항만 questions 테이블에 INSERT, 삽입/스킵 수 반환
def _save_generated_questions(rows):
    inserted = 0
    skipped = 0
    batch_hashes = set()
    for row in rows:
        cleaned_question = _sanitize_question_text(row["question"])
        if not is_korean_text(cleaned_question):
            skipped += 1
            continue
        q_hash = _question_hash(row["category"], cleaned_question)
        if q_hash in batch_hashes or Question.query.filter_by(question_hash=q_hash).first():
            skipped += 1
            continue
        batch_hashes.add(q_hash)
        db.session.add(
            Question(
                category=row["category"],
                question=cleaned_question,
                choice_a=row["choices"]["A"],
                choice_b=row["choices"]["B"],
                choice_c=row["choices"]["C"],
                choice_d=row["choices"]["D"],
                answer=row["answer"],
                explanation=row["explanation"],
                question_hash=q_hash,
                created_at=now_kst_naive(),
            )
        )
        inserted += 1
    if inserted:
        db.session.commit()
    return inserted, skipped


# ──────────────────────────────────────────────────────────
# 사용자 중복 회피: 최근 풀이 이력에서 문제 해시를 추출
# 동일 사용자가 최근에 풀었던 문제를 재출제하지 않도록 제외 목록 구성
# ──────────────────────────────────────────────────────────
def _get_recent_user_question_hashes(user_name, limit_attempts=20):
    name = _normalize_text(user_name)
    if not name:
        return set()
    user = User.query.filter_by(name=name).first()
    if not user:
        return set()

    attempts = (
        QuizAttempt.query.filter_by(user_id=user.id)
        .order_by(QuizAttempt.id.desc())
        .limit(limit_attempts)
        .all()
    )
    used = set()
    for a in attempts:
        try:
            rows = json.loads(a.answers_json or "[]")
        except Exception:
            rows = []
        for r in rows:
            q_category = str(r.get("category") or a.category or "").strip().lower()
            q_text = str(r.get("question") or "").strip()
            if q_category and q_text:
                used.add(_question_hash(q_category, q_text))
    return used


# [한국어 문제 최소 수량 보장] DB에 한국어 문제가 limit 미만이면 Gemini API로 부족분 자동 보충
def _ensure_min_korean_questions(category, limit, difficulty):
    current_ko = [q for q in Question.query.filter_by(category=category).all() if is_korean_text(q.question)]
    needed = max(0, limit - len(current_ko))
    retries = 0

    while needed > 0 and retries < 1:
        retries += 1
        rows = _call_gemini_generate_questions(category, min(max(needed, 3), 12), difficulty)
        inserted, _ = _save_generated_questions(rows)
        if inserted == 0:
            break
        current_ko = [q for q in Question.query.filter_by(category=category).all() if is_korean_text(q.question)]
        needed = max(0, limit - len(current_ko))


PURGED_DEFAULT_COUNT = _ensure_schema()


# ──────────────────────────────────────────────────────────
# API 라우트 정의
# ──────────────────────────────────────────────────────────

# [관리자] 전체 문제 삭제 - 하드코딩 문제 초기화용
@app.route("/api/admin/purge-questions", methods=["DELETE"])
def purge_all_questions():
    """DB의 모든 문제 삭제 - 하드코딩 문제 정리용"""
    try:
        count = Question.query.count()
        Question.query.delete()
        db.session.commit()
        return jsonify({"deleted": count, "message": f"{count}개 문제가 삭제되었습니다."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# [헬스체크] DB 연결 상태 확인
@app.route("/api/health", methods=["GET"])
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "db": "connected", "db_mode": ACTIVE_DB}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500


# [AI 헬스체크] Gemini API 키 설정 여부 + 네트워크 연결 여부 확인
@app.route("/api/ai/health", methods=["GET"])
def ai_health():
    check = request.args.get("check", "0") == "1"
    # 네트워크 연결 여부는 check 없이도 항상 표시 (진단 편의)
    network_ok = _is_tcp_open("generativelanguage.googleapis.com", 443, timeout=3.0)
    payload = {
        "provider": "gemini",
        "configured": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
        "purged_default_count": PURGED_DEFAULT_COUNT,
        "network_reachable": network_ok,
        "network_target": "generativelanguage.googleapis.com:443",
    }
    if not check:
        return jsonify(payload), 200

    if not GEMINI_API_KEY:
        payload.update({"reachable": False, "error": "GEMINI_API_KEY is not configured"})
        return jsonify(payload), 200

    if not network_ok:
        payload.update({
            "reachable": False,
            "error": "NETWORK_BLOCKED: TCP 443 to generativelanguage.googleapis.com is not reachable. "
                     "Check NetworkPolicy backend-egress-mysql-and-https or cluster egress rules."
        })
        return jsonify(payload), 200

    try:
        _call_gemini_generate_questions("network", 1, "mixed")
        payload.update({"reachable": True})
    except Exception as e:
        payload.update({"reachable": False, "error": str(e)})
    return jsonify(payload), 200


# [AI 문제 생성] 카테고리/개수/난이도를 받아 Gemini로 문제를 생성하고 DB에 저장
@app.route("/api/ai/questions", methods=["POST"])
def generate_questions():
    data = request.get_json(silent=True) or {}
    category = str(data.get("category", "")).strip().lower()
    difficulty = str(data.get("difficulty", "mixed")).strip().lower()
    try:
        count = int(data.get("count", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "count must be integer"}), 400
    count = max(1, min(count, 20))
    if category not in VALID_CATEGORIES:
        return jsonify({"error": "category must be one of network, infra, linux"}), 400

    rows = _call_gemini_generate_questions(category, count, difficulty)
    inserted, skipped = _save_generated_questions(rows)

    unique_rows = []
    seen_hash = set()
    for row in rows:
        q_hash = _question_hash(row["category"], row["question"])
        if q_hash in seen_hash:
            continue
        seen_hash.add(q_hash)
        row["question"] = _sanitize_question_text(row["question"])
        unique_rows.append(row)
        if len(unique_rows) >= count:
            break

    return jsonify(
        {
            "provider": "gemini",
            "category": category,
            "inserted_count": inserted,
            "duplicate_skipped_count": skipped,
            "questions": unique_rows,
        }
    ), 200


# [카테고리 목록] DB에 저장된 카테고리별 문제 수 반환
@app.route("/api/categories", methods=["GET"])
def get_categories():
    rows = db.session.query(Question.category, db.func.count(Question.id).label("count")).group_by(Question.category).all()
    count_map = {r.category: int(r.count) for r in rows}
    categories = [{"name": c, "count": count_map.get(c, 0)} for c in sorted(VALID_CATEGORIES)]
    return jsonify({"categories": categories}), 200


# [문제 출제] source=ai면 Gemini 우선 생성 후 DB 보강, source=db면 DB에서만 출제
# user 파라미터로 최근 기출 문제 제외 가능
@app.route("/api/questions/<category>", methods=["GET"])
def get_questions(category):
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        return jsonify({"error": "invalid category"}), 400

    try:
        limit = min(max(int(request.args.get("limit", 10)), 1), 50)
    except ValueError:
        return jsonify({"error": "limit must be integer"}), 400

    source = request.args.get("source", "db").strip().lower()
    difficulty = request.args.get("difficulty", "mixed").strip().lower()
    shuffle = request.args.get("shuffle", "1") == "1"
    user_name = request.args.get("user", "").strip()
    exclude_hashes = _get_recent_user_question_hashes(user_name)

    if source == "ai":
        ai_error = ""
        ai_errors = []
        generated_rows = []
        # AI-first: try to fill the whole requested count from API-generated questions.
        target_new = limit

        seen_hash = set()
        ai_started_at = time.monotonic()
        attempts = 0
        while (
            len(generated_rows) < target_new
            and attempts < 5
            and (time.monotonic() - ai_started_at) < AI_REQUEST_BUDGET_SEC
        ):
            attempts += 1
            needed = target_new - len(generated_rows)
            # batch_size: 최대 8로 완화해서 10문제 요청 시 2번 내로 완료 가능하게.
            batch_size = min(max(needed, 3), 20)
            try:
                batch = _call_gemini_generate_questions(category, batch_size, difficulty)
                for row in batch:
                    qh = _question_hash(row["category"], row["question"])
                    if qh in seen_hash:
                        continue
                    seen_hash.add(qh)
                    generated_rows.append(row)
                    if len(generated_rows) >= target_new:
                        break
            except Exception as e:
                ai_error = str(e)
                ai_errors.append(ai_error)

        inserted, skipped = _save_generated_questions(generated_rows) if generated_rows else (0, 0)
        generated_hashes = list({_question_hash(r["category"], r["question"]) for r in generated_rows})

        selected = []
        seen_selected = set()
        ai_count = 0

        if generated_hashes:
            ai_rows = (
                Question.query.filter_by(category=category)
                .filter(Question.question_hash.in_(generated_hashes))
                .order_by(Question.id.desc())
                .all()
            )
            for q in ai_rows:
                key = _question_hash(q.category, q.question)
                if key in seen_selected:
                    continue
                # AI로 방금 생성된 문제는 exclude_hashes(기출) 필터를 적용하지 않음.
                # 기출 필터는 DB 캐시 풀에서 뽑을 때만 적용해야 신규 문제가 보장됨.
                selected.append(q)
                seen_selected.add(key)
                ai_count += 1
                if len(selected) >= limit:
                    break

        # AI 실패 시 DB 캐시에서 보충 (하드코딩 fallback 없음)
        if len(selected) < limit:
            db_pool = (
                Question.query.filter_by(category=category)
                .order_by(Question.id.desc())
                .all()
            )
            for q in db_pool:
                key = _question_hash(q.category, q.question)
                if key in seen_selected:
                    continue
                if not is_korean_text(q.question):
                    continue
                selected.append(q)
                seen_selected.add(key)
                if len(selected) >= limit:
                    break

        if len(selected) < limit:
            # AI 생성 실패 및 DB 캐시도 부족한 경우 명확한 오류 반환
            err_msg = ai_error or "AI 문제 생성에 실패했습니다. GEMINI_API_KEY를 확인하거나 잠시 후 다시 시도해주세요."
            if shuffle:
                random.shuffle(selected)
            return jsonify(
                {
                    "category": category,
                    "source": "ai",
                    "provider": "degraded",
                    "requested": limit,
                    "total": len(selected),
                    "ai_count": ai_count,
                    "db_count": max(0, len(selected) - ai_count),
                    "warning": err_msg,
                    "ai_errors": ai_errors[-3:],
                    "inserted_count": inserted,
                    "duplicate_skipped_count": skipped,
                    "questions": [q.to_dict(hide_answer=True) for q in selected],
                }
            ), 200 if selected else 502

        db_count = max(0, len(selected) - ai_count)
        if ai_count > 0 and db_count > 0:
            provider = "hybrid"
        elif ai_count > 0:
            provider = "gemini"
        else:
            provider = "cache-db"

        if shuffle:
            random.shuffle(selected)

        return jsonify(
            {
                "category": category,
                "source": "ai",
                "provider": provider,
                "total": len(selected),
                "requested": limit,
                "ai_count": ai_count,
                "db_count": db_count,
                "warning": ai_error if ai_error else "",
                "inserted_count": inserted,
                "duplicate_skipped_count": skipped,
                "questions": [q.to_dict(hide_answer=True) for q in selected],
            }
        ), 200

    if source == "auto":
        try:
            _ensure_min_korean_questions(category, limit, difficulty)
        except Exception:
            pass

    raw_questions = Question.query.filter_by(category=category).all()
    questions = [q for q in raw_questions if is_korean_text(q.question)]
    if not questions:
        return jsonify({"error": f"category '{category}' has no Korean questions"}), 404

    if shuffle:
        random.shuffle(questions)

    selected = []
    seen_key = set()
    for q in questions:
        key = _question_hash(q.category, q.question)
        if key in seen_key:
            continue
        q.question = _sanitize_question_text(q.question)
        selected.append(q)
        seen_key.add(key)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        return jsonify(
            {
                "error": "\uc694\uccad\ud55c \ubb38\uc81c \uc218\ub97c \ucc44\uc6b0\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.",
                "category": category,
                "requested": limit,
                "returned": len(selected),
            }
        ), 502

    return jsonify({"category": category, "total": len(selected), "questions": [q.to_dict(hide_answer=True) for q in selected]}), 200


# [정답 포함 문제 조회] 채점 후 리뷰 화면에서 정답/해설을 포함한 문제 상세 반환
@app.route("/api/questions/<category>/all", methods=["GET", "POST"])
def get_questions_with_answers(category):
    data = request.get_json(silent=True) or {}
    if not data.get("ids") and request.args.get("ids"):
        ids = [int(i) for i in request.args.get("ids").split(",") if i.strip().isdigit()]
    else:
        ids = data.get("ids", [])
    query = Question.query.filter_by(category=category)
    if ids:
        query = query.filter(Question.id.in_(ids))
    questions = [q for q in query.all() if is_korean_text(q.question)]
    return jsonify({"category": category, "questions": [q.to_dict(hide_answer=False) for q in questions]}), 200


# [답안 제출] 사용자 답안을 채점하고 결과를 DB에 저장 (QuizAttempt 생성)
@app.route("/api/submit", methods=["POST"])
def submit_answers():
    data = request.get_json() or {}
    if "answers" not in data:
        return jsonify({"error": "answers field is required"}), 400

    user_name = _normalize_text(data.get("user_name", "\uc775\uba85"))[:100] or "\uc775\uba85"
    quiz_category = _normalize_text(data.get("category", ""))[:50]

    answers = data["answers"]
    ids = [a["id"] for a in answers]
    questions = Question.query.filter(Question.id.in_(ids)).all()
    q_map = {q.id: q for q in questions}

    results = []
    correct_count = 0
    for a in answers:
        qid = a["id"]
        selected = a.get("selected", "").upper()
        q = q_map.get(qid)
        if not q:
            continue
        is_correct = selected == q.answer.upper()
        if is_correct:
            correct_count += 1
        results.append(
            {
                "id": qid,
                "question": q.question,
                "category": q.category,
                "choices": {"A": q.choice_a, "B": q.choice_b, "C": q.choice_c, "D": q.choice_d},
                "selected": selected,
                "answer": q.answer.upper(),
                "is_correct": is_correct,
                "explanation": q.explanation,
            }
        )

    total = len(results)
    wrong_count = total - correct_count
    score_percent = round(correct_count / total * 100, 1) if total else 0

    user = User.query.filter_by(name=user_name).first()
    if not user:
        user = User(name=user_name, created_at=now_kst_naive())
        db.session.add(user)
        db.session.flush()

    if not quiz_category and results:
        quiz_category = results[0]["category"]

    attempt = QuizAttempt(
        user_id=user.id,
        category=quiz_category or "mixed",
        total=total,
        correct=correct_count,
        wrong=wrong_count,
        score_percent=score_percent,
        answers_json=json.dumps(results, ensure_ascii=False),
        created_at=now_kst_naive(),
    )
    db.session.add(attempt)
    db.session.commit()

    return jsonify(
        {
            "total": total,
            "correct": correct_count,
            "wrong": wrong_count,
            "score_percent": score_percent,
            "results": results,
            "attempt_id": attempt.id,
            "user_name": user_name,
        }
    ), 200


# [풀이 이력 목록] 사용자 이름으로 시도 이력 목록 조회 (최신순)
@app.route("/api/history/<user_name>", methods=["GET"])
def get_user_history(user_name):
    name = _normalize_text(user_name)
    user = User.query.filter_by(name=name).first()
    if not user:
        return jsonify({"user_name": name, "attempts": []}), 200

    limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    attempts = QuizAttempt.query.filter_by(user_id=user.id).order_by(QuizAttempt.created_at.desc()).limit(limit).all()

    result = []
    for a in attempts:
        result.append(
            {
                "attempt_id": a.id,
                "category": a.category,
                "total": a.total,
                "correct": a.correct,
                "wrong": a.wrong,
                "score_percent": a.score_percent,
                "created_at_kst": a.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return jsonify({"user_name": user.name, "created_at_kst": user.created_at.strftime("%Y-%m-%d %H:%M:%S"), "attempts": result}), 200


# [시도 상세 조회] 특정 attempt_id의 답안 전체(정답/오답 포함)를 반환 - 리뷰 화면에서 사용
@app.route("/api/history/<user_name>/<int:attempt_id>", methods=["GET"])
def get_attempt_detail(user_name, attempt_id):
    name = _normalize_text(user_name)
    user = User.query.filter_by(name=name).first()
    if not user:
        return jsonify({"error": "user not found"}), 404

    attempt = QuizAttempt.query.filter_by(id=attempt_id, user_id=user.id).first()
    if not attempt:
        return jsonify({"error": "attempt not found"}), 404

    try:
        results = json.loads(attempt.answers_json or "[]")
    except Exception:
        results = []

    return jsonify(
        {
            "attempt_id": attempt.id,
            "user_name": user.name,
            "category": attempt.category,
            "total": attempt.total,
            "correct": attempt.correct,
            "wrong": attempt.wrong,
            "score_percent": attempt.score_percent,
            "created_at_kst": attempt.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "results": results,
        }
    ), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)