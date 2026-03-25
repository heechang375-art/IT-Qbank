"""
init_db.py - 데이터베이스 초기화 스크립트

역할:
  1. MySQL 연결 대기 (DB_WAIT_RETRIES 횟수만큼 재시도)
  2. 테이블 생성: questions / users / quiz_attempts
  3. 기존 문제의 중복 제거 및 question_hash 정규화
  4. 스키마 마이그레이션 (question_hash 컬럼이 없으면 ALTER TABLE 추가)

실행 시점:
  - Docker: entrypoint.sh에서 app.py 실행 전 자동 호출
  - 로컬 개발: python init_db.py 직접 실행
  - INIT_DB_SEED=true 일 때만 seed 데이터 삽입 (현재 AI API 전용이므로 seed 없음)
"""

import hashlib
import os
import re
import socket
import sys
import time

import pymysql
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────
# 환경 변수에서 DB 접속 정보 로드
# ──────────────────────────────────────────────────────────
DB_USER = os.getenv("DB_USER", "quizuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "quizpassword")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "quizdb")
INIT_DB_SEED = os.getenv("INIT_DB_SEED", "false").lower() == "true"

# Docker Compose에서 서비스명 'db'를 사용하지만 로컬에서는 없을 수 있으므로 fallback
if DB_HOST == "db":
    try:
        socket.gethostbyname("db")
    except socket.gaierror:
        DB_HOST = "localhost"


# ──────────────────────────────────────────────────────────
# 문제 텍스트 정규화 유틸리티
# 공백 제거, 변형 suffix 제거 → 동일 문제를 같은 해시로 처리
# ──────────────────────────────────────────────────────────
def normalize_text(value):
    return " ".join(str(value or "").split())


VARIANT_SUFFIX_RE = re.compile(r"\s*\([^)]*\d+[^)]*\)\s*$")
TRAILING_BRACKET_RE = re.compile(r"\(\s*\d+\s*\)\s*$")


def sanitize_question_text(value):
    text = normalize_text(value)
    text = VARIANT_SUFFIX_RE.sub("", text)
    text = TRAILING_BRACKET_RE.sub("", text)
    return normalize_text(text)


def question_hash(category, question):
    """카테고리 + 문제 텍스트로 SHA-256 해시 생성 (중복 판별 키)"""
    base = f"{normalize_text(category).lower()}|{sanitize_question_text(question).lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────
# MySQL 연결 대기 (컨테이너 기동 순서 문제 대응)
# DB_WAIT_RETRIES, DB_WAIT_DELAY 환경 변수로 조정 가능
# ──────────────────────────────────────────────────────────
def wait_for_db(max_retries=None, delay=None):
    if max_retries is None:
        max_retries = int(os.getenv("DB_WAIT_RETRIES", "12"))
    if delay is None:
        delay = int(os.getenv("DB_WAIT_DELAY", "2"))
    for i in range(max_retries):
        try:
            conn = pymysql.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                db=DB_NAME,
                charset="utf8mb4",
                connect_timeout=2,
                read_timeout=4,
                write_timeout=4,
            )
            conn.close()
            print("[OK] DB connected")
            return True
        except Exception as e:
            print(f"[{i + 1}/{max_retries}] waiting DB... ({e})")
            time.sleep(delay)
    print("[FAIL] DB connection failed")
    return False


def init_db():
    """DB 초기화 메인 함수: 테이블 생성 → 스키마 마이그레이션 → 중복 정리"""
    if not wait_for_db():
        sys.exit(1)

    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        db=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    with conn:
        with conn.cursor() as cur:
            # ── 테이블 생성 ──────────────────────────────────
            # questions: 문제 저장소 (카테고리/보기 A~D/정답/해설/해시)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS questions (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    category      VARCHAR(50) NOT NULL,
                    question_style VARCHAR(20) NULL,
                    question      TEXT NOT NULL,
                    choice_a      TEXT NOT NULL,
                    choice_b      TEXT NOT NULL,
                    choice_c      TEXT NOT NULL,
                    choice_d      TEXT NOT NULL,
                    answer        VARCHAR(1) NOT NULL,
                    explanation   TEXT,
                    question_hash VARCHAR(64),
                    INDEX idx_category (category),
                    INDEX idx_question_style (question_style),
                    UNIQUE KEY uq_question_hash (question_hash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """
            )

            # users: 사용자 테이블 (이름 기준으로 식별, 중복 불가)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    name       VARCHAR(100) NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_user_name (name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """
            )

            # quiz_attempts: 시도 이력 (채점 결과 + 답안 JSON 저장)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_attempts (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    user_id       INT NOT NULL,
                    category      VARCHAR(50) NOT NULL,
                    total         INT NOT NULL,
                    correct       INT NOT NULL,
                    wrong         INT NOT NULL,
                    score_percent FLOAT NOT NULL,
                    answers_json  LONGTEXT,
                    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_attempt_user_id (user_id),
                    CONSTRAINT fk_attempt_user FOREIGN KEY (user_id) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """
            )

            # ── 스키마 마이그레이션: question_hash 컬럼 추가 ──
            cur.execute("SHOW COLUMNS FROM questions LIKE 'question_hash'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE questions ADD COLUMN question_hash VARCHAR(64) NULL")

            cur.execute("SHOW COLUMNS FROM questions LIKE 'question_style'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE questions ADD COLUMN question_style VARCHAR(20) NULL")
            cur.execute("SHOW INDEX FROM questions WHERE Key_name='idx_question_style'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE questions ADD INDEX idx_question_style (question_style)")

            # ── 기존 문제 중복 제거 및 해시 재생성 ──────────
            cur.execute("SELECT id, category, question FROM questions ORDER BY id ASC")
            rows = cur.fetchall()
            seen = set()
            duplicate_ids = []
            for row in rows:
                cleaned_question = sanitize_question_text(row["question"])
                q_hash = question_hash(row["category"], cleaned_question)
                if q_hash in seen:
                    duplicate_ids.append(row["id"])
                else:
                    seen.add(q_hash)
                    cur.execute(
                        "UPDATE questions SET question=%s, question_hash=%s WHERE id=%s",
                        (cleaned_question, q_hash, row["id"]),
                    )

            if duplicate_ids:
                placeholders = ",".join(["%s"] * len(duplicate_ids))
                cur.execute(f"DELETE FROM questions WHERE id IN ({placeholders})", duplicate_ids)
                print(f"[CLEANUP] removed duplicates={len(duplicate_ids)}")

            # ── UNIQUE 제약 보장 ──────────────────────────────
            cur.execute("UPDATE questions SET question_hash='MISSING_HASH' WHERE question_hash IS NULL")
            cur.execute("ALTER TABLE questions MODIFY COLUMN question_hash VARCHAR(64) NOT NULL")
            cur.execute("SHOW INDEX FROM questions WHERE Key_name='uq_question_hash'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE questions ADD UNIQUE KEY uq_question_hash (question_hash)")

            cur.execute("SELECT COUNT(*) as cnt FROM questions")
            before_cnt = cur.fetchone()["cnt"]

            conn.commit()

            cur.execute("SELECT COUNT(*) as cnt FROM questions")
            after_cnt = cur.fetchone()["cnt"]
            print(f"[SEED] inserted={after_cnt - before_cnt}, total={after_cnt}")

    print("[DONE] DB init completed")


if __name__ == "__main__":
    init_db()
