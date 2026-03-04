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
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "8"))
AI_REQUEST_BUDGET_SEC = int(os.getenv("AI_REQUEST_BUDGET_SEC", "20"))

# Maintenance config
PURGE_DEFAULT_ON_BOOT = os.getenv("PURGE_DEFAULT_ON_BOOT", "true").lower() == "true"

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


def _build_local_fallback_questions(category, count, start_index=1):
    """Deterministic local fallback to avoid hard 502 when AI provider fails."""
    bank = {
        "linux": [
            (
                "\ub9ac\ub205\uc2a4 \uc11c\ubc84\uc5d0\uc11c \ud2b9\uc815 \ud3ec\ud2b8\uc758 LISTEN \uc0c1\ud0dc\ub97c \ud655\uc778\ud558\ub294 \ub370 \uac00\uc7a5 \uc801\uc808\ud55c \uba85\ub839\uc740?",
                {"A": "ps aux", "B": "ss -tlnp", "C": "df -h", "D": "chmod 755"},
                "B",
                "ss -tlnp\ub294 \ub9ac\uc2a4\ub2dd \ud3ec\ud2b8\uc640 \uc5f0\uacb0\ub41c \ud504\ub85c\uc138\uc2a4\ub97c \ud655\uc778\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.",
            ),
            (
                "\ub2e4\uc74c \uc911 \ud30c\uc77c \uc18c\uc720\uc790\uc5d0\uac8c\ub9cc \uc4f0\uae30 \uad8c\ud55c\uc744 \ubd80\uc5ec\ud558\uace0 \uadf8\ub8f9/\uae30\ud0c0\ub294 \uc77d\uae30\ub9cc \uac00\ub2a5\ud558\uac8c \uc124\uc815\ud558\ub294 \uad8c\ud55c \uac12\uc740?",
                {"A": "755", "B": "744", "C": "700", "D": "664"},
                "B",
                "744\ub294 \uc18c\uc720\uc790 rwx, \uadf8\ub8f9 r--, \uae30\ud0c0 r-- \uc758\ubbf8\uc785\ub2c8\ub2e4.",
            ),
            (
                "\uc2e4\ubb34\uc5d0\uc11c \ub85c\uadf8\ub97c \uc2e4\uc2dc\uac04 \ubaa8\ub2c8\ud130\ub9c1\ud558\uba74\uc11c \uc2e0\uaddc \ub77c\uc778\ub9cc \uacc4\uc18d \ubcf4\ub824\uba74 \uc5b4\ub5a4 \uba85\ub839\uc744 \uc0ac\uc6a9\ud574\uc57c \ud558\ub294\uac00?",
                {"A": "tail -f", "B": "head -n 10", "C": "cat", "D": "lsblk"},
                "A",
                "tail -f\ub294 \ud30c\uc77c\uc758 \ub05d\ubd80\ubd84\uc744 \ucd94\uc801\ud558\uba70 \uc2e4\uc2dc\uac04 \ub85c\uadf8 \ud655\uc778\uc5d0 \uc801\ud569\ud569\ub2c8\ub2e4.",
            ),
        ],
        "network": [
            (
                "OSI 7\uacc4\uce35 \uc911 IP \ub77c\uc6b0\ud305\uc774 \uc218\ud589\ub418\ub294 \uacc4\uce35\uc740?",
                {"A": "\ubb3c\ub9ac \uacc4\uce35", "B": "\ub370\uc774\ud130\ub9c1\ud06c \uacc4\uce35", "C": "\ub124\ud2b8\uc6cc\ud06c \uacc4\uce35", "D": "\uc138\uc158 \uacc4\uce35"},
                "C",
                "\ub77c\uc6b0\ud305\uc740 3\uacc4\uce35(\ub124\ud2b8\uc6cc\ud06c \uacc4\uce35)\uc758 \ud575\uc2ec \uae30\ub2a5\uc785\ub2c8\ub2e4.",
            ),
            (
                "DNS\uc758 \uae30\ubcf8 \uc11c\ube44\uc2a4 \ud3ec\ud2b8\ub85c \uc62c\ubc14\ub978 \uac83\uc740?",
                {"A": "22", "B": "53", "C": "80", "D": "443"},
                "B",
                "DNS\ub294 \uc77c\ubc18\uc801\uc73c\ub85c UDP/TCP 53 \ud3ec\ud2b8\ub97c \uc0ac\uc6a9\ud569\ub2c8\ub2e4.",
            ),
            (
                "UDP\uc5d0 \ub300\ud55c \uc124\uba85\uc73c\ub85c \uac00\uc7a5 \ud0c0\ub2f9\ud55c \uac83\uc740?",
                {"A": "\uc5f0\uacb0 \uc218\ub9bd \ud6c4 \uc804\uc1a1\ud55c\ub2e4", "B": "\uc21c\uc11c\ub97c \ubcf4\uc7a5\ud55c\ub2e4", "C": "\uc624\ubc84\ud5e4\ub4dc\uac00 \uc791\uc544 \ube60\ub978 \ud3b8\uc774\ub2e4", "D": "\ud750\ub984 \uc81c\uc5b4\uac00 \ud544\uc218\ub2e4"},
                "C",
                "UDP\ub294 \ube44\uc5f0\uacb0\ud615 \ud504\ub85c\ud1a0\ucf5c\ub85c \uc9c0\uc5f0\uacfc \uc624\ubc84\ud5e4\ub4dc\uac00 \uc801\uc740 \ud3b8\uc785\ub2c8\ub2e4.",
            ),
        ],
        "infra": [
            (
                "Kubernetes\uc5d0\uc11c Pod \uc678\ubd80 \ud2b8\ub798\ud53d \uc720\uc785\uc744 URL \uae30\ubc18\uc73c\ub85c \uc81c\uc5b4\ud558\ub294 \ub9ac\uc18c\uc2a4\ub294?",
                {"A": "ConfigMap", "B": "Ingress/HTTPRoute", "C": "Secret", "D": "PVC"},
                "B",
                "Ingress \ub610\ub294 Gateway API\uc758 HTTPRoute\ub97c \ud1b5\ud574 \uacbd\ub85c/\ud638\uc2a4\ud2b8 \ub77c\uc6b0\ud305\uc744 \uad6c\uc131\ud569\ub2c8\ub2e4.",
            ),
            (
                "\ub85c\ub4dc\ubc38\ub7f0\uc11c\uc758 \uc8fc\uc694 \ubaa9\uc801\uc73c\ub85c \uac00\uc7a5 \uc62c\ubc14\ub978 \uac83\uc740?",
                {"A": "\ud30c\uc77c \uad8c\ud55c \ubcc0\uacbd", "B": "\ud2b8\ub798\ud53d \ubd84\uc0b0\uacfc \uac00\uc6a9\uc131 \ud5a5\uc0c1", "C": "\uc18c\uc2a4 \ucf54\ub4dc \ube4c\ub4dc", "D": "DNS \ub808\ucf54\ub4dc \uc0ad\uc81c"},
                "B",
                "\ub85c\ub4dc\ubc38\ub7f0\uc11c\ub294 \uc694\uccad\uc744 \ub2e4\uc218 \ub300\uc0c1\uc73c\ub85c \ubd84\uc0b0\ud558\uc5ec \ud655\uc7a5\uc131\uacfc \uc548\uc815\uc131\uc744 \ub192\uc785\ub2c8\ub2e4.",
            ),
            (
                "IaC(Infrastructure as Code)\uc758 \uac15\uc810\uc73c\ub85c \uc801\uc808\ud55c \uac83\uc740?",
                {"A": "\uad6c\uc131 \uc77c\uad00\uc131 \uc800\ud558", "B": "\uc791\uc5c5 \uc774\ub825 \ucd94\uc801 \ubd88\uac00", "C": "\uc7ac\ud604 \uac00\ub2a5\ud55c \uc778\ud504\ub77c \uad6c\uc131", "D": "\ubc30\ud3ec \uc790\ub3d9\ud654 \ubd88\uac00\ub2a5"},
                "C",
                "IaC\ub294 \ucf54\ub4dc \ub9ac\ubdf0\uc640 \ubc84\uc804\uad00\ub9ac\ub97c \ud1b5\ud574 \uc778\ud504\ub77c \uc7ac\ud604\uc131\uc744 \ub192\uc785\ub2c8\ub2e4.",
            ),
        ],
    }

    seeds = bank.get(category, [])
    if not seeds:
        return []

    rows = []
    for i in range(count):
        q, choices, answer, exp = seeds[i % len(seeds)]
        # Keep uniqueness without trailing "(n)" style that may be stripped by sanitizer.
        suffix = f" -- \uc2dc\ub098\ub9ac\uc624 \ucf54\ub4dc {start_index + i}"
        rows.append(
            {
                "category": category,
                "question": _sanitize_question_text(f"{q}{suffix}"),
                "choices": choices,
                "answer": answer,
                "explanation": exp,
            }
        )
    return rows


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
            "maxOutputTokens": 3072,
        },
    }

    model_candidates = []
    for m in [GEMINI_MODEL, "gemini-2.0-flash"]:
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
        ai_errors = []
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

        seen_hash = set()
        ai_started_at = time.monotonic()
        attempts = 0
        while (
            len(generated_rows) < target_new
            and attempts < 2
            and (time.monotonic() - ai_started_at) < AI_REQUEST_BUDGET_SEC
        ):
            attempts += 1
            needed = target_new - len(generated_rows)
            # Keep each request small to avoid long model latency.
            batch_size = min(max(needed + 1, 3), 6)
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

        transient_extra = []
        if len(selected) < limit:
            fallback_rows = _build_local_fallback_questions(category, limit - len(selected), start_index=len(selected) + 1)
            fallback_inserted, fallback_skipped = _save_generated_questions(fallback_rows) if fallback_rows else (0, 0)
            inserted += fallback_inserted
            skipped += fallback_skipped
            if fallback_rows:
                fallback_hashes = {_question_hash(r["category"], r["question"]) for r in fallback_rows}
                fallback_pool = (
                    Question.query.filter_by(category=category)
                    .filter(Question.question_hash.in_(list(fallback_hashes)))
                    .order_by(Question.id.desc())
                    .all()
                )
                for q in fallback_pool:
                    key = _question_hash(q.category, q.question)
                    if key in seen_selected:
                        continue
                    selected.append(q)
                    seen_selected.add(key)
                    if len(selected) >= limit:
                        break

        if len(selected) < limit:
            # Ensure the API still returns requested count even if DB insert dedupe blocks fallback rows.
            rows = _build_local_fallback_questions(category, limit - len(selected), start_index=1000 + len(selected))
            for idx, row in enumerate(rows, start=1):
                qh = _question_hash(category, row["question"])
                if qh in seen_selected:
                    continue
                seen_selected.add(qh)
                transient_extra.append(
                    {
                        "id": -(100000 + idx + len(transient_extra)),
                        "category": row["category"],
                        "question": row["question"],
                        "choices": row["choices"],
                    }
                )
                if len(selected) + len(transient_extra) >= limit:
                    break

        if len(selected) < limit:
            if shuffle:
                random.shuffle(selected)
            return jsonify(
                {
                    "category": category,
                    "source": "ai",
                    "provider": "degraded",
                    "requested": limit,
                    "total": len(selected) + len(transient_extra),
                    "ai_count": ai_count,
                    "db_count": max(0, len(selected) - ai_count),
                    "warning": ai_error or "insufficient questions",
                    "ai_errors": ai_errors[-3:],
                    "inserted_count": inserted,
                    "duplicate_skipped_count": skipped,
                    "questions": [q.to_dict(hide_answer=True) for q in selected] + transient_extra,
                }
            ), 200

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



