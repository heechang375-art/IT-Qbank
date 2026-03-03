"""
Network/Infrastructure/Linux Quiz Bank - Backend API
"""

import hashlib
import json
import os
import random
import re
import socket
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

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# DB config
DB_USER = os.getenv("DB_USER", "quizuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "quizpassword")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "quizdb")
USE_SQLITE_FALLBACK = os.getenv("USE_SQLITE_FALLBACK", "true").lower() == "true"

# Gemini config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_API_URL = os.getenv("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/models")
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "20"))

# Maintenance config
PURGE_DEFAULT_ON_BOOT = os.getenv("PURGE_DEFAULT_ON_BOOT", "true").lower() == "true"

VALID_CATEGORIES = {"network", "infra", "linux"}
CATEGORY_LABEL = {
    "network": "네트워크",
    "infra": "인프라",
    "linux": "리눅스",
}

KST = timezone(timedelta(hours=9))
KO_RE = re.compile(r"[가-힣]")


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


@event.listens_for(Engine, "connect")
def set_mysql_utf8mb4(dbapi_connection, _):
    try:
        cur = dbapi_connection.cursor()
        cur.execute("SET NAMES utf8mb4")
        cur.close()
    except Exception:
        pass


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


def _normalize_text(value):
    return " ".join(str(value or "").split())


VARIANT_SUFFIX_RE = re.compile(r"\s*\([^)]*\d+[^)]*\)\s*$")
TRAILING_BRACKET_RE = re.compile(r"\(\s*\d+\s*\)\s*$")


def _sanitize_question_text(value):
    text = _normalize_text(value)
    text = VARIANT_SUFFIX_RE.sub("", text)
    text = TRAILING_BRACKET_RE.sub("", text)
    return _normalize_text(text)


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
            db.session.execute(
                db.text(
                    "ALTER TABLE questions "
                    "ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )
            db.session.commit()
        return

    if "sqlite" in driver:
        rows = db.session.execute(db.text("PRAGMA table_info(questions)")).fetchall()
        names = {str(r[1]) for r in rows}
        if "created_at" not in names:
            db.session.execute(
                db.text("ALTER TABLE questions ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
            )
            db.session.commit()


def _default_bank_hashes():
    base_by_category = {
        "network": [
            "OSI 계층 중 라우팅을 담당하는 계층은?",
            "HTTPS 기본 포트는?",
            "서브넷 마스크 /24에서 사용 가능한 호스트 수는?",
            "DNS 기본 포트는?",
            "UDP의 특징으로 옳은 것은?",
            "라우터가 주로 동작하는 OSI 계층은?",
        ],
        "infra": [
            "Kubernetes의 최소 배포 단위는?",
            "로드밸런서의 주요 역할은?",
            "컨테이너와 VM의 차이로 맞는 것은?",
            "Kubernetes Ingress의 역할은?",
            "Prometheus의 주 용도는?",
            "Terraform의 대표 범주는?",
        ],
        "linux": [
            "실행 중인 프로세스를 확인하는 명령은?",
            "chmod 755 권한 설명으로 맞는 것은?",
            "디스크 사용량을 보기 좋은 형태로 확인하는 명령은?",
            "실시간 로그 추적 명령은?",
            "포트 리스닝 상태 확인에 주로 쓰는 명령은?",
            "권한 변경 명령은?",
            "SSH 공개키 로그인에 사용되는 파일은?",
        ],
    }
    prefixes = ["점검 문제:", "실무 문제:", "응용 문제:", "기초 문제:", "심화 문제:", "개념 문제:"]

    out = set()
    for category, questions in base_by_category.items():
        for q in questions:
            out.add(_question_hash(category, q))
            for p in prefixes:
                out.add(_question_hash(category, f"{p} {q}"))
    return out


def _purge_default_questions():
    purge_hashes = _default_bank_hashes()
    rows = Question.query.filter(Question.question_hash.in_(list(purge_hashes))).all()
    delete_ids = [r.id for r in rows]
    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
        db.session.commit()
    return len(delete_ids)


def _ensure_schema():
    with app.app_context():
        db.create_all()
        _ensure_questions_created_at_column()
        _cleanup_legacy_variant_questions()
        purged = 0
        if PURGE_DEFAULT_ON_BOOT:
            purged = _purge_default_questions()
        return purged


def _extract_json_text(value):
    text = _normalize_text(value)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def _safe_parse_questions_json(raw_text):
    text = _extract_json_text(raw_text)
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to extract JSON object boundaries.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    # Last resort: normalize common trailing comma issue.
    normalized = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(normalized)


def _call_gemini_generate_questions(category, count, difficulty):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")

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
    )

    payload = {
        "system_instruction": {
            "parts": [
                {
                    "text": "Return exactly one JSON object. Schema: {\"questions\":[{\"question\":\"...\",\"choices\":{\"A\":\"...\",\"B\":\"...\",\"C\":\"...\",\"D\":\"...\"},\"answer\":\"A\",\"explanation\":\"...\"}]}. All output text must be Korean."
                }
            ]
        },
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
            "maxOutputTokens": 3072,
        },
    }

    model_candidates = []
    for m in [GEMINI_MODEL, "gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash"]:
        if m and m not in model_candidates:
            model_candidates.append(m)

    body = None
    errors = []
    base_url = GEMINI_API_URL.rstrip("/")
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
        except Exception as e:
            errors.append(str(e))

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


@app.route("/api/health", methods=["GET"])
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "db": "connected", "db_mode": ACTIVE_DB}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500


@app.route("/api/ai/health", methods=["GET"])
def ai_health():
    check = request.args.get("check", "0") == "1"
    payload = {
        "provider": "gemini",
        "configured": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
        "purged_default_count": PURGED_DEFAULT_COUNT,
    }
    if not check:
        return jsonify(payload), 200

    if not GEMINI_API_KEY:
        payload.update({"reachable": False, "error": "GEMINI_API_KEY is not configured"})
        return jsonify(payload), 200

    try:
        _call_gemini_generate_questions("network", 1, "mixed")
        payload.update({"reachable": True})
    except Exception as e:
        payload.update({"reachable": False, "error": str(e)})
    return jsonify(payload), 200


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


@app.route("/api/categories", methods=["GET"])
def get_categories():
    rows = db.session.query(Question.category, db.func.count(Question.id).label("count")).group_by(Question.category).all()
    count_map = {r.category: int(r.count) for r in rows}
    categories = [{"name": c, "count": count_map.get(c, 0)} for c in sorted(VALID_CATEGORIES)]
    return jsonify({"categories": categories}), 200


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
        generated_rows = []
        target_new = min(limit, max(3, int(limit * 0.7)))

        # Recent pool for fallback fill only (AI-first policy).
        recent_cutoff = now_kst_naive() - timedelta(minutes=30)
        recent_pool = (
            Question.query.filter_by(category=category)
            .filter(Question.created_at >= recent_cutoff)
            .order_by(Question.id.desc())
            .all()
        )
        recent_unique = []
        seen_recent = set()
        for q in recent_pool:
            key = _question_hash(q.category, q.question)
            if key in seen_recent:
                continue
            seen_recent.add(key)
            recent_unique.append(q)

        try:
            batch = _call_gemini_generate_questions(category, min(max(target_new + 2, 5), 12), difficulty)
            seen_hash = set()
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
                if key in seen_selected or key in exclude_hashes:
                    continue
                selected.append(q)
                seen_selected.add(key)
                ai_count += 1
                if len(selected) >= limit:
                    break

        if len(selected) < limit:
            for q in recent_unique:
                key = _question_hash(q.category, q.question)
                if key in seen_selected or key in exclude_hashes:
                    continue
                selected.append(q)
                seen_selected.add(key)
                if len(selected) >= limit:
                    break

        if len(selected) < limit:
            all_pool = Question.query.filter_by(category=category).order_by(Question.id.desc()).all()
            for q in all_pool:
                key = _question_hash(q.category, q.question)
                if key in seen_selected or key in exclude_hashes:
                    continue
                selected.append(q)
                seen_selected.add(key)
                if len(selected) >= limit:
                    break

        # last resort: allow previously seen questions only when unavoidable
        if len(selected) < limit:
            all_pool = Question.query.filter_by(category=category).order_by(Question.id.desc()).all()
            for q in all_pool:
                key = _question_hash(q.category, q.question)
                if key in seen_selected:
                    continue
                selected.append(q)
                seen_selected.add(key)
                if len(selected) >= limit:
                    break

        if len(selected) < limit:
            return jsonify(
                {
                    "error": "요청한 문제 수를 채우지 못했습니다.",
                    "category": category,
                    "provider": provider if 'provider' in locals() else "gemini",
                    "requested": limit,
                    "returned": len(selected),
                    "ai_error": ai_error or "insufficient questions",
                }
            ), 502

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
                "error": "요청한 문제 수를 채우지 못했습니다.",
                "category": category,
                "requested": limit,
                "returned": len(selected),
            }
        ), 502

    return jsonify({"category": category, "total": len(selected), "questions": [q.to_dict(hide_answer=True) for q in selected]}), 200


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


@app.route("/api/submit", methods=["POST"])
def submit_answers():
    data = request.get_json() or {}
    if "answers" not in data:
        return jsonify({"error": "answers field is required"}), 400

    user_name = _normalize_text(data.get("user_name", "익명"))[:100] or "익명"
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
