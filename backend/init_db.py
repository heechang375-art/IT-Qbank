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


SAMPLE_QUESTIONS = [
    ("network", "OSI 계층 중 라우팅을 담당하는 계층은?", "물리 계층", "데이터링크 계층", "네트워크 계층", "전송 계층", "C", "IP 기반 라우팅은 네트워크 계층에서 수행됩니다."),
    ("network", "HTTPS 기본 포트는?", "80", "443", "22", "53", "B", "HTTPS는 TCP 443 포트를 사용합니다."),
    ("network", "DNS의 주된 역할은?", "IP를 MAC으로 변환", "도메인을 IP로 변환", "포트 스캔", "패킷 암호화", "B", "DNS는 도메인 이름을 IP 주소로 해석합니다."),
    ("network", "UDP의 특징으로 옳은 것은?", "연결형 전송", "순서 보장", "비연결형/오버헤드 적음", "혼잡 제어 강함", "C", "UDP는 비연결형이며 오버헤드가 적습니다."),
    ("infra", "Kubernetes의 최소 배포 단위는?", "Container", "Pod", "Node", "Namespace", "B", "Pod는 최소 배포 단위입니다."),
    ("infra", "IaC 도구로 가장 적절한 것은?", "Terraform", "Wireshark", "Grafana", "Jenkins", "A", "Terraform은 대표적인 IaC 도구입니다."),
    ("infra", "Ingress의 주요 역할은?", "볼륨 생성", "외부 HTTP/HTTPS 라우팅", "파드 스케일링", "노드 교체", "B", "Ingress는 외부 요청 라우팅 규칙을 제공합니다."),
    ("infra", "로드밸런서의 주요 역할은?", "디스크 암호화", "트래픽 분산", "코드 빌드", "DNS 제거", "B", "요청 트래픽을 여러 서버에 분산합니다."),
    ("linux", "실행 중인 프로세스를 확인하는 명령은?", "df -h", "ps aux", "chmod 755", "tail -f", "B", "ps aux는 실행 중인 프로세스를 출력합니다."),
    ("linux", "chmod 755 권한 설명으로 맞는 것은?", "소유자 rwx, 그룹 r-x, 기타 r-x", "소유자 r--, 그룹 rwx, 기타 rwx", "모든 사용자 rwx", "소유자만 rwx", "A", "7은 rwx, 5는 r-x를 의미합니다."),
    ("linux", "파일 소유자를 변경하는 명령은?", "chown", "chmod", "lsblk", "uptime", "A", "chown 명령으로 소유자/그룹을 변경합니다."),
    ("linux", "SSH 공개키 로그인에 사용되는 파일은?", "~/.ssh/known_hosts", "~/.ssh/config", "~/.ssh/authorized_keys", "/etc/hosts", "C", "authorized_keys에 공개키를 등록해 인증합니다."),
]


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

            sql = """
                INSERT IGNORE INTO questions
                    (category, question, choice_a, choice_b, choice_c, choice_d, answer, explanation, question_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            payload = [q + (question_hash(q[0], q[1]),) for q in SAMPLE_QUESTIONS]
            cur.executemany(sql, payload)
            conn.commit()

            cur.execute("SELECT COUNT(*) as cnt FROM questions")
            after_cnt = cur.fetchone()["cnt"]
            print(f"[SEED] inserted={after_cnt - before_cnt}, total={after_cnt}")

    print("[DONE] DB init completed")


if __name__ == "__main__":
    init_db()
