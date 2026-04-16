from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "math_academy.db"
COOKIE_NAME = "math_academy_session"
SESSION_DAYS = 30
ALL_GRADES = ["V", "VI", "VII", "VIII"]
EMPTY_SNAPSHOT_META = {
    "app": "Matematică pe clase",
    "version": 2,
    "storage": "sqlite-backend",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def normalize_role(role: str) -> str:
    return "profesor" if str(role or "").strip().lower() == "profesor" else "elev"


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    alnum = "".join(ch for ch in stripped if ch.isalnum())
    return alnum.upper()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 200_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iter_str, salt, expected = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return secrets.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS study_groups (
                id TEXT PRIMARY KEY,
                grade TEXT NOT NULL,
                name TEXT NOT NULL,
                section TEXT NOT NULL,
                description TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                teacher_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS enrollments (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL REFERENCES study_groups(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                joined_at TEXT NOT NULL,
                UNIQUE(group_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS lesson_visits (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                lesson_id TEXT NOT NULL,
                grade TEXT NOT NULL,
                title TEXT NOT NULL,
                open_count INTEGER NOT NULL DEFAULT 0,
                first_opened_at TEXT NOT NULL,
                last_opened_at TEXT NOT NULL,
                UNIQUE(user_id, lesson_id)
            );

            CREATE TABLE IF NOT EXISTS tests (
                id TEXT PRIMARY KEY,
                teacher_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                group_id TEXT NOT NULL REFERENCES study_groups(id) ON DELETE CASCADE,
                grade TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS test_questions (
                id TEXT PRIMARY KEY,
                test_id TEXT NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                text TEXT NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                option_d TEXT NOT NULL,
                correct_index INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS test_submissions (
                id TEXT PRIMARY KEY,
                test_id TEXT NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                answers_json TEXT NOT NULL,
                score INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                percentage INTEGER NOT NULL,
                submitted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS homeworks (
                id TEXT PRIMARY KEY,
                teacher_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                group_id TEXT NOT NULL REFERENCES study_groups(id) ON DELETE CASCADE,
                grade TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                due_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS homework_submissions (
                id TEXT PRIMARY KEY,
                homework_id TEXT NOT NULL REFERENCES homeworks(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                answer_text TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                UNIQUE(homework_id, student_id)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ui_state (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, key)
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count == 0:
            seed_demo_data(conn)
        conn.commit()


def seed_demo_data(conn: sqlite3.Connection) -> None:
    created_at = now_iso()
    teacher_id = "usr_profesor_demo"
    student_id = "usr_elev_demo"

    conn.execute(
        "INSERT INTO users (id, full_name, username, password_hash, role, created_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)",
        (teacher_id, "profesor_demo", "profesor_demo", hash_password("1234"), "profesor", created_at),
    )
    conn.execute(
        "INSERT INTO users (id, full_name, username, password_hash, role, created_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)",
        (student_id, "elev_demo", "elev_demo", hash_password("1234"), "elev", created_at),
    )

    groups = [
        ("grp_demo_v", "V", "Clasa V", "Demo", "Acces demonstrativ pentru materia de clasa a V-a.", "MATEV5"),
        ("grp_demo_vi", "VI", "Clasa VI", "Demo", "Acces demonstrativ pentru materia de clasa a VI-a.", "MATEVI6"),
        ("grp_demo_vii", "VII", "Clasa VII", "Demo", "Acces demonstrativ pentru materia de clasa a VII-a.", "MATEVII7"),
        ("grp_demo_viii", "VIII", "Clasa VIII", "Demo", "Acces demonstrativ pentru materia de clasa a VIII-a.", "MATEVIII8"),
    ]
    for group_id, grade, name, section, description, code in groups:
        conn.execute(
            "INSERT INTO study_groups (id, grade, name, section, description, code, teacher_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (group_id, grade, name, section, description, code, teacher_id, created_at),
        )
        conn.execute(
            "INSERT INTO enrollments (id, group_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (uid("enr"), group_id, student_id, created_at),
        )

    test_id = "test_demo_v"
    conn.execute(
        "INSERT INTO tests (id, teacher_id, group_id, grade, title, description, duration_minutes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            test_id,
            teacher_id,
            "grp_demo_v",
            "V",
            "Test demo · clasa a V-a",
            "Exemplu de test grilă creat local pentru grupa demo.",
            20,
            created_at,
        ),
    )
    demo_questions = [
        ("Care este rezultatul lui 18 + 7?", ["23", "24", "25", "26"], 2),
        ("Care dintre numere este par?", ["17", "21", "28", "33"], 2),
        ("Cât este 6 × 4?", ["20", "22", "24", "26"], 2),
    ]
    for index, (text, options, correct_index) in enumerate(demo_questions):
        conn.execute(
            "INSERT INTO test_questions (id, test_id, position, text, option_a, option_b, option_c, option_d, correct_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid("q"), test_id, index, text, options[0], options[1], options[2], options[3], correct_index),
        )

    conn.execute(
        "INSERT INTO homeworks (id, teacher_id, group_id, grade, title, description, due_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "hw_demo_v",
            teacher_id,
            "grp_demo_v",
            "V",
            "Temă demo · operații cu numere naturale",
            "Rezolvă exercițiile 1-4 din caiet și explică în 3-5 rânduri cum ai verificat calculele.",
            "",
            created_at,
        ),
    )


def serialize_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "fullName": row["full_name"],
        "username": row["username"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "isDemo": bool(row["is_demo"]),
    }


def serialize_group(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "grade": row["grade"],
        "name": row["name"],
        "section": row["section"],
        "description": row["description"],
        "code": row["code"],
        "teacherId": row["teacher_id"],
        "createdAt": row["created_at"],
    }


def serialize_enrollment(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "groupId": row["group_id"],
        "userId": row["user_id"],
        "joinedAt": row["joined_at"],
    }


def serialize_visit(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "lessonId": row["lesson_id"],
        "grade": row["grade"],
        "title": row["title"],
        "openCount": row["open_count"],
        "firstOpenedAt": row["first_opened_at"],
        "lastOpenedAt": row["last_opened_at"],
    }


def serialize_test(row: sqlite3.Row, questions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "teacherId": row["teacher_id"],
        "groupId": row["group_id"],
        "grade": row["grade"],
        "title": row["title"],
        "description": row["description"],
        "durationMinutes": row["duration_minutes"],
        "createdAt": row["created_at"],
        "questions": questions,
    }


def serialize_test_submission(row: sqlite3.Row, include_answers: bool = False) -> dict[str, Any]:
    data = {
        "id": row["id"],
        "testId": row["test_id"],
        "studentId": row["student_id"],
        "score": row["score"],
        "totalQuestions": row["total_questions"],
        "percentage": row["percentage"],
        "submittedAt": row["submitted_at"],
    }
    if include_answers:
        data["answers"] = json.loads(row["answers_json"])
    return data


def serialize_homework(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "teacherId": row["teacher_id"],
        "groupId": row["group_id"],
        "grade": row["grade"],
        "title": row["title"],
        "description": row["description"],
        "dueAt": row["due_at"],
        "createdAt": row["created_at"],
    }


def serialize_homework_submission(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "homeworkId": row["homework_id"],
        "studentId": row["student_id"],
        "answerText": row["answer_text"],
        "submittedAt": row["submitted_at"],
    }


def fetch_users_by_ids(conn: sqlite3.Connection, user_ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = [item for item in dict.fromkeys(user_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM users WHERE id IN ({placeholders})", ids).fetchall()


def fetch_groups_by_ids(conn: sqlite3.Connection, group_ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = [item for item in dict.fromkeys(group_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM study_groups WHERE id IN ({placeholders}) ORDER BY datetime(created_at) DESC", ids).fetchall()


def fetch_enrollments_by_group_ids(conn: sqlite3.Connection, group_ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = [item for item in dict.fromkeys(group_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM enrollments WHERE group_id IN ({placeholders}) ORDER BY datetime(joined_at) DESC", ids).fetchall()


def fetch_visits_by_user_ids(conn: sqlite3.Connection, user_ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = [item for item in dict.fromkeys(user_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM lesson_visits WHERE user_id IN ({placeholders}) ORDER BY datetime(last_opened_at) DESC", ids).fetchall()


def fetch_tests_by_teacher(conn: sqlite3.Connection, teacher_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tests WHERE teacher_id = ? ORDER BY datetime(created_at) DESC",
        (teacher_id,),
    ).fetchall()


def fetch_tests_by_group_ids(conn: sqlite3.Connection, group_ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = [item for item in dict.fromkeys(group_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM tests WHERE group_id IN ({placeholders}) ORDER BY datetime(created_at) DESC", ids).fetchall()


def fetch_questions_for_tests(conn: sqlite3.Connection, test_ids: Iterable[str], include_correct: bool) -> dict[str, list[dict[str, Any]]]:
    ids = [item for item in dict.fromkeys(test_ids) if item]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM test_questions WHERE test_id IN ({placeholders}) ORDER BY test_id, position ASC",
        ids,
    ).fetchall()
    mapping: dict[str, list[dict[str, Any]]] = {test_id: [] for test_id in ids}
    for row in rows:
        item = {
            "id": row["id"],
            "text": row["text"],
            "options": [row["option_a"], row["option_b"], row["option_c"], row["option_d"]],
        }
        if include_correct:
            item["correctIndex"] = row["correct_index"]
        mapping.setdefault(row["test_id"], []).append(item)
    return mapping


def fetch_test_submissions_for_tests(conn: sqlite3.Connection, test_ids: Iterable[str], include_answers: bool = False) -> list[dict[str, Any]]:
    ids = [item for item in dict.fromkeys(test_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM test_submissions WHERE test_id IN ({placeholders}) ORDER BY datetime(submitted_at) DESC",
        ids,
    ).fetchall()
    return [serialize_test_submission(row, include_answers=include_answers) for row in rows]


def fetch_test_submissions_for_student(conn: sqlite3.Connection, test_ids: Iterable[str], student_id: str) -> list[dict[str, Any]]:
    ids = [item for item in dict.fromkeys(test_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM test_submissions WHERE test_id IN ({placeholders}) AND student_id = ? ORDER BY datetime(submitted_at) DESC",
        (*ids, student_id),
    ).fetchall()
    return [serialize_test_submission(row, include_answers=False) for row in rows]


def fetch_homeworks_by_teacher(conn: sqlite3.Connection, teacher_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM homeworks WHERE teacher_id = ? ORDER BY datetime(created_at) DESC",
        (teacher_id,),
    ).fetchall()


def fetch_homeworks_by_group_ids(conn: sqlite3.Connection, group_ids: Iterable[str]) -> list[sqlite3.Row]:
    ids = [item for item in dict.fromkeys(group_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(f"SELECT * FROM homeworks WHERE group_id IN ({placeholders}) ORDER BY datetime(created_at) DESC", ids).fetchall()


def fetch_homework_submissions_for_homeworks(conn: sqlite3.Connection, homework_ids: Iterable[str]) -> list[dict[str, Any]]:
    ids = [item for item in dict.fromkeys(homework_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM homework_submissions WHERE homework_id IN ({placeholders}) ORDER BY datetime(submitted_at) DESC",
        ids,
    ).fetchall()
    return [serialize_homework_submission(row) for row in rows]


def fetch_homework_submissions_for_student(conn: sqlite3.Connection, homework_ids: Iterable[str], student_id: str) -> list[dict[str, Any]]:
    ids = [item for item in dict.fromkeys(homework_ids) if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM homework_submissions WHERE homework_id IN ({placeholders}) AND student_id = ? ORDER BY datetime(submitted_at) DESC",
        (*ids, student_id),
    ).fetchall()
    return [serialize_homework_submission(row) for row in rows]


def empty_snapshot() -> dict[str, Any]:
    return {
        "meta": {**EMPTY_SNAPSHOT_META, "createdAt": now_iso()},
        "users": [],
        "studyGroups": [],
        "enrollments": [],
        "lessonVisits": [],
        "tests": [],
        "testSubmissions": [],
        "homeworks": [],
        "homeworkSubmissions": [],
    }


def build_snapshot(conn: sqlite3.Connection, user: sqlite3.Row | None) -> dict[str, Any]:
    if user is None:
        return empty_snapshot()

    snapshot = empty_snapshot()
    snapshot["meta"] = {**snapshot["meta"], "createdAt": user["created_at"]}

    if user["role"] == "profesor":
        group_rows = conn.execute(
            "SELECT * FROM study_groups WHERE teacher_id = ? ORDER BY datetime(created_at) DESC",
            (user["id"],),
        ).fetchall()
        group_ids = [row["id"] for row in group_rows]
        enrollment_rows = fetch_enrollments_by_group_ids(conn, group_ids)
        student_ids = [row["user_id"] for row in enrollment_rows]
        user_rows = fetch_users_by_ids(conn, [user["id"], *student_ids])
        visit_rows = fetch_visits_by_user_ids(conn, student_ids)
        test_rows = fetch_tests_by_teacher(conn, user["id"])
        test_ids = [row["id"] for row in test_rows]
        questions_map = fetch_questions_for_tests(conn, test_ids, include_correct=True)
        homework_rows = fetch_homeworks_by_teacher(conn, user["id"])
        homework_ids = [row["id"] for row in homework_rows]

        snapshot["users"] = [serialize_user(row) for row in user_rows]
        snapshot["studyGroups"] = [serialize_group(row) for row in group_rows]
        snapshot["enrollments"] = [serialize_enrollment(row) for row in enrollment_rows]
        snapshot["lessonVisits"] = [serialize_visit(row) for row in visit_rows]
        snapshot["tests"] = [serialize_test(row, questions_map.get(row["id"], [])) for row in test_rows]
        snapshot["testSubmissions"] = fetch_test_submissions_for_tests(conn, test_ids, include_answers=False)
        snapshot["homeworks"] = [serialize_homework(row) for row in homework_rows]
        snapshot["homeworkSubmissions"] = fetch_homework_submissions_for_homeworks(conn, homework_ids)
        return snapshot

    enrollment_rows = conn.execute(
        "SELECT * FROM enrollments WHERE user_id = ? ORDER BY datetime(joined_at) DESC",
        (user["id"],),
    ).fetchall()
    group_ids = [row["group_id"] for row in enrollment_rows]
    group_rows = fetch_groups_by_ids(conn, group_ids)
    teacher_ids = [row["teacher_id"] for row in group_rows]
    user_rows = fetch_users_by_ids(conn, [user["id"], *teacher_ids])
    visit_rows = fetch_visits_by_user_ids(conn, [user["id"]])
    test_rows = fetch_tests_by_group_ids(conn, group_ids)
    test_ids = [row["id"] for row in test_rows]
    questions_map = fetch_questions_for_tests(conn, test_ids, include_correct=False)
    homework_rows = fetch_homeworks_by_group_ids(conn, group_ids)
    homework_ids = [row["id"] for row in homework_rows]

    snapshot["users"] = [serialize_user(row) for row in user_rows]
    snapshot["studyGroups"] = [serialize_group(row) for row in group_rows]
    snapshot["enrollments"] = [serialize_enrollment(row) for row in enrollment_rows]
    snapshot["lessonVisits"] = [serialize_visit(row) for row in visit_rows]
    snapshot["tests"] = [serialize_test(row, questions_map.get(row["id"], [])) for row in test_rows]
    snapshot["testSubmissions"] = fetch_test_submissions_for_student(conn, test_ids, user["id"])
    snapshot["homeworks"] = [serialize_homework(row) for row in homework_rows]
    snapshot["homeworkSubmissions"] = fetch_homework_submissions_for_student(conn, homework_ids, user["id"])
    return snapshot


def build_session_payload(conn: sqlite3.Connection, user: sqlite3.Row | None) -> dict[str, Any]:
    return {
        "authenticated": bool(user),
        "user": serialize_user(user) if user else None,
        "snapshot": build_snapshot(conn, user),
    }


def create_session(conn: sqlite3.Connection, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    created_at = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, created_at, expires_at),
    )
    conn.commit()
    return token


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def get_user_from_session(conn: sqlite3.Connection, request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    session_row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    if session_row is None:
        return None
    try:
        expires_at = datetime.fromisoformat(session_row["expires_at"])
    except Exception:
        delete_session(conn, token)
        return None
    if expires_at < datetime.now(timezone.utc):
        delete_session(conn, token)
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (session_row["user_id"],)).fetchone()


def require_user(conn: sqlite3.Connection, request: Request) -> sqlite3.Row:
    user = get_user_from_session(conn, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Trebuie să fii autentificat.")
    return user


def generate_group_code(conn: sqlite3.Connection, grade: str, name: str, section: str) -> str:
    base = (normalize_text(f"{grade}{name}{section}")[:6] or "MATE")
    while True:
        code = f"{base}{secrets.randbelow(900) + 100}"
        exists = conn.execute("SELECT 1 FROM study_groups WHERE code = ?", (code,)).fetchone()
        if not exists:
            return code


class RegisterPayload(BaseModel):
    username: str = Field(..., min_length=3)
    password: str = Field(..., min_length=4)
    role: str


class LoginPayload(BaseModel):
    username: str
    password: str
    role: str


class GroupCreatePayload(BaseModel):
    grade: str
    name: str
    section: str
    description: str = ""


class GroupJoinPayload(BaseModel):
    code: str


class QuestionPayload(BaseModel):
    text: str
    options: list[str]
    correctIndex: int


class TestCreatePayload(BaseModel):
    groupId: str
    title: str
    description: str = ""
    durationMinutes: int = 30
    questions: list[QuestionPayload]


class TestSubmitPayload(BaseModel):
    answers: list[int]


class HomeworkCreatePayload(BaseModel):
    groupId: str
    title: str
    description: str
    dueAt: str = ""


class HomeworkSubmitPayload(BaseModel):
    answerText: str


class LessonVisitPayload(BaseModel):
    lessonId: str
    grade: str
    title: str


class UiStatePayload(BaseModel):
    key: str
    value: Any


app = FastAPI(title="Math Academy Backend")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/session")
def get_session(request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = get_user_from_session(conn, request)
        return build_session_payload(conn, user)


@app.post("/api/auth/register")
def register(payload: RegisterPayload) -> dict[str, Any]:
    username = normalize_username(payload.username)
    password = str(payload.password or "").strip()
    role = normalize_role(payload.role)
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Numele utilizator trebuie să aibă cel puțin 3 caractere.")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Parola trebuie să aibă cel puțin 4 caractere.")
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Există deja un cont cu acest utilizator.")
        user_id = uid("usr")
        created_at = now_iso()
        conn.execute(
            "INSERT INTO users (id, full_name, username, password_hash, role, created_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (user_id, username, username, hash_password(password), role, created_at),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return {"user": serialize_user(user)}


@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response) -> JSONResponse:
    username = normalize_username(payload.username)
    password = str(payload.password or "").strip()
    role = normalize_role(payload.role)
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ? AND role = ?", (username, role)).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Datele de autentificare nu sunt corecte.")
        token = create_session(conn, user["id"])
        payload_data = build_session_payload(conn, user)
        json_response = JSONResponse(payload_data)
        json_response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_DAYS * 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )
        return json_response


@app.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    with get_conn() as conn:
        delete_session(conn, request.cookies.get(COOKIE_NAME))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.post("/api/groups")
def create_group(payload: GroupCreatePayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "profesor":
            raise HTTPException(status_code=403, detail="Doar profesorii pot crea grupe.")
        grade = payload.grade if payload.grade in ALL_GRADES else "V"
        name = str(payload.name or "").strip()
        section = str(payload.section or "").strip()
        description = str(payload.description or "").strip()
        if len(name) < 3:
            raise HTTPException(status_code=400, detail="Numele grupei trebuie să aibă cel puțin 3 caractere.")
        if len(section) < 1:
            raise HTTPException(status_code=400, detail="Completează secțiunea sau litera grupei.")
        group_id = uid("grp")
        code = generate_group_code(conn, grade, name, section)
        created_at = now_iso()
        conn.execute(
            "INSERT INTO study_groups (id, grade, name, section, description, code, teacher_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (group_id, grade, name, section, description, code, user["id"], created_at),
        )
        conn.commit()
        group = conn.execute("SELECT * FROM study_groups WHERE id = ?", (group_id,)).fetchone()
        data = build_session_payload(conn, user)
        data["group"] = serialize_group(group)
        return data


@app.post("/api/groups/join")
def join_group(payload: GroupJoinPayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "elev":
            raise HTTPException(status_code=403, detail="Doar elevii se pot înscrie într-o grupă.")
        code = str(payload.code or "").strip().upper()
        if not code:
            raise HTTPException(status_code=400, detail="Introdu codul grupei primit de la profesor.")
        group = conn.execute("SELECT * FROM study_groups WHERE code = ?", (code,)).fetchone()
        if group is None:
            raise HTTPException(status_code=404, detail="Nu am găsit nicio grupă cu acest cod.")
        exists = conn.execute(
            "SELECT 1 FROM enrollments WHERE group_id = ? AND user_id = ?",
            (group["id"], user["id"]),
        ).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Ești deja înscris în această grupă.")
        conn.execute(
            "INSERT INTO enrollments (id, group_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (uid("enr"), group["id"], user["id"], now_iso()),
        )
        conn.commit()
        data = build_session_payload(conn, user)
        data["group"] = serialize_group(group)
        return data


@app.post("/api/tests")
def create_test(payload: TestCreatePayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "profesor":
            raise HTTPException(status_code=403, detail="Doar profesorii pot crea teste.")
        group = conn.execute(
            "SELECT * FROM study_groups WHERE id = ? AND teacher_id = ?",
            (payload.groupId, user["id"]),
        ).fetchone()
        if group is None:
            raise HTTPException(status_code=400, detail="Alege o grupă validă pentru test.")
        title = str(payload.title or "").strip()
        description = str(payload.description or "").strip()
        duration_minutes = max(5, min(180, int(payload.durationMinutes or 30)))
        if len(title) < 3:
            raise HTTPException(status_code=400, detail="Titlul testului trebuie să aibă cel puțin 3 caractere.")
        questions = payload.questions or []
        if len(questions) < 3:
            raise HTTPException(status_code=400, detail="Testul trebuie să aibă cel puțin 3 întrebări.")
        test_id = uid("test")
        created_at = now_iso()
        conn.execute(
            "INSERT INTO tests (id, teacher_id, group_id, grade, title, description, duration_minutes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (test_id, user["id"], group["id"], group["grade"], title, description, duration_minutes, created_at),
        )
        for index, question in enumerate(questions):
            text = str(question.text or "").strip()
            options = [str(option or "").strip() for option in (question.options or [])][:4]
            correct_index = int(question.correctIndex)
            if len(text) < 5:
                raise HTTPException(status_code=400, detail=f"Completează enunțul pentru întrebarea {index + 1}.")
            if len(options) != 4 or any(len(option) < 1 for option in options):
                raise HTTPException(status_code=400, detail=f"Completează toate cele 4 variante la întrebarea {index + 1}.")
            if correct_index < 0 or correct_index > 3:
                raise HTTPException(status_code=400, detail=f"Alege varianta corectă pentru întrebarea {index + 1}.")
            conn.execute(
                "INSERT INTO test_questions (id, test_id, position, text, option_a, option_b, option_c, option_d, correct_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid("q"), test_id, index, text, options[0], options[1], options[2], options[3], correct_index),
            )
        conn.commit()
        test_row = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
        questions_map = fetch_questions_for_tests(conn, [test_id], include_correct=True)
        data = build_session_payload(conn, user)
        data["test"] = serialize_test(test_row, questions_map.get(test_id, []))
        return data


@app.delete("/api/tests/{test_id}")
def delete_test(test_id: str, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "profesor":
            raise HTTPException(status_code=403, detail="Doar profesorii pot șterge teste.")
        test = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
        if test is None or test["teacher_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Nu poți șterge acest test.")
        conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        conn.commit()
        data = build_session_payload(conn, user)
        data["deletedTestId"] = test_id
        return data


@app.post("/api/tests/{test_id}/submit")
def submit_test(test_id: str, payload: TestSubmitPayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "elev":
            raise HTTPException(status_code=403, detail="Doar elevii pot trimite teste.")
        test = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
        if test is None:
            raise HTTPException(status_code=404, detail="Testul nu a fost găsit.")
        enrolled = conn.execute(
            "SELECT 1 FROM enrollments WHERE group_id = ? AND user_id = ?",
            (test["group_id"], user["id"]),
        ).fetchone()
        if not enrolled:
            raise HTTPException(status_code=403, detail="Nu ai acces la acest test.")
        question_rows = conn.execute(
            "SELECT * FROM test_questions WHERE test_id = ? ORDER BY position ASC",
            (test_id,),
        ).fetchall()
        answers = [int(value) for value in payload.answers]
        if len(answers) != len(question_rows) or any(value < 0 or value > 3 for value in answers):
            raise HTTPException(status_code=400, detail="Răspunde la toate întrebările înainte de trimitere.")
        score = sum(1 for index, row in enumerate(question_rows) if row["correct_index"] == answers[index])
        total = len(question_rows)
        percentage = round((score / total) * 100) if total else 0
        submitted_at = now_iso()
        submission_id = uid("sub")
        conn.execute(
            "INSERT INTO test_submissions (id, test_id, student_id, answers_json, score, total_questions, percentage, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (submission_id, test_id, user["id"], json.dumps(answers), score, total, percentage, submitted_at),
        )
        conn.commit()
        submission = conn.execute("SELECT * FROM test_submissions WHERE id = ?", (submission_id,)).fetchone()
        data = build_session_payload(conn, user)
        data["submission"] = serialize_test_submission(submission, include_answers=False)
        return data


@app.post("/api/homeworks")
def create_homework(payload: HomeworkCreatePayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "profesor":
            raise HTTPException(status_code=403, detail="Doar profesorii pot crea teme.")
        group = conn.execute(
            "SELECT * FROM study_groups WHERE id = ? AND teacher_id = ?",
            (payload.groupId, user["id"]),
        ).fetchone()
        if group is None:
            raise HTTPException(status_code=400, detail="Alege o grupă validă pentru temă.")
        title = str(payload.title or "").strip()
        description = str(payload.description or "").strip()
        due_at = str(payload.dueAt or "").strip()
        if len(title) < 3:
            raise HTTPException(status_code=400, detail="Titlul temei trebuie să aibă cel puțin 3 caractere.")
        if len(description) < 10:
            raise HTTPException(status_code=400, detail="Descrierea temei trebuie să aibă cel puțin 10 caractere.")
        homework_id = uid("hw")
        created_at = now_iso()
        conn.execute(
            "INSERT INTO homeworks (id, teacher_id, group_id, grade, title, description, due_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (homework_id, user["id"], group["id"], group["grade"], title, description, due_at, created_at),
        )
        conn.commit()
        homework = conn.execute("SELECT * FROM homeworks WHERE id = ?", (homework_id,)).fetchone()
        data = build_session_payload(conn, user)
        data["homework"] = serialize_homework(homework)
        return data


@app.delete("/api/homeworks/{homework_id}")
def delete_homework(homework_id: str, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "profesor":
            raise HTTPException(status_code=403, detail="Doar profesorii pot șterge teme.")
        homework = conn.execute("SELECT * FROM homeworks WHERE id = ?", (homework_id,)).fetchone()
        if homework is None or homework["teacher_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Nu poți șterge această temă.")
        conn.execute("DELETE FROM homeworks WHERE id = ?", (homework_id,))
        conn.commit()
        data = build_session_payload(conn, user)
        data["deletedHomeworkId"] = homework_id
        return data


@app.post("/api/homeworks/{homework_id}/submit")
def submit_homework(homework_id: str, payload: HomeworkSubmitPayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        if user["role"] != "elev":
            raise HTTPException(status_code=403, detail="Doar elevii pot trimite teme.")
        homework = conn.execute("SELECT * FROM homeworks WHERE id = ?", (homework_id,)).fetchone()
        if homework is None:
            raise HTTPException(status_code=404, detail="Tema nu a fost găsită.")
        enrolled = conn.execute(
            "SELECT 1 FROM enrollments WHERE group_id = ? AND user_id = ?",
            (homework["group_id"], user["id"]),
        ).fetchone()
        if not enrolled:
            raise HTTPException(status_code=403, detail="Nu ai acces la această temă.")
        answer_text = str(payload.answerText or "").strip()
        if len(answer_text) < 10:
            raise HTTPException(status_code=400, detail="Scrie un răspuns de cel puțin 10 caractere înainte să trimiți tema.")
        submitted_at = now_iso()
        existing = conn.execute(
            "SELECT id FROM homework_submissions WHERE homework_id = ? AND student_id = ?",
            (homework_id, user["id"]),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE homework_submissions SET answer_text = ?, submitted_at = ? WHERE id = ?",
                (answer_text, submitted_at, existing["id"]),
            )
            submission_id = existing["id"]
        else:
            submission_id = uid("hws")
            conn.execute(
                "INSERT INTO homework_submissions (id, homework_id, student_id, answer_text, submitted_at) VALUES (?, ?, ?, ?, ?)",
                (submission_id, homework_id, user["id"], answer_text, submitted_at),
            )
        conn.commit()
        submission = conn.execute("SELECT * FROM homework_submissions WHERE id = ?", (submission_id,)).fetchone()
        data = build_session_payload(conn, user)
        data["submission"] = serialize_homework_submission(submission)
        return data


@app.post("/api/lesson-visits")
def track_lesson_visit(payload: LessonVisitPayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        lesson_id = str(payload.lessonId or "").strip()
        grade = payload.grade if payload.grade in ALL_GRADES else "V"
        title = str(payload.title or lesson_id).strip() or lesson_id
        if not lesson_id:
            raise HTTPException(status_code=400, detail="Lecția nu a fost specificată.")
        row = conn.execute(
            "SELECT * FROM lesson_visits WHERE user_id = ? AND lesson_id = ?",
            (user["id"], lesson_id),
        ).fetchone()
        timestamp = now_iso()
        if row is None:
            visit_id = uid("visit")
            conn.execute(
                "INSERT INTO lesson_visits (id, user_id, lesson_id, grade, title, open_count, first_opened_at, last_opened_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (visit_id, user["id"], lesson_id, grade, title, timestamp, timestamp),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM lesson_visits WHERE id = ?", (visit_id,)).fetchone()
        else:
            conn.execute(
                "UPDATE lesson_visits SET grade = ?, title = ?, open_count = ?, last_opened_at = ? WHERE id = ?",
                (grade, title, row["open_count"] + 1, timestamp, row["id"]),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM lesson_visits WHERE id = ?", (row["id"],)).fetchone()
        return {"visit": serialize_visit(row)}


@app.get("/api/ui-state")
def get_ui_state(key: str, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        row = conn.execute(
            "SELECT value_json FROM ui_state WHERE user_id = ? AND key = ?",
            (user["id"], key),
        ).fetchone()
        return {"value": json.loads(row["value_json"]) if row else None}


@app.post("/api/ui-state")
def set_ui_state(payload: UiStatePayload, request: Request) -> dict[str, Any]:
    with get_conn() as conn:
        user = require_user(conn, request)
        conn.execute(
            "INSERT INTO ui_state (user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (user["id"], payload.key, json.dumps(payload.value), now_iso()),
        )
        conn.commit()
        return {"ok": True}


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
