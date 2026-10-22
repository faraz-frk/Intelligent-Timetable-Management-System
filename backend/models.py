"""
models.py
---------
MySQL database connection and raw SQL helpers for the timetable system.

Uses PyMySQL (NO ORM — raw SQL queries).
"""

import os
import logging
from datetime import datetime

import pymysql
import pymysql.cursors
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MySQL connection settings (read from .env)
# ---------------------------------------------------------------------------
_DB_CONFIG = {
    "host":     os.getenv("MYSQL_HOST", "localhost"),
    "user":     os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "root"),
    "database": os.getenv("MYSQL_DATABASE", "timetable_db"),
    "autocommit": False,
}


def get_connection():
    """Open a new MySQL connection using PyMySQL."""
    return pymysql.connect(**_DB_CONFIG)


# ---------------------------------------------------------------------------
# Create tables (run once on startup)
# ---------------------------------------------------------------------------
def init_db():
    """Create all required tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                username      VARCHAR(80)  UNIQUE NOT NULL,
                email         VARCHAR(120) UNIQUE NOT NULL,
                password_hash VARCHAR(256) NOT NULL,
                school_name   VARCHAR(150),
                role          ENUM('admin', 'teacher', 'student') DEFAULT 'student',
                teacher_name  VARCHAR(150),
                class_num     INT,
                section       CHAR(1),
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS timetables (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                user_id         INT NOT NULL,
                class_num       INT NOT NULL,
                section         CHAR(1) NOT NULL,
                schedule_json   LONGTEXT NOT NULL,
                sports_day      VARCHAR(20),
                sports_periods  VARCHAR(20),
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE KEY uq_user_class_section (user_id, class_num, section)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teacher_uploads (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                user_id       INT NOT NULL,
                teachers_json LONGTEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
        logger.info("MySQL tables verified / created.")
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# User class (works with flask-login)
# ---------------------------------------------------------------------------
class User(UserMixin):
    """User object compatible with Flask-Login. Built from a MySQL row dict."""

    def __init__(self, row: dict):
        self.id           = row["id"]
        self.username     = row["username"]
        self.email        = row["email"]
        self.password_hash = row["password_hash"]
        self.school_name  = row.get("school_name")
        self.role         = row["role"]
        self.teacher_name = row.get("teacher_name")
        self.class_num    = row.get("class_num")
        self.section      = row.get("section")
        self.created_at   = row.get("created_at", datetime.utcnow())

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id":           self.id,
            "username":     self.username,
            "email":        self.email,
            "school_name":  self.school_name,
            "role":         self.role,
            "teacher_name": self.teacher_name,
            "class_num":    self.class_num,
            "section":      self.section,
            "created_at":   self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
        }


# ---------------------------------------------------------------------------
# User queries  (raw SQL)
# ---------------------------------------------------------------------------
def find_user_by_id(user_id: int):
    """SELECT * FROM users WHERE id = %s"""
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return User(row) if row else None
    finally:
        cursor.close()
        conn.close()


def find_user_by_username(username: str):
    """SELECT * FROM users WHERE username = %s"""
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        row = cursor.fetchone()
        return User(row) if row else None
    finally:
        cursor.close()
        conn.close()


def find_user_by_email(email: str):
    """SELECT * FROM users WHERE email = %s"""
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cursor.fetchone()
        return User(row) if row else None
    finally:
        cursor.close()
        conn.close()


def find_first_admin():
    """SELECT * FROM users WHERE role = 'admin' LIMIT 1"""
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE role = 'admin' LIMIT 1")
        row = cursor.fetchone()
        return User(row) if row else None
    finally:
        cursor.close()
        conn.close()


def create_user(username, email, password, school_name, role, teacher_name=None, class_num=None, section=None):
    """INSERT INTO users (...) VALUES (...)  — returns the new User object."""
    password_hash = generate_password_hash(password)
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("""
            INSERT INTO users (username, email, password_hash, school_name, role, teacher_name, class_num, section)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (username, email, password_hash, school_name, role, teacher_name, class_num, section))
        conn.commit()
        new_id = cursor.lastrowid
        # Fetch and return the full user object
        cursor.execute("SELECT * FROM users WHERE id = %s", (new_id,))
        row = cursor.fetchone()
        return User(row)
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Timetable queries (raw SQL)
# ---------------------------------------------------------------------------
def save_timetables(user_id: int, all_tt: dict):
    """
    INSERT or UPDATE timetables for an admin user.
    Uses INSERT ... ON DUPLICATE KEY UPDATE for upsert.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for key, tt in all_tt.items():
            class_num = tt.get("class_num")
            section   = tt.get("section")
            sp        = tt.get("sports_periods", [0, 0])
            import json
            schedule_json  = json.dumps(tt.get("schedule", {}))
            sports_day     = tt.get("sports_day", "")
            sports_periods = f"{sp[0]},{sp[1]}"

            cursor.execute("""
                INSERT INTO timetables (user_id, class_num, section, schedule_json, sports_day, sports_periods)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    schedule_json  = VALUES(schedule_json),
                    sports_day     = VALUES(sports_day),
                    sports_periods = VALUES(sports_periods)
            """, (user_id, class_num, section, schedule_json, sports_day, sports_periods))
        conn.commit()
        logger.info("Saved %d timetables for user_id=%d", len(all_tt), user_id)
    finally:
        cursor.close()
        conn.close()


def load_timetables(user_id: int) -> dict:
    """
    SELECT * FROM timetables WHERE user_id = %s
    Returns a dict: { "Class_1A": {...}, "Class_1B": {...}, ... }
    """
    import json
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM timetables WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall()
        if not rows:
            return {}
        all_tt = {}
        for row in rows:
            key = f"Class_{row['class_num']}{row['section']}"
            sp = [int(x) for x in row["sports_periods"].split(",")] if row["sports_periods"] else [0, 0]
            all_tt[key] = {
                "class_num":      row["class_num"],
                "section":        row["section"],
                "sports_day":     row["sports_day"],
                "sports_periods": sp,
                "schedule":       json.loads(row["schedule_json"]),
            }
        return all_tt
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Teacher upload queries (raw SQL)
# ---------------------------------------------------------------------------
def save_teacher_upload(user_id: int, teachers_data: list):
    """INSERT or UPDATE the teacher upload JSON for an admin."""
    import json
    teachers_json = json.dumps(teachers_data)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check if exists
        cursor.execute("SELECT id FROM teacher_uploads WHERE user_id = %s", (user_id,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("UPDATE teacher_uploads SET teachers_json = %s WHERE user_id = %s",
                           (teachers_json, user_id))
        else:
            cursor.execute("INSERT INTO teacher_uploads (user_id, teachers_json) VALUES (%s, %s)",
                           (user_id, teachers_json))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
