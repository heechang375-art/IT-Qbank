"""
Network/Infrastructure/Linux Quiz Bank - Backend API
"""

import hashlib
import json
import os
import random
import re
import socket
import urllib.request
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

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

# Groq config
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "45"))
VALID_CATEGORIES = {"network", "infra", "linux"}

KST = UTC + timedelta(hours=9)
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


@event.listens_for(db.engine, "connect")
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


LOCAL_KO_SEED = [
    ("network", "OSI 계층 중 라우팅을 담당하는 계층은?", "물리 계층", "데이터링크 계층", "네트워크 계층", "전송 계층", "C", "IP 기반 라우팅은 네트워크 계층에서 수행됩니다."),
    ("network", "HTTPS 기본 포트는?", "80", "443", "22", "53", "B", "HTTPS는 TCP 443 포트를 사용합니다."),
    ("infra", "Kubernetes의 최소 배포 단위는?", "Container", "Pod", "Node", "Namespace", "B", "Pod는 최소 배포 단위입니다."),
    ("infra", "로드밸런서의 주요 역할은?", "디스크 암호화", "트래픽 분산", "코드 빌드", "DNS 제거", "B", "요청 트래픽을 여러 서버에 분산합니다."),
    ("linux", "실행 중인 프로세스를 확인하는 명령은?", "df -h", "ps aux", "chmod 755", "tail -f", "B", "ps aux는 실행 중인 프로세스를 출력합니다."),
    ("linux", "chmod 755 권한 설명으로 맞는 것은?", "소유자 rwx, 그룹 r-x, 기타 r-x", "소유자 r--, 그룹 rwx, 기타 rwx", "모든 사용자 rwx", "소유자만 rwx", "A", "7은 rwx, 5는 r-x를 의미합니다."),
]

FALLBACK_KO_BANK = {
    "network": [
        ("서브넷 마스크 /24에서 사용 가능한 호스트 수는?", ["254", "256", "510", "1022"], "A", "/24는 2^8-2로 254개입니다."),
        ("DNS 기본 포트는?", ["22", "53", "80", "443"], "B", "DNS는 기본적으로 53 포트를 사용합니다."),
        ("UDP의 특징으로 옳은 것은?", ["연결형 전송", "순서 보장", "비연결형/오버헤드 적음", "혼잡 제어 강함"], "C", "UDP는 비연결형이며 오버헤드가 적습니다."),
        ("라우터가 주로 동작하는 OSI 계층은?", ["1계층", "2계층", "3계층", "7계층"], "C", "라우팅은 3계층에서 수행됩니다."),
    ],
    "infra": [
        ("컨테이너와 VM의 차이로 맞는 것은?", ["컨테이너가 항상 무겁다", "VM은 커널 공유", "컨테이너는 호스트 커널 공유", "둘 다 동일"], "C", "컨테이너는 호스트 OS 커널을 공유합니다."),
        ("Kubernetes Ingress의 역할은?", ["볼륨 생성", "외부 HTTP/HTTPS 라우팅", "파드 스케일링", "노드 교체"], "B", "Ingress는 외부 요청 라우팅 규칙을 제공합니다."),
        ("Prometheus의 주 용도는?", ["소스코드 빌드", "메트릭 수집/모니터링", "이미지 레지스트리", "패킷 캡처"], "B", "Prometheus는 시계열 메트릭 수집에 사용됩니다."),
        ("Terraform이 속한 범주는?", ["IaC", "APM", "RDBMS", "VCS"], "A", "Terraform은 대표적인 IaC 도구입니다."),
    ],
    "linux": [
        ("디스크 사용량을 보기 좋은 형태로 확인하는 명령은?", ["ps aux", "df -h", "free -m", "top"], "B", "df -h는 파일시스템 사용량을 사람이 읽기 쉬운 형태로 표시합니다."),
        ("실시간 로그 추적 명령은?", ["tail -f", "head -n", "cat", "grep"], "A", "tail -f로 로그를 실시간 확인합니다."),
        ("포트 리스닝 상태 확인에 주로 쓰는 명령은?", ["ip route", "ss -tlnp", "chmod", "chown"], "B", "ss -tlnp는 TCP 리스닝 포트와 프로세스를 보여줍니다."),
        ("권한 변경 명령은?", ["chown", "chmod", "mv", "ln"], "B", "chmod로 파일 권한을 변경합니다."),
    ],
}


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


def _ensure_schema_and_seed():
    with app.app_context():
        db.create_all()
        _cleanup_legacy_variant_questions()
        for row in LOCAL_KO_SEED:
            q_hash = _question_hash(row[0], row[1])
            if Question.query.filter_by(question_hash=q_hash).first():
                continue
            db.session.add(
                Question(
                    category=row[0],
                    question=row[1],
                    choice_a=row[2],
                    choice_b=row[3],
                    choice_c=row[4],
                    choice_d=row[5],
                    answer=row[6],
                    explanation=row[7],
                    question_hash=q_hash,
                )
            )
        db.session.commit()


def _build_fallback_questions(category, count):
    bank = FALLBACK_KO_BANK.get(category, [])
    if not bank:
        return []

    shuffled = bank[:]
    random.shuffle(shuffled)
    out = []
    for q, choices, answer, explanation in shuffled:
        out.append(
            {
                "category": category,
                "question": _sanitize_question_text(q),
                "choices": {"A": choices[0], "B": choices[1], "C": choices[2], "D": choices[3]},
                "answer": answer,
                "explanation": explanation,
            }
        )
        if len(out) >= count:
            break
    return out


def _call_groq_generate_questions(category, count, difficulty):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.4,
        "messages": [
            {
                "role": "system",
                "content": "Return only JSON: {\"questions\":[{\"question\":\"...\",\"choices\":{\"A\":\"...\",\"B\":\"...\",\"C\":\"...\",\"D\":\"...\"},\"answer\":\"A|B|C|D\",\"explanation\":\"...\"}]}. All text must be Korean.",
            },
            {
                "role": "user",
                "content": f"카테고리 {category}, 난이도 {difficulty} 기준 객관식 문제 {count}개를 한국어로 생성해줘.",
            },
        ],
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        GROQ_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=GROQ_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    parsed = json.loads(body.get("choices", [{}])[0].get("message", {}).get("content", "{}"))

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
        raise RuntimeError("Groq returned no valid Korean questions")
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
            )
        )
        inserted += 1
    if inserted:
        db.session.commit()
    return inserted, skipped


def _ensure_min_korean_questions(category, limit, difficulty, force_generate=False):
    current_ko = Question.query.filter_by(category=category).all()
    current_ko = [q for q in current_ko if is_korean_text(q.question)]
    needed = max(0, limit - len(current_ko))
    generate_target = needed
    if force_generate:
        generate_target = max(generate_target, limit)

    if generate_target == 0:
        return

    retries = 0
    generated_new = 0
    while generate_target > 0 and retries < 3:
        retries += 1
        rows = []
        try:
            rows = _call_groq_generate_questions(category, min(max(generate_target * 3, 10), 50), difficulty)
        except Exception:
            rows = []

        inserted, _ = _save_generated_questions(rows)
        # fallback은 "정말 부족할 때"만 사용. force_generate 시엔 fallback으로 돌려막기하지 않음.
        if inserted == 0 and needed > 0:
            inserted, _ = _save_generated_questions(_build_fallback_questions(category, needed))
        if inserted == 0:
            break

        generated_new += inserted
        current_ko = [q for q in Question.query.filter_by(category=category).all() if is_korean_text(q.question)]
        needed = max(0, limit - len(current_ko))
        if force_generate:
            generate_target = max(needed, limit - generated_new)
        else:
            generate_target = needed


_ensure_schema_and_seed()


@app.route("/api/health", methods=["GET"])
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "db": "connected", "db_mode": ACTIVE_DB}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500


@app.route("/api/ai/health", methods=["GET"])
def ai_health():
    return jsonify({"provider": "groq", "configured": bool(GROQ_API_KEY), "model": GROQ_MODEL}), 200


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

    try:
        rows = _call_groq_generate_questions(category, count, difficulty)
        provider = "groq"
    except Exception:
        rows = _build_fallback_questions(category, count)
        provider = "fallback-local"

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
    return jsonify({"provider": provider, "category": category, "inserted_count": inserted, "duplicate_skipped_count": skipped, "questions": unique_rows}), 200


@app.route("/api/categories", methods=["GET"])
def get_categories():
    rows = db.session.query(Question.category, db.func.count(Question.id).label("count")).group_by(Question.category).all()
    return jsonify({"categories": [{"name": r.category, "count": r.count} for r in rows]}), 200


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
    fresh = request.args.get("fresh", "0") == "1"

    if source in {"ai", "auto"}:
        _ensure_min_korean_questions(category, limit, difficulty, force_generate=(source == "ai" or fresh))

    questions = [q for q in Question.query.filter_by(category=category).all() if is_korean_text(q.question)]
    if not questions:
        return jsonify({"error": f"category '{category}' has no Korean questions"}), 404
    if shuffle:
        random.shuffle(questions)
    selected = []
    seen_hash = set()
    for q in questions:
        if q.question_hash in seen_hash:
            continue
        selected.append(q)
        seen_hash.add(q.question_hash)
        if len(selected) >= limit:
            break
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

    return jsonify({"total": total, "correct": correct_count, "wrong": wrong_count, "score_percent": score_percent, "results": results, "attempt_id": attempt.id, "user_name": user_name}), 200


@app.route("/api/history/<user_name>", methods=["GET"])
def get_user_history(user_name):
    name = _normalize_text(user_name)
    user = User.query.filter_by(name=name).first()
    if not user:
        return jsonify({"user_name": name, "attempts": []}), 200

    limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    attempts = (
        QuizAttempt.query.filter_by(user_id=user.id)
        .order_by(QuizAttempt.created_at.desc())
        .limit(limit)
        .all()
    )

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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
