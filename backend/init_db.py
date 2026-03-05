"""
Database bootstrap script
Runs table migration + deduplication + seed insert.
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

DB_USER = os.getenv("DB_USER", "quizuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "quizpassword")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "quizdb")
INIT_DB_SEED = os.getenv("INIT_DB_SEED", "false").lower() == "true"

if DB_HOST == "db":
    try:
        socket.gethostbyname("db")
    except socket.gaierror:
        DB_HOST = "localhost"


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
    base = f"{normalize_text(category).lower()}|{sanitize_question_text(question).lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


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


# SAMPLE_QUESTIONS 제거 - AI API로만 문제 생성


def init_db():
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS questions (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    category      VARCHAR(50) NOT NULL,
                    question      TEXT NOT NULL,
                    choice_a      TEXT NOT NULL,
                    choice_b      TEXT NOT NULL,
                    choice_c      TEXT NOT NULL,
                    choice_d      TEXT NOT NULL,
                    answer        VARCHAR(1) NOT NULL,
                    explanation   TEXT,
                    question_hash VARCHAR(64),
                    INDEX idx_category (category),
                    UNIQUE KEY uq_question_hash (question_hash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
                """
            )

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

            cur.execute("SHOW COLUMNS FROM questions LIKE 'question_hash'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE questions ADD COLUMN question_hash VARCHAR(64) NULL")

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