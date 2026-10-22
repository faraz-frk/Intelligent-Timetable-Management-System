"""
auth.py
-------
Authentication routes: register, login, logout, current user.

All database operations use raw SQL via PyMySQL.
"""

import logging
from flask import Blueprint, jsonify, request
from flask_login import login_user, logout_user, login_required, current_user
from models import (
    find_user_by_username,
    find_user_by_email,
    create_user,
)

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

VALID_ROLES = {"admin", "teacher", "student"}


@auth_bp.route("/register", methods=["POST"])
def register():
    data         = request.get_json(silent=True) or {}
    username     = (data.get("username")     or "").strip()
    email        = (data.get("email")        or "").strip().lower()
    password     = (data.get("password")     or "").strip()
    school_name  = (data.get("school_name")  or "").strip()
    role         = (data.get("role")         or "student").strip().lower()
    teacher_name = (data.get("teacher_name") or "").strip()
    class_num    = data.get("class_num")
    section      = (data.get("section")      or "").strip().upper()

    if not username or not email or not password:
        return jsonify({"success": False, "error": "Username, email and password are required."}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters."}), 400
    if role not in VALID_ROLES:
        return jsonify({"success": False, "error": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}."}), 400
    if role == "teacher" and not teacher_name:
        return jsonify({"success": False, "error": "Teacher name is required for teacher accounts."}), 400
    if role == "student":
        try:
            class_num = int(class_num)
            if class_num < 1 or class_num > 10:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Class must be a number between 1 and 10."}), 400
        if section not in ("A", "B"):
            return jsonify({"success": False, "error": "Section must be A or B."}), 400

    # Check uniqueness using raw SQL
    if find_user_by_username(username):
        return jsonify({"success": False, "error": "Username already taken."}), 409
    if find_user_by_email(email):
        return jsonify({"success": False, "error": "Email already registered."}), 409

    # INSERT INTO users (...) VALUES (...) — raw SQL inside create_user()
    user = create_user(
        username=username,
        email=email,
        password=password,
        school_name=school_name,
        role=role,
        teacher_name=teacher_name if role == "teacher" else None,
        class_num=class_num        if role == "student" else None,
        section=section            if role == "student" else None,
    )

    login_user(user, remember=True)
    logger.info("New user registered: %s (role=%s)", username, role)
    return jsonify({"success": True, "user": user.to_dict()}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password are required."}), 400

    # SELECT * FROM users WHERE username = %s — raw SQL inside find_user_by_username()
    user = find_user_by_username(username)
    if not user or not user.check_password(password):
        return jsonify({"success": False, "error": "Invalid username or password."}), 401

    login_user(user, remember=True)
    logger.info("User logged in: %s (role=%s)", username, user.role)
    return jsonify({"success": True, "user": user.to_dict()})


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    username = current_user.username
    logout_user()
    logger.info("User logged out: %s", username)
    return jsonify({"success": True})


@auth_bp.route("/me", methods=["GET"])
def me():
    if current_user.is_authenticated:
        return jsonify({"authenticated": True, "user": current_user.to_dict()})
    return jsonify({"authenticated": False}), 401
