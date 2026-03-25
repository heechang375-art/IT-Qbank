"""
Popular IT Quiz Bank - Backend API
"""

import hashlib
import json
import math
import os
import random
import re
import socket
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

load_dotenv()

# ──────────────────────────────────────────────────────────
# Flask 앱 초기화 및 CORS 설정
# ──────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ──────────────────────────────────────────────────────────
# 환경 변수 로드
# DB 접속 정보, Gemini AI 설정, 운영 옵션 등
# ──────────────────────────────────────────────────────────
DB_USER     = os.getenv("DB_USER", "quizuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "quizpassword")
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "3306")
DB_NAME     = os.getenv("DB_NAME", "quizdb")
USE_SQLITE_FALLBACK = os.getenv("USE_SQLITE_FALLBACK", "true").lower() == "true"

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_URL  = os.getenv("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/models")
GEMINI_MODEL_CANDIDATES = os.getenv("GEMINI_MODEL_CANDIDATES", "gemini-2.5-flash")

# GEMINI_TIMEOUT: Gemini API 응답 대기 최대 시간(초)
# 이 값이 실제 소켓 read timeout으로 적용됨 (연결 후 즉시 설정)
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "30"))

# AI_REQUEST_BUDGET_SEC: get_questions 내부 AI 호출 총 허용 시간
AI_REQUEST_BUDGET_SEC = int(os.getenv("AI_REQUEST_BUDGET_SEC", "60"))

# 부팅 시 DB 정리 옵션
PURGE_DEFAULT_ON_BOOT = os.getenv("PURGE_DEFAULT_ON_BOOT", "false").lower() == "true"
PURGE_SHORT_ON_BOOT   = os.getenv("PURGE_SHORT_ON_BOOT", "false").lower() == "true"

# 지원 카테고리 메타데이터
CATEGORY_META = {
    "programming": {
        "label": "프로그래밍",
        "group": "development",
        "group_label": "개발",
        "theme": "programming",
        "icon": "fa-code",
        "description": "Python, Java, JavaScript, 문법, 객체지향, 자료구조, 디버깅 등 개발 전반",
    },
    "web": {
        "label": "웹개발",
        "group": "development",
        "group_label": "개발",
        "theme": "web",
        "icon": "fa-globe",
        "description": "HTTP, REST API, 프론트엔드, 백엔드, 인증, 쿠키/세션 등 웹 개발 핵심",
    },
    "database": {
        "label": "데이터베이스",
        "group": "data",
        "group_label": "데이터",
        "theme": "database",
        "icon": "fa-database",
        "description": "SQL, 정규화, 인덱스, 트랜잭션, JOIN, 모델링, 튜닝 등 데이터베이스 핵심",
    },
    "network": {
        "label": "네트워크",
        "group": "infra",
        "group_label": "인프라",
        "theme": "network",
        "icon": "fa-network-wired",
        "description": "OSI, TCP/IP, 라우팅, 스위칭, DNS, DHCP, 로드밸런싱 등 네트워크 전반",
    },
    "linux": {
        "label": "리눅스",
        "group": "infra",
        "group_label": "인프라",
        "theme": "linux",
        "icon": "fa-linux",
        "description": "명령어, 파일 시스템, 권한, 서비스, 프로세스, 로그 분석 등 리눅스 운영 전반",
    },
    "cloud": {
        "label": "클라우드",
        "group": "infra",
        "group_label": "인프라",
        "theme": "cloud",
        "icon": "fa-cloud",
        "description": "AWS, Azure, GCP, 가상화, 스토리지, IaC, 모니터링 등 클라우드 운영 전반",
    },
    "kubernetes": {
        "label": "쿠버네티스",
        "group": "infra",
        "group_label": "인프라",
        "theme": "kubernetes",
        "icon": "fa-dharmachakra",
        "description": "컨테이너, Pod, Deployment, Service, Ingress, 롤링업데이트, 장애 대응 등 실무 중심",
    },
    "security": {
        "label": "보안",
        "group": "security",
        "group_label": "보안",
        "theme": "security",
        "icon": "fa-shield-halved",
        "description": "인증, 인가, 암호화, 취약점, 네트워크 보안, 시스템 보안 등 보안 기초 전반",
    },
}
QUESTION_STYLE_META = {
    "concept": {
        "label": "개념형",
        "description": "핵심 개념과 정의를 점검하는 기본형 문제",
    },
    "practical": {
        "label": "실무형",
        "description": "운영, 설계, 장애 대응처럼 현업 맥락을 담은 문제",
    },
    "cert": {
        "label": "자격시험 스타일",
        "description": "자격시험 분위기를 참고한 시나리오형 객관식 문제",
    },
    "mixed": {
        "label": "형식 혼합",
        "description": "개념형, 실무형, 자격시험 스타일을 섞어서 출제",
    },
}
LEGACY_CATEGORY_ALIASES = {
    "network": "network",
    "infra": "cloud",
    "linux": "linux",
    "network_general": "network",
    "network_security": "security",
    "network_services": "network",
    "infra_general": "cloud",
    "infra_cloud": "cloud",
    "infra_kubernetes": "kubernetes",
    "linux_general": "linux",
    "linux_admin": "linux",
    "linux_troubleshooting": "linux",
}
VALID_CATEGORIES = set(CATEGORY_META.keys())
VALID_QUESTION_STYLES = set(QUESTION_STYLE_META.keys())
CATEGORY_LABEL   = {
    **{key: meta["label"] for key, meta in CATEGORY_META.items()},
    "infra": "인프라",
    "all": "전체혼합",
    "mixed": "전체혼합",
    "network_general": "네트워크",
    "network_security": "보안",
    "network_services": "네트워크",
    "infra_general": "클라우드",
    "infra_cloud": "클라우드",
    "infra_kubernetes": "쿠버네티스",
    "linux_general": "리눅스",
    "linux_admin": "리눅스",
    "linux_troubleshooting": "리눅스",
}
QUESTION_STYLE_LABEL = {key: meta["label"] for key, meta in QUESTION_STYLE_META.items()}
# 배치당 최대 문제 수 (Gemini 1회 요청)
# 너무 많으면 응답이 길어져 파싱 오류 or 타임아웃 위험
GEMINI_BATCH_MAX = 5

KST   = timezone(timedelta(hours=9))
KO_RE = re.compile(r"[\uac00-\ud7a3]")


# ──────────────────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────────────────

def now_kst_naive():
    """KST 현재 시각 (tzinfo 없는 naive datetime)"""
    return datetime.now(KST).replace(tzinfo=None)


def is_korean_text(text):
    """한글 문자가 1개 이상 포함되어 있으면 True"""
    return bool(KO_RE.search(str(text or "")))


def _is_tcp_open(host, port, timeout=1.0):
    """TCP 포트가 열려있는지 빠르게 확인 (Gemini 네트워크 도달 가능 여부 사전 체크)"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _normalize_text(value):
    """연속 공백 → 단일 공백 정규화"""
    return " ".join(str(value or "").split())


def _normalize_category_key(value):
    """레거시 카테고리를 현재 세부 카테고리 키로 정규화"""
    key = _normalize_text(value).lower()
    return LEGACY_CATEGORY_ALIASES.get(key, key)


def _normalize_question_style(value):
    """문제 형식을 표준 키로 정규화"""
    key = _normalize_text(value).lower()
    return key if key in VALID_QUESTION_STYLES else "mixed"


# 문제 텍스트에서 "(변형 1)" 같은 자동 생성 접미사 제거용 정규식
VARIANT_SUFFIX_RE    = re.compile(r"\s*\([^)]*\d+[^)]*\)\s*$")
TRAILING_BRACKET_RE  = re.compile(r"\(\s*\d+\s*\)\s*$")


def _sanitize_question_text(value):
    """변형 접미사 제거 후 공백 정규화 (중복 해시 판별 일관성 확보)"""
    text = _normalize_text(value)
    text = VARIANT_SUFFIX_RE.sub("", text)
    text = TRAILING_BRACKET_RE.sub("", text)
    return _normalize_text(text)


def _question_hash(category, question):
    """카테고리 + 문제 텍스트 기반 SHA-256 해시 (중복 문제 식별용)"""
    base = f"{_normalize_text(category).lower()}|{_sanitize_question_text(question).lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────
# DB 연결 설정
# MySQL 우선, 연결 불가 시 SQLite fallback (로컬/개발 환경)
# k8s 환경에서 'db' 호스트명 미등록 시 localhost로 fallback
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


# MySQL 연결 시마다 utf8mb4 강제 (한글 깨짐 방지)
@event.listens_for(Engine, "connect")
def set_mysql_utf8mb4(dbapi_connection, _):
    try:
        cur = dbapi_connection.cursor()
        cur.execute("SET NAMES utf8mb4")
        cur.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# ORM 모델
# ──────────────────────────────────────────────────────────

class Question(db.Model):
    """문제 테이블: AI 생성 문제 저장"""
    __tablename__ = "questions"

    id             = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category       = db.Column(db.String(50), nullable=False, index=True)
    question_style = db.Column(db.String(20), nullable=True, index=True)
    question       = db.Column(db.Text, nullable=False)
    choice_a       = db.Column(db.Text, nullable=False)
    choice_b       = db.Column(db.Text, nullable=False)
    choice_c       = db.Column(db.Text, nullable=False)
    choice_d       = db.Column(db.Text, nullable=False)
    answer         = db.Column(db.String(1), nullable=False)
    explanation    = db.Column(db.Text, nullable=True)
    question_hash  = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at     = db.Column(db.DateTime, default=now_kst_naive, nullable=False, index=True)

    def to_dict(self, hide_answer=True):
        data = {
            "id": self.id,
            "category": self.category,
            "category_label": CATEGORY_LABEL.get(self.category, self.category),
            "question_style": self.question_style or "mixed",
            "question_style_label": QUESTION_STYLE_LABEL.get(self.question_style or "mixed", self.question_style or "mixed"),
            "question": self.question,
            "choices": {"A": self.choice_a, "B": self.choice_b, "C": self.choice_c, "D": self.choice_d},
        }
        if not hide_answer:
            data["answer"]      = self.answer
            data["explanation"] = self.explanation
        return data


class User(db.Model):
    """사용자 테이블: 이름 기준으로 식별"""
    __tablename__ = "users"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name       = db.Column(db.String(100), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=now_kst_naive, nullable=False)


class QuizAttempt(db.Model):
    """풀이 이력 테이블: 채점 결과 및 답안 JSON 저장"""
    __tablename__ = "quiz_attempts"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category      = db.Column(db.String(50), nullable=False)
    total         = db.Column(db.Integer, nullable=False)
    correct       = db.Column(db.Integer, nullable=False)
    wrong         = db.Column(db.Integer, nullable=False)
    score_percent = db.Column(db.Float, nullable=False)
    answers_json  = db.Column(db.Text, nullable=True)
    created_at    = db.Column(db.DateTime, default=now_kst_naive, nullable=False)


# ──────────────────────────────────────────────────────────
# DB 스키마 관리
# ──────────────────────────────────────────────────────────

def _ensure_questions_created_at_column():
    """created_at 컬럼이 없는 구버전 DB에 자동으로 컬럼 추가"""
    driver = str(db.engine.url.drivername)
    if "mysql" in driver:
        exists = db.session.execute(db.text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:s AND TABLE_NAME='questions' AND COLUMN_NAME='created_at'"
        ), {"s": DB_NAME}).scalar() or 0
        if not int(exists):
            try:
                db.session.execute(db.text(
                    "ALTER TABLE questions ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ))
                db.session.commit()
            except Exception as e:
                if "Duplicate column" in str(e) or "1060" in str(e):
                    db.session.rollback()
                else:
                    raise
    elif "sqlite" in driver:
        rows  = db.session.execute(db.text("PRAGMA table_info(questions)")).fetchall()
        names = {str(r[1]) for r in rows}
        if "created_at" not in names:
            db.session.execute(db.text(
                "ALTER TABLE questions ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ))
            db.session.commit()


def _ensure_questions_style_column():
    """question_style 컬럼이 없는 구버전 DB에 자동으로 컬럼 추가"""
    driver = str(db.engine.url.drivername)
    if "mysql" in driver:
        exists = db.session.execute(db.text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:s AND TABLE_NAME='questions' AND COLUMN_NAME='question_style'"
        ), {"s": DB_NAME}).scalar() or 0
        if not int(exists):
            try:
                db.session.execute(db.text(
                    "ALTER TABLE questions ADD COLUMN question_style VARCHAR(20) NULL"
                ))
                db.session.execute(db.text(
                    "ALTER TABLE questions ADD INDEX idx_question_style (question_style)"
                ))
                db.session.commit()
            except Exception as e:
                if ("Duplicate column" in str(e)
                        or "1060" in str(e)
                        or "Duplicate key name" in str(e)
                        or "1061" in str(e)):
                    db.session.rollback()
                else:
                    raise
    elif "sqlite" in driver:
        rows  = db.session.execute(db.text("PRAGMA table_info(questions)")).fetchall()
        names = {str(r[1]) for r in rows}
        if "question_style" not in names:
            db.session.execute(db.text(
                "ALTER TABLE questions ADD COLUMN question_style VARCHAR(20)"
            ))
            db.session.commit()


def _text_score(value):
    """공백/구두점 제외 문자 수 반환 (품질 판별용)"""
    return len(re.sub(r"[\s\-\_\.\,\(\)\[\]\:\/]+", "", _normalize_text(value)))


def _is_low_quality_question(row):
    """
    저품질 문제 판별:
    - 문제+해설이 너무 짧거나
    - 보기 3개 이상이 5자 이하인 경우
    """
    q_len  = _text_score(row.question)
    e_len  = _text_score(row.explanation)
    c_lens = [_text_score(row.choice_a), _text_score(row.choice_b),
              _text_score(row.choice_c), _text_score(row.choice_d)]
    short_choices  = sum(1 for x in c_lens if x <= 5)
    avg_choice_len = sum(c_lens) / 4

    if e_len <= 8 and q_len <= 22:
        return True
    if q_len <= 22 and short_choices >= 3 and avg_choice_len <= 9:
        return True
    return False


def _purge_low_quality_questions():
    """부팅 시 저품질 문제 일괄 삭제"""
    rows       = Question.query.all()
    delete_ids = [r.id for r in rows if _is_low_quality_question(r)]
    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
        db.session.commit()
    return len(delete_ids)


def _cleanup_legacy_variant_questions():
    """
    기존 DB의 '(변형 N)' 접미사 문제를 정제하고,
    정제 후 해시 충돌(중복)이 생기면 오래된 쪽 삭제
    """
    rows         = Question.query.order_by(Question.id.asc()).all()
    keep_by_hash = {}
    delete_ids   = []
    for row in rows:
        cleaned  = _sanitize_question_text(row.question)
        new_hash = _question_hash(row.category, cleaned)
        if new_hash in keep_by_hash:
            delete_ids.append(row.id)
            continue
        keep_by_hash[new_hash] = row.id
        if row.question      != cleaned:  row.question      = cleaned
        if row.question_hash != new_hash: row.question_hash = new_hash
    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
    db.session.commit()


def _migrate_legacy_categories():
    """구형 대분류 카테고리를 세부 카테고리의 general 버전으로 이관"""
    changed = 0
    for legacy, current in LEGACY_CATEGORY_ALIASES.items():
        changed += Question.query.filter_by(category=legacy).update(
            {"category": current}, synchronize_session=False
        )
        changed += QuizAttempt.query.filter_by(category=legacy).update(
            {"category": current}, synchronize_session=False
        )
    if changed:
        db.session.commit()
    return changed


def _default_bank_hashes():
    """
    초기 하드코딩 시드 문제의 해시 집합 반환
    PURGE_DEFAULT_ON_BOOT=true 시 이 해시들을 DB에서 삭제
    """
    base_by_category = {
        "network": [
            "OSI 7계층 모델에 대한 설명으로 올바른 것은?",
            "HTTPS 기본 포트는?",
            "IP주소 /24에서 사용 가능한 호스트 수는?",
            "DNS 기본 포트는?",
            "UDP와의 비교로 올바른 설명은?",
            "패킷으로 분리되어 전송하는 OSI 계층은?",
        ],
        "cloud": [
            "클라우드에서 확장성을 높이기 위해 가장 자주 사용하는 개념은?",
            "가상화의 주요 목적은?",
            "하이퍼바이저가 VM을 이용한 것은?",
            "Prometheus의 주요 기능은?",
            "Terraform의 주요 목적은?",
            "가용 영역을 여러 개 사용하는 주된 이유는?",
        ],
        "linux": [
            "현재 실행 중인 프로세스를 확인하는 명령어는?",
            "chmod 755 권한 설명으로 올바른 것은?",
            "디스크 사용량을 확인하고 상태를 확인하는 명령어는?",
            "시스템 로그 확인 명령어는?",
            "파일 시스템 상태 확인에 사용하는 명령어는?",
            "권한 변경 명령어는?",
            "SSH 접속에 로그인 시 사용하는 파일은?",
        ],
    }
    prefixes = ["기본 문제:", "심화 문제:", "응용 문제:", "기초 문제:", "고급 문제:", "실제 문제:"]
    out = set()
    for category, questions in base_by_category.items():
        for q in questions:
            out.add(_question_hash(category, q))
            for p in prefixes:
                out.add(_question_hash(category, f"{p} {q}"))
    return out


def _purge_default_questions():
    """하드코딩 시드 문제를 해시 기반으로 DB에서 제거"""
    purge_hashes = _default_bank_hashes()
    rows = Question.query.filter(Question.question_hash.in_(list(purge_hashes))).all()
    delete_ids = [r.id for r in rows]
    if delete_ids:
        Question.query.filter(Question.id.in_(delete_ids)).delete(synchronize_session=False)
        db.session.commit()
    return len(delete_ids)


def _ensure_schema():
    """앱 시작 시 스키마 생성 → 마이그레이션 → 정리 순서대로 실행"""
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            if "already exists" not in str(e).lower():
                raise
        _ensure_questions_created_at_column()
        _ensure_questions_style_column()
        _migrate_legacy_categories()
        _cleanup_legacy_variant_questions()
        purged = 0
        if PURGE_DEFAULT_ON_BOOT:
            purged = _purge_default_questions()
        if PURGE_SHORT_ON_BOOT:
            _purge_low_quality_questions()
        return purged


# ──────────────────────────────────────────────────────────
# JSON 파싱 유틸
# ──────────────────────────────────────────────────────────

def _extract_json_text(value):
    """마크다운 코드펜스(```json ... ```) 제거 후 순수 JSON 텍스트 반환"""
    text = _normalize_text(value)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def _safe_parse_questions_json(raw_text):
    """
    JSON 파싱 3단계 시도:
    1. 표준 json.loads
    2. 중괄호 경계 탐색 후 파싱
    3. trailing comma 제거 후 파싱
    """
    text = _extract_json_text(raw_text)
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    if start != -1:
        depth, end, in_string, escaped = 0, -1, False, False
        for idx, ch in enumerate(text[start:], start=start):
            if in_string:
                if escaped:       escaped = False
                elif ch == "\\": escaped = True
                elif ch == '"':  in_string = False
                continue
            if ch == '"':    in_string = True; continue
            if ch == "{":    depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0: end = idx; break
        if end != -1:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass

    normalized = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(normalized)


# ──────────────────────────────────────────────────────────
# Gemini API 유틸
# ──────────────────────────────────────────────────────────

def _normalize_model_name(model_name):
    """'models/gemini-xxx:generateContent' → 'gemini-xxx' 로 정규화"""
    m = _normalize_text(model_name)
    if not m: return ""
    if m.endswith(":generateContent"): m = m[:-len(":generateContent")]
    if "/" in m: m = m.split("/")[-1]
    return m


def _normalized_gemini_base_url():
    """환경변수 URL 정규화: 끝 슬래시 제거, /models 경로 보완"""
    base = _normalize_text(GEMINI_API_URL).rstrip("/")
    if not base:
        return "https://generativelanguage.googleapis.com/v1beta/models"
    if "/models" not in base:
        base = f"{base}/models"
    return base


def _gemini_http_post(url: str, post_data: bytes) -> tuple:
    """
    Gemini API에 HTTP POST 요청.
    핵심: 연결 후 소켓에 GEMINI_TIMEOUT을 즉시 설정하고 send/read.
    이렇게 해야 실제 응답 대기에 GEMINI_TIMEOUT이 적용됨.

    Returns: (status_code, response_body_str)
    """
    import http.client as _http_client

    parsed = urllib.parse.urlparse(url)
    host   = parsed.netloc
    path   = parsed.path + ("?" + parsed.query if parsed.query else "")

    # 연결 timeout=10초 (TCP handshake만), 이후 소켓 timeout은 GEMINI_TIMEOUT으로 교체
    conn = _http_client.HTTPSConnection(host, timeout=10)
    conn.connect()                          # 먼저 연결 확립
    conn.sock.settimeout(GEMINI_TIMEOUT)    # 연결 후 즉시 read timeout 교체 (핵심 수정)
    conn.request("POST", path, body=post_data, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw  = resp.read().decode("utf-8")
    conn.close()
    return resp.status, raw


def _extract_gemini_error_message(status: int, raw: str) -> str:
    """Return a compact Gemini error message from a raw error body."""
    try:
        payload = json.loads(raw or "{}")
    except Exception:
        payload = {}

    err = payload.get("error") or {}
    message = _normalize_text(err.get("message") or raw or f"HTTP {status}")
    return message[:300]


# ──────────────────────────────────────────────────────────
# Gemini 문제 생성 메인 함수
# ──────────────────────────────────────────────────────────

def _call_gemini_generate_questions(
    category: str,
    count: int,
    difficulty: str,
    question_style: str = "mixed",
    _fill_round: int = 0,
    exclude_questions: list | None = None,
) -> list:
    """
    Gemini API로 한국어 객관식 문제를 생성해 리스트로 반환.

    - count > GEMINI_BATCH_MAX(5) 이면 배치로 나눠 재귀 호출
    - 429 발생 시: retryDelay 대기 후 최대 3회 재시도
    - 최종 실패 시 RuntimeError raise
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    # ── 배치 분할 ───────────────────────────────────────────
    if count > GEMINI_BATCH_MAX:
        results = []
        seen = set()
        attempts = 0
        max_attempts = max(4, math.ceil(count / GEMINI_BATCH_MAX) * 3)

        while len(results) < count and attempts < max_attempts:
            attempts += 1
            remaining = count - len(results)
            batch_count = min(remaining, GEMINI_BATCH_MAX)
            print(f"[Gemini] 배치 요청: category={category} count={batch_count} difficulty={difficulty}", flush=True)
            try:
                batch_excludes = list(exclude_questions or [])
                batch_excludes.extend(r["question"] for r in results)
                rows = _call_gemini_generate_questions(
                    category,
                    batch_count,
                    difficulty,
                    question_style=question_style,
                    _fill_round=_fill_round,
                    exclude_questions=batch_excludes,
                )
            except Exception as e:
                if results:
                    print(f"[Gemini] partial batch kept category={category} collected={len(results)} error={e}", flush=True)
                    break
                raise
            before_len = len(results)
            for r in rows:
                key = _question_hash(r["category"], r["question"])
                if key not in seen:
                    seen.add(key)
                    results.append(r)
                    if len(results) >= count:
                        break
            if len(results) == before_len:
                print(f"[Gemini] no new rows added category={category} attempt={attempts}", flush=True)
                break
        return results[:count]

    # ── 프롬프트 구성 ────────────────────────────────────────
    prompt = (
        f"Category: {CATEGORY_LABEL.get(category, category)} ({category})\n"
        f"Difficulty: {difficulty}\n"
        f"QuestionStyle: {QUESTION_STYLE_LABEL.get(question_style, question_style)} ({question_style})\n"
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
        "- Keep explanation concise: why correct and why other choices are wrong (max 3 sentences).\n"
        "- Keep each choice text under 20 words.\n"
        "- Even if the topic includes English commands, question and explanation must be in Korean.\n"
    )
    if question_style == "concept":
        prompt += (
            "- Focus on core concepts, terminology, and direct understanding checks.\n"
            "- Keep stems concise and avoid overlong operational scenarios.\n"
        )
    elif question_style == "practical":
        prompt += (
            "- Focus on real-world operations, troubleshooting, design decisions, and incident handling.\n"
            "- Prefer realistic workplace context over textbook wording.\n"
        )
    elif question_style == "cert":
        prompt += (
            "- Make questions feel like polished mock certification questions without copying any real exam text.\n"
            "- Prefer scenario-based stems with carefully balanced distractors.\n"
        )
    else:
        prompt += "- Mix concept checks, practical cases, and certification-style scenarios evenly.\n"
    if exclude_questions:
        exclude_lines = "\n".join(
            f"- {q[:160]}" for q in exclude_questions if _normalize_text(q)
        )
        if exclude_lines:
            prompt += (
                "Avoid generating questions that are the same as or very similar to these stems:\n"
                f"{exclude_lines}\n"
            )

    payload = {
        "system_instruction": {
            "parts": [{
                "text": (
                    'Return exactly one JSON object. '
                    'Schema: {"questions":[{"question":"...","choices":{"A":"...","B":"...","C":"...","D":"..."},"answer":"A","explanation":"..."}]}. '
                    'All output text must be Korean. Use realistic exam tone and avoid any markdown/code fences.'
                )
            }]
        },
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    # ── 모델 후보 목록 구성 ──────────────────────────────────
    model_candidates = []
    for m in [GEMINI_MODEL] + [x.strip() for x in GEMINI_MODEL_CANDIDATES.split(",") if x.strip()]:
        m = _normalize_model_name(m)
        if m and m not in model_candidates:
            model_candidates.append(m)

    base_url  = _normalized_gemini_base_url()
    post_data = json.dumps(payload).encode("utf-8")
    body      = None
    errors    = []

    # ── 모델별 시도 (429 시 retryDelay 대기 후 최대 3회 재시도) ──
    for model_name in model_candidates:
        url = f"{base_url}/{model_name}:generateContent?{urllib.parse.urlencode({'key': GEMINI_API_KEY})}"

        try:
            status, raw = _gemini_http_post(url, post_data)
        except Exception as e:
            errors.append(f"model={model_name} {e}")
            continue  # 네트워크 오류면 다음 모델 후보 시도

        print(f"[Gemini] model={model_name} status={status}", flush=True)

        if status == 200:
            body = json.loads(raw)
            break  # 성공

        message = _extract_gemini_error_message(status, raw)
        errors.append(f"HTTP {status} model={model_name} {message}")
        if status in (401, 403, 404, 429):
            break  # 권한/모델/쿼터 오류는 같은 요청에서 오래 붙잡지 않음

        # 그 외 5xx 등은 다른 후보 모델이 있으면 다음 후보로 진행
        continue


    # ── 최종 실패 처리 ───────────────────────────────────────
    if body is None:
        if any("HTTP 429" in e for e in errors):
            detail = next((e for e in errors if "HTTP 429" in e), errors[-1] if errors else "")
            raise RuntimeError(f"AI 요청 한도를 초과했습니다. {detail}")
        if any("403" in e for e in errors):
            raise RuntimeError("AI API 키 권한 오류입니다. (403 Forbidden)")
        if any("404" in e for e in errors):
            raise RuntimeError("AI 모델을 찾을 수 없습니다. (404 Not Found)")
        if any("503" in e for e in errors):
            raise RuntimeError("AI 서버 일시 과부하. 잠시 후 다시 시도해주세요. (503)")
        raise RuntimeError(errors[-1] if errors else "AI 문제 생성 실패")

    # ── 응답 파싱 및 검증 ────────────────────────────────────
    candidates = body.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {body.get('promptFeedback', {})}")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise RuntimeError("Gemini returned empty content")

    parsed = _safe_parse_questions_json(parts[0].get("text", ""))

    out = []
    out_seen = set()
    for item in parsed.get("questions", []):
        choices = item.get("choices", {})
        answer  = str(item.get("answer", "")).upper()
        q_text  = _sanitize_question_text(item.get("question", ""))
        if answer not in {"A", "B", "C", "D"}:
            continue
        if not is_korean_text(q_text):
            continue
        if not all(k in choices and _normalize_text(choices[k]) for k in ("A", "B", "C", "D")):
            continue
        q_hash = _question_hash(category, q_text)
        if q_hash in out_seen:
            continue
        out_seen.add(q_hash)
        out.append({
            "category":    category,
            "question_style": question_style,
            "question":    q_text,
            "choices":     {k: _normalize_text(choices[k]) for k in ("A", "B", "C", "D")},
            "answer":      answer,
            "explanation": _normalize_text(item.get("explanation", "")),
        })

    if not out:
        raise RuntimeError("Gemini returned no valid Korean questions")
    if len(out) < count and _fill_round < 2:
        missing = count - len(out)
        try:
            extra = _call_gemini_generate_questions(
                category,
                missing,
                difficulty,
                question_style=question_style,
                _fill_round=_fill_round + 1,
                exclude_questions=list(exclude_questions or []) + [r["question"] for r in out],
            )
        except Exception as e:
            print(f"[Gemini] partial single batch kept category={category} collected={len(out)} error={e}", flush=True)
        else:
            for row in extra:
                q_hash = _question_hash(row["category"], row["question"])
                if q_hash in out_seen:
                    continue
                out_seen.add(q_hash)
                out.append(row)
                if len(out) >= count:
                    break

    if len(out) < count:
        print(f"[Gemini] short result category={category} requested={count} got={len(out)} round={_fill_round}", flush=True)
    return out[:count]


# ──────────────────────────────────────────────────────────
# DB 저장 유틸
# ──────────────────────────────────────────────────────────

def _save_generated_questions(rows: list) -> tuple:
    """
    AI 생성 문제를 DB에 저장.
    - 한국어 아닌 문제, 중복 해시 → skip
    Returns: (inserted_count, skipped_count)
    """
    inserted, skipped = 0, 0
    prepared, batch_hashes = [], set()

    for row in rows:
        cleaned = _sanitize_question_text(row["question"])
        if not is_korean_text(cleaned):
            skipped += 1
            continue
        q_hash = _question_hash(row["category"], cleaned)
        if q_hash in batch_hashes:
            skipped += 1
            continue
        batch_hashes.add(q_hash)
        prepared.append({
            "category": row["category"],
            "question_style": _normalize_question_style(row.get("question_style", "mixed")),
            "question": cleaned,
            "choice_a": row["choices"]["A"],
            "choice_b": row["choices"]["B"],
            "choice_c": row["choices"]["C"],
            "choice_d": row["choices"]["D"],
            "answer": row["answer"],
            "explanation": row["explanation"],
            "question_hash": q_hash,
        })

    if not prepared:
        return inserted, skipped

    with db.session.no_autoflush:
        existing_hashes = {
            value for (value,) in db.session.query(Question.question_hash)
            .filter(Question.question_hash.in_(list(batch_hashes))).all()
        }

    to_insert = [row for row in prepared if row["question_hash"] not in existing_hashes]
    skipped += len(prepared) - len(to_insert)

    for row in to_insert:
        db.session.add(Question(
            category=row["category"], question_style=row["question_style"], question=row["question"],
            choice_a=row["choice_a"], choice_b=row["choice_b"],
            choice_c=row["choice_c"], choice_d=row["choice_d"],
            answer=row["answer"], explanation=row["explanation"],
            question_hash=row["question_hash"], created_at=now_kst_naive(),
        ))

    if not to_insert:
        return inserted, skipped

    try:
        db.session.commit()
        inserted = len(to_insert)
        return inserted, skipped
    except (IntegrityError, OperationalError):
        db.session.rollback()

    for row in to_insert:
        try:
            with db.session.no_autoflush:
                exists = Question.query.filter_by(question_hash=row["question_hash"]).first()
            if exists:
                skipped += 1
                continue
            db.session.add(Question(
                category=row["category"], question_style=row["question_style"], question=row["question"],
                choice_a=row["choice_a"], choice_b=row["choice_b"],
                choice_c=row["choice_c"], choice_d=row["choice_d"],
                answer=row["answer"], explanation=row["explanation"],
                question_hash=row["question_hash"], created_at=now_kst_naive(),
            ))
            db.session.commit()
            inserted += 1
        except (IntegrityError, OperationalError):
            db.session.rollback()
            skipped += 1

    return inserted, skipped


# ──────────────────────────────────────────────────────────
# 출제 비율 결정
# DB 문제 수가 많을수록 AI 호출 비중 감소
# ──────────────────────────────────────────────────────────

def _get_source_ratio(db_count: int, limit: int) -> tuple:
    """
    DB 문제 수 기준 (ai_need, db_need) 반환:
      < 50   → AI 100%
      50~99  → AI 50% + DB 50%
      100~149→ AI 30% + DB 70%
      150+   → DB 100% (AI 호출 없음)
    """
    if db_count >= 150:
        return 0, limit
    elif db_count >= 100:
        ai_n = max(1, round(limit * 0.3))
        return ai_n, limit - ai_n
    elif db_count >= 50:
        ai_n = max(1, round(limit * 0.5))
        return ai_n, limit - ai_n
    else:
        return limit, 0


# ──────────────────────────────────────────────────────────
# 사용자 기출 문제 제외 유틸
# ──────────────────────────────────────────────────────────

def _get_recent_user_question_hashes(user_name: str, limit_attempts: int = 20) -> set:
    """최근 N회 시도에서 풀었던 문제 해시 집합 반환 (재출제 방지)"""
    name = _normalize_text(user_name)
    if not name: return set()
    user = User.query.filter_by(name=name).first()
    if not user: return set()

    attempts = (QuizAttempt.query.filter_by(user_id=user.id)
                .order_by(QuizAttempt.id.desc()).limit(limit_attempts).all())
    used = set()
    for a in attempts:
        try:
            rows = json.loads(a.answers_json or "[]")
        except Exception:
            rows = []
        for r in rows:
            q_cat  = str(r.get("category") or a.category or "").strip().lower()
            q_text = str(r.get("question") or "").strip()
            if q_cat and q_text:
                used.add(_question_hash(q_cat, q_text))
    return used


# ──────────────────────────────────────────────────────────
# 앱 시작 시 스키마 초기화
# ──────────────────────────────────────────────────────────
PURGED_DEFAULT_COUNT = _ensure_schema()


# ══════════════════════════════════════════════════════════
# API 라우트
# ══════════════════════════════════════════════════════════

@app.route("/api/admin/purge-questions", methods=["DELETE"])
def purge_all_questions():
    """[관리자] DB의 모든 문제 삭제"""
    try:
        count = Question.query.count()
        Question.query.delete()
        db.session.commit()
        return jsonify({"deleted": count, "message": f"{count}개 문제가 삭제되었습니다."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/health", methods=["GET"])
def health():
    """DB 연결 상태 확인"""
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "db": "connected", "db_mode": ACTIVE_DB}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500


@app.route("/api/ai/health", methods=["GET"])
def ai_health():
    """Gemini API 키 설정 및 네트워크 도달 가능 여부 확인"""
    check      = request.args.get("check", "0") == "1"
    network_ok = _is_tcp_open("generativelanguage.googleapis.com", 443, timeout=3.0)
    payload    = {
        "provider": "gemini", "configured": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL, "network_reachable": network_ok,
        "network_target": "generativelanguage.googleapis.com:443",
        "purged_default_count": PURGED_DEFAULT_COUNT,
    }
    if not check:
        return jsonify(payload), 200
    if not GEMINI_API_KEY:
        payload.update({"reachable": False, "error": "GEMINI_API_KEY is not configured"})
        return jsonify(payload), 200
    if not network_ok:
        payload.update({"reachable": False, "error": "NETWORK_BLOCKED"})
        return jsonify(payload), 200
    try:
        _call_gemini_generate_questions("programming", 1, "mixed")
        payload.update({"reachable": True})
    except Exception as e:
        payload.update({"reachable": False, "error": str(e)})
    return jsonify(payload), 200


@app.route("/api/ai/questions", methods=["POST"])
def generate_questions():
    """[AI 문제 생성] 카테고리/개수/난이도로 문제를 생성해 DB 저장 후 반환"""
    data       = request.get_json(silent=True) or {}
    category   = _normalize_category_key(data.get("category", ""))
    difficulty = str(data.get("difficulty", "mixed")).strip().lower()
    question_style = _normalize_question_style(data.get("question_style", "mixed"))
    try:
        count = int(data.get("count", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "count must be integer"}), 400
    count = max(1, min(count, 20))
    if category not in VALID_CATEGORIES:
        return jsonify({"error": f"category must be one of {', '.join(sorted(VALID_CATEGORIES))}"}), 400

    try:
        rows = _call_gemini_generate_questions(category, count, difficulty, question_style=question_style)
    except RuntimeError as e:
        err = str(e)
        status = 429 if "429" in err else 500
        return jsonify({"error": err}), status

    inserted, skipped = _save_generated_questions(rows)
    current_db_count  = Question.query.filter_by(category=category).count()

    unique_rows, seen_hash = [], set()
    for row in rows:
        qh = _question_hash(row["category"], row["question"])
        if qh in seen_hash: continue
        seen_hash.add(qh)
        row["question"] = _sanitize_question_text(row["question"])
        unique_rows.append(row)
        if len(unique_rows) >= count: break

    return jsonify({
        "provider": "gemini", "category": category,
        "question_style": question_style,
        "inserted_count": inserted, "duplicate_skipped_count": skipped,
        "db_total_count": current_db_count, "questions": unique_rows,
    }), 200


@app.route("/api/categories", methods=["GET"])
def get_categories():
    """카테고리별 DB 저장 문제 수 반환"""
    rows      = db.session.query(Question.category, db.func.count(Question.id).label("count")).group_by(Question.category).all()
    count_map = {r.category: int(r.count) for r in rows}
    categories = []
    for c in sorted(VALID_CATEGORIES):
        meta = CATEGORY_META[c]
        categories.append({
            "name": c,
            "label": meta["label"],
            "group": meta["group"],
            "group_label": meta["group_label"],
            "theme": meta["theme"],
            "icon": meta["icon"],
            "description": meta["description"],
            "count": count_map.get(c, 0),
        })
    return jsonify({"categories": categories}), 200


@app.route("/api/questions/<category>", methods=["GET"])
def get_questions(category):
    """
    [문제 출제] DB 문제 수 기반으로 AI/DB 비율 결정 후 출제.
    - DB 부족 시 AI로 신규 생성 → DB 저장 → 출제
    - AI 실패 시 DB 문제로 보충
    """
    category = _normalize_category_key(category)
    if category not in VALID_CATEGORIES:
        return jsonify({"error": "invalid category"}), 400

    try:
        limit = min(max(int(request.args.get("limit", 10)), 1), 50)
    except ValueError:
        return jsonify({"error": "limit must be integer"}), 400

    difficulty     = request.args.get("difficulty", "mixed").strip().lower()
    question_style = _normalize_question_style(request.args.get("style", "mixed"))
    shuffle        = request.args.get("shuffle", "1") == "1"
    user_name      = request.args.get("user", "").strip()
    exclude_hashes = _get_recent_user_question_hashes(user_name)

    # DB 풀 확인 → AI/DB 비율 결정
    query = Question.query.filter_by(category=category)
    if question_style != "mixed":
        query = query.filter_by(question_style=question_style)
    ko_questions  = [q for q in query.all() if is_korean_text(q.question)]
    db_pool_count = len(ko_questions)
    ai_need, _    = _get_source_ratio(db_pool_count, limit)

    selected, seen_selected = [], set()
    ai_count, ai_error, inserted, skipped = 0, "", 0, 0

    # ── AI 출제 ──────────────────────────────────────────────
    if ai_need > 0:
        if not _is_tcp_open("generativelanguage.googleapis.com", 443, timeout=3.0):
            ai_error = "NETWORK_BLOCKED: Gemini에 도달할 수 없습니다. DB 문제로 대체합니다."
            ai_need  = 0
        else:
            generated_rows, seen_hash = [], set()
            ai_started_at, attempts   = time.monotonic(), 0
            max_attempts = max(5, math.ceil(ai_need / GEMINI_BATCH_MAX) * 3)

            while (len(generated_rows) < ai_need
                   and attempts < max_attempts
                   and (time.monotonic() - ai_started_at) < AI_REQUEST_BUDGET_SEC):
                attempts += 1
                batch_size = min(max(ai_need - len(generated_rows), 1), GEMINI_BATCH_MAX)
                try:
                    batch = _call_gemini_generate_questions(
                        category,
                        batch_size,
                        difficulty,
                        question_style=question_style,
                        exclude_questions=[r["question"] for r in generated_rows],
                    )
                    for row in batch:
                        qh = _question_hash(row["category"], row["question"])
                        if qh not in seen_hash:
                            seen_hash.add(qh)
                            generated_rows.append(row)
                        if len(generated_rows) >= ai_need:
                            break
                except Exception as e:
                    ai_error = str(e)
                    # 429든 다른 오류든 현재까지 생성된 것 저장 후 DB 보충
                    print(f"[Gemini] AI 오류 발생: {ai_error} → DB 보충", flush=True)
                    break
                if not batch:
                    print(f"[Gemini] empty batch category={category} attempt={attempts}", flush=True)
                    break

            if generated_rows:
                inserted, skipped = _save_generated_questions(generated_rows)
                gen_hashes = list({_question_hash(r["category"], r["question"]) for r in generated_rows})
                ai_rows_query = Question.query.filter_by(category=category)
                if question_style != "mixed":
                    ai_rows_query = ai_rows_query.filter_by(question_style=question_style)
                ai_rows = (ai_rows_query
                           .filter(Question.question_hash.in_(gen_hashes))
                           .order_by(Question.id.desc()).all())
                for q in ai_rows:
                    key = _question_hash(q.category, q.question)
                    if key in seen_selected: continue
                    selected.append(q); seen_selected.add(key); ai_count += 1
                    if len(selected) >= limit: break

    # ── DB 보충 출제 ─────────────────────────────────────────
    if len(selected) < limit:
        query = Question.query.filter_by(category=category)
        if question_style != "mixed":
            query = query.filter_by(question_style=question_style)
        ko_questions = [q for q in query.all() if is_korean_text(q.question)]
        db_pool_count = len(ko_questions)
        fresh = [q for q in ko_questions
                 if _question_hash(q.category, q.question) not in seen_selected
                 and _question_hash(q.category, q.question) not in exclude_hashes]
        reuse = [q for q in ko_questions
                 if _question_hash(q.category, q.question) not in seen_selected
                 and _question_hash(q.category, q.question) in exclude_hashes]
        if shuffle:
            random.shuffle(fresh); random.shuffle(reuse)
        for q in fresh + reuse:
            key = _question_hash(q.category, q.question)
            if key in seen_selected: continue
            q.question = _sanitize_question_text(q.question)
            selected.append(q); seen_selected.add(key)
            if len(selected) >= limit: break

    if not selected:
        err_msg = ai_error or "출제할 문제가 없습니다. GEMINI_API_KEY를 확인하거나 잠시 후 다시 시도해주세요."
        status = 429 if "429" in err_msg else 502
        return jsonify({"error": err_msg, "category": category}), status

    if shuffle:
        random.shuffle(selected)

    db_count_used = max(0, len(selected) - ai_count)
    provider = "hybrid" if ai_count > 0 and db_count_used > 0 else ("gemini" if ai_count > 0 else "cache-db")

    return jsonify({
        "category": category, "source": provider, "provider": provider,
        "question_style": question_style,
        "total": len(selected), "requested": limit,
        "ai_count": ai_count, "db_count": db_count_used,
        "db_pool_size": db_pool_count, "warning": ai_error,
        "inserted_count": inserted, "duplicate_skipped_count": skipped,
        "questions": [q.to_dict(hide_answer=True) for q in selected],
    }), 200


@app.route("/api/questions/<category>/all", methods=["GET", "POST"])
def get_questions_with_answers(category):
    """[채점 후 리뷰] 정답/해설 포함 문제 상세 반환"""
    data = request.get_json(silent=True) or {}
    if not data.get("ids") and request.args.get("ids"):
        ids = [int(i) for i in request.args.get("ids").split(",") if i.strip().isdigit()]
    else:
        ids = data.get("ids", [])
    category  = _normalize_category_key(category)
    query     = Question.query.filter_by(category=category)
    if ids:
        query = query.filter(Question.id.in_(ids))
    questions = [q for q in query.all() if is_korean_text(q.question)]
    return jsonify({"category": category, "questions": [q.to_dict(hide_answer=False) for q in questions]}), 200


@app.route("/api/retry-wrong", methods=["POST"])
def retry_wrong_questions():
    """[오답 재출제] 이전 시도에서 틀린 문제 ID 목록으로 문제 반환"""
    data = request.get_json(silent=True) or {}
    ids  = data.get("ids", [])
    if not ids:
        return jsonify({"error": "ids field is required"}), 400
    questions = [q for q in Question.query.filter(Question.id.in_(ids)).all() if is_korean_text(q.question)]
    if not questions:
        return jsonify({"error": "해당 문제를 찾을 수 없습니다."}), 404
    random.shuffle(questions)
    return jsonify({
        "total": len(questions), "source": "retry", "provider": "cache-db",
        "questions": [q.to_dict(hide_answer=True) for q in questions],
    }), 200


@app.route("/api/questions/all/mixed", methods=["GET"])
def get_mixed_questions():
    """
    [카테고리 혼합 출제] 전체 카테고리에서 골고루 출제.
    DB 문제 수 기반으로 AI/DB 비율 결정 후 카테고리별 순서대로 AI 호출.
    """
    try:
        limit = min(max(int(request.args.get("limit", 10)), 1), 50)
    except ValueError:
        return jsonify({"error": "limit must be integer"}), 400

    difficulty     = request.args.get("difficulty", "mixed").strip().lower()
    question_style = _normalize_question_style(request.args.get("style", "mixed"))
    user_name      = request.args.get("user", "").strip()
    exclude_hashes = _get_recent_user_question_hashes(user_name)
    categories     = sorted(VALID_CATEGORIES)
    random.shuffle(categories)
    per_cat        = limit // len(categories)
    remainder      = limit % len(categories)
    selected, seen_selected = [], set()
    total_ai, total_inserted = 0, 0

    for i, cat in enumerate(categories):
        cat_limit     = per_cat + (1 if i < remainder else 0)
        if cat_limit <= 0:
            continue
        query = Question.query.filter_by(category=cat)
        if question_style != "mixed":
            query = query.filter_by(question_style=question_style)
        ko_questions  = [q for q in query.all() if is_korean_text(q.question)]
        db_pool_count = len(ko_questions)
        ai_need, _    = _get_source_ratio(db_pool_count, cat_limit)

        # ── AI 출제 ──────────────────────────────────────────
        if ai_need > 0 and _is_tcp_open("generativelanguage.googleapis.com", 443, timeout=3.0):
            try:
                rows, attempts = [], 0
                max_attempts = max(5, math.ceil(ai_need / GEMINI_BATCH_MAX) * 3)
                seen_hash = set()
                while len(rows) < ai_need and attempts < max_attempts:
                    attempts += 1
                    batch_size = min(max(ai_need - len(rows), 1), GEMINI_BATCH_MAX)
                    batch = _call_gemini_generate_questions(cat, batch_size, difficulty, question_style=question_style)
                    if not batch:
                        print(f"[mixed] empty batch cat={cat} attempt={attempts}", flush=True)
                        break
                    for row in batch:
                        key = _question_hash(row["category"], row["question"])
                        if key in seen_hash:
                            continue
                        seen_hash.add(key)
                        rows.append(row)
                        if len(rows) >= ai_need:
                            break
                ins, _ = _save_generated_questions(rows)
                total_inserted += ins
                gen_hashes = list({_question_hash(r["category"], r["question"]) for r in rows})
                ai_rows_query = Question.query.filter_by(category=cat)
                if question_style != "mixed":
                    ai_rows_query = ai_rows_query.filter_by(question_style=question_style)
                ai_rows = (ai_rows_query
                           .filter(Question.question_hash.in_(gen_hashes))
                           .order_by(Question.id.desc()).all())
                for q in ai_rows:
                    key = _question_hash(q.category, q.question)
                    if key in seen_selected: continue
                    selected.append(q); seen_selected.add(key); total_ai += 1
                    if sum(1 for s in selected if s.category == cat) >= cat_limit: break
            except Exception as e:
                print(f"[mixed] AI 오류 cat={cat}: {e}", flush=True)

        # ── DB 보충 ──────────────────────────────────────────
        cat_selected = sum(1 for s in selected if s.category == cat)
        if cat_selected < cat_limit:
            fresh = [q for q in ko_questions
                     if _question_hash(q.category, q.question) not in seen_selected
                     and _question_hash(q.category, q.question) not in exclude_hashes]
            reuse = [q for q in ko_questions
                     if _question_hash(q.category, q.question) not in seen_selected
                     and _question_hash(q.category, q.question) in exclude_hashes]
            random.shuffle(fresh); random.shuffle(reuse)
            for q in fresh + reuse:
                key = _question_hash(q.category, q.question)
                if key in seen_selected: continue
                q.question = _sanitize_question_text(q.question)
                selected.append(q); seen_selected.add(key)
                if sum(1 for s in selected if s.category == cat) >= cat_limit: break

    if len(selected) < limit:
        global_query = Question.query.filter(Question.category.in_(categories))
        if question_style != "mixed":
            global_query = global_query.filter_by(question_style=question_style)
        global_ko_questions = [q for q in global_query.all() if is_korean_text(q.question)]
        fresh = [q for q in global_ko_questions
                 if _question_hash(q.category, q.question) not in seen_selected
                 and _question_hash(q.category, q.question) not in exclude_hashes]
        reuse = [q for q in global_ko_questions
                 if _question_hash(q.category, q.question) not in seen_selected
                 and _question_hash(q.category, q.question) in exclude_hashes]
        random.shuffle(fresh)
        random.shuffle(reuse)
        for q in fresh + reuse:
            key = _question_hash(q.category, q.question)
            if key in seen_selected:
                continue
            q.question = _sanitize_question_text(q.question)
            selected.append(q)
            seen_selected.add(key)
            if len(selected) >= limit:
                break

    if not selected:
        return jsonify({"error": "출제할 문제가 없습니다."}), 502

    random.shuffle(selected)
    db_used  = max(0, len(selected) - total_ai)
    provider = "hybrid" if total_ai > 0 and db_used > 0 else ("gemini" if total_ai > 0 else "cache-db")

    return jsonify({
        "category": "mixed", "provider": provider,
        "question_style": question_style,
        "total": len(selected), "requested": limit,
        "ai_count": total_ai, "db_count": db_used,
        "inserted_count": total_inserted,
        "questions": [q.to_dict(hide_answer=True) for q in selected],
    }), 200


@app.route("/api/submit", methods=["POST"])
def submit_answers():
    """[답안 제출] 채점 후 QuizAttempt DB에 저장"""
    data = request.get_json() or {}
    if "answers" not in data:
        return jsonify({"error": "answers field is required"}), 400

    user_name     = _normalize_text(data.get("user_name", "익명"))[:100] or "익명"
    quiz_category = _normalize_text(data.get("category", ""))[:50]
    answers       = data["answers"]
    q_map         = {q.id: q for q in Question.query.filter(Question.id.in_([a["id"] for a in answers])).all()}

    results, correct_count = [], 0
    for a in answers:
        q = q_map.get(a["id"])
        if not q: continue
        selected   = a.get("selected", "").upper()
        is_correct = selected == q.answer.upper()
        if is_correct: correct_count += 1
        results.append({
            "id": q.id, "question": q.question, "category": q.category,
            "category_label": CATEGORY_LABEL.get(q.category, q.category),
            "choices": {"A": q.choice_a, "B": q.choice_b, "C": q.choice_c, "D": q.choice_d},
            "selected": selected, "answer": q.answer.upper(),
            "is_correct": is_correct, "explanation": q.explanation,
        })

    total         = len(results)
    score_percent = round(correct_count / total * 100, 1) if total else 0

    user = User.query.filter_by(name=user_name).first()
    if not user:
        user = User(name=user_name, created_at=now_kst_naive())
        db.session.add(user); db.session.flush()

    if not quiz_category and results:
        quiz_category = results[0]["category"]

    attempt = QuizAttempt(
        user_id=user.id, category=quiz_category or "mixed",
        total=total, correct=correct_count, wrong=total - correct_count,
        score_percent=score_percent, answers_json=json.dumps(results, ensure_ascii=False),
        created_at=now_kst_naive(),
    )
    db.session.add(attempt); db.session.commit()

    return jsonify({
        "total": total, "correct": correct_count, "wrong": total - correct_count,
        "score_percent": score_percent, "results": results,
        "attempt_id": attempt.id, "user_name": user_name,
    }), 200


@app.route("/api/history/<user_name>", methods=["GET"])
def get_user_history(user_name):
    """[풀이 이력] 사용자의 시도 목록 반환 (최신순)"""
    name = _normalize_text(user_name)
    user = User.query.filter_by(name=name).first()
    if not user:
        return jsonify({"user_name": name, "attempts": []}), 200

    limit    = min(max(int(request.args.get("limit", 20)), 1), 100)
    attempts = QuizAttempt.query.filter_by(user_id=user.id).order_by(QuizAttempt.created_at.desc()).limit(limit).all()
    return jsonify({
        "user_name":      user.name,
        "created_at_kst": user.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "attempts": [{
            "attempt_id": a.id, "category": a.category,
            "category_label": CATEGORY_LABEL.get(a.category, a.category),
            "total": a.total, "correct": a.correct, "wrong": a.wrong,
            "score_percent": a.score_percent,
            "created_at_kst": a.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        } for a in attempts],
    }), 200


@app.route("/api/history/<user_name>/<int:attempt_id>", methods=["GET"])
def get_attempt_detail(user_name, attempt_id):
    """[시도 상세] 특정 attempt의 답안 전체 반환 (리뷰 화면용)"""
    name    = _normalize_text(user_name)
    user    = User.query.filter_by(name=name).first()
    if not user:
        return jsonify({"error": "user not found"}), 404
    attempt = QuizAttempt.query.filter_by(id=attempt_id, user_id=user.id).first()
    if not attempt:
        return jsonify({"error": "attempt not found"}), 404

    try:
        results = json.loads(attempt.answers_json or "[]")
    except Exception:
        results = []
    for row in results:
        if row.get("category") and not row.get("category_label"):
            row["category_label"] = CATEGORY_LABEL.get(row["category"], row["category"])

    return jsonify({
        "attempt_id": attempt.id, "user_name": user.name,
        "category": attempt.category,
        "category_label": CATEGORY_LABEL.get(attempt.category, attempt.category),
        "total": attempt.total,
        "correct": attempt.correct, "wrong": attempt.wrong,
        "score_percent": attempt.score_percent,
        "created_at_kst": attempt.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
    }), 200


@app.route("/api/history/<user_name>/wrong-ids", methods=["GET"])
def get_user_wrong_ids(user_name):
    """[누적 오답] 사용자의 전체 이력에서 오답 문제 ID 중복 없이 수집"""
    name     = _normalize_text(user_name)
    user     = User.query.filter_by(name=name).first()
    if not user:
        return jsonify({"error": "user not found"}), 404

    category = _normalize_category_key(request.args.get("category", ""))
    limit    = min(max(int(request.args.get("limit", 50)), 1), 200)
    query    = QuizAttempt.query.filter_by(user_id=user.id)
    if category and category in VALID_CATEGORIES:
        query = query.filter_by(category=category)
    attempts = query.order_by(QuizAttempt.created_at.desc()).limit(limit).all()

    wrong_ids, seen = [], set()
    for attempt in attempts:
        try:
            results = json.loads(attempt.answers_json or "[]")
        except Exception:
            continue
        for r in results:
            if not r.get("is_correct") and r.get("id") and r["id"] not in seen:
                seen.add(r["id"]); wrong_ids.append(r["id"])

    return jsonify({
        "user_name": user.name, "category": category or "all",
        "wrong_count": len(wrong_ids), "wrong_ids": wrong_ids,
    }), 200


if __name__ == "__main__":
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)

