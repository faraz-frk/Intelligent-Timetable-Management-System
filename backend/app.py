"""
app.py
------
Flask backend for the Automatic Timetable Scheduler.

All database operations use raw SQL via mysql-connector-python (NO ORM).

Routes
------
POST /api/upload
    Upload teachers CSV → generate all 20 timetables → build RAG index.

GET  /api/timetables
    Return all cached timetables as JSON.

GET  /api/timetable/<class_num>/<section>
    Return a single class-section timetable.

POST /api/ask
    Body: {"question": "..."}
    RAG-based Q&A on the generated timetables.

GET  /api/export/pdf/<class_num>/<section>
    Download PDF for one class-section.

GET  /api/export/pdf/all
    Download PDF containing all 20 timetables.

GET  /api/sample-csv
    Download the sample teachers.csv.

GET  /api/health
    Returns {"status": "ok"}.
"""

import io
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user

from models import init_db, find_user_by_id, find_first_admin, save_timetables, load_timetables, save_teacher_upload
from auth import auth_bp
from csv_parser import parse_teachers_csv
from csp_scheduler import DAYS, DISPLAY_TIMELINE, PERIOD_TIMES, TimetableCSP
from rag_engine import RAGEngine

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app  — serves both the API and the frontend from one server
# ---------------------------------------------------------------------------
_FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

app = Flask(
    __name__,
    static_folder=_FRONTEND_DIR,
    static_url_path="",
)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "timetable-secret-key-2024")

CORS(app, supports_credentials=True)

# Init Flask-Login
login_manager = LoginManager(app)
login_manager.login_view = None   # API — return 401 instead of redirect

@login_manager.user_loader
def load_user(user_id):
    """SELECT * FROM users WHERE id = %s"""
    return find_user_by_id(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"success": False, "error": "Login required."}), 401

# Register auth blueprint
app.register_blueprint(auth_bp)

# Create MySQL tables on first run
init_db()

# In-memory RAG engine (rebuilt per upload)
_store = {
    "rag_engine": RAGEngine(),
}

# Per-user timetable cache: user_id -> all_tt dict
_user_timetables: dict = {}

# Path to bundled sample CSV (relative to this file)
_SAMPLE_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "..", "sample_data", "teachers.csv"
)

# ---------------------------------------------------------------------------
# Subject → background colour map (hex, for PDF)
# ---------------------------------------------------------------------------
SUBJECT_COLORS = {
    "Telugu":                "#FFE0E0",
    "Hindi":                 "#E0F0FF",
    "English":               "#E0FFE0",
    "Social":                "#FFF0E0",
    "Maths":                 "#F0E0FF",
    "Environmental Science": "#E0FFFF",
    "Computer":              "#FFE0F0",
    "Physics":               "#FFFDE0",
    "Biology":               "#E8F5E9",
    "Sports":                "#FFF9C4",
    "TBD":                   "#E0E0E0",
    "—":                     "#F5F5F5",
}


# ---------------------------------------------------------------------------
# Helper: count TBD slots
# ---------------------------------------------------------------------------
def _count_tbd(all_timetables: dict) -> int:
    count = 0
    for tt in all_timetables.values():
        for day_slots in tt.get("schedule", {}).values():
            for slot in day_slots:
                if slot.get("teacher") == "TBD":
                    count += 1
    return count


# ---------------------------------------------------------------------------
# PDF generation helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str):
    """Convert '#RRGGBB' to a reportlab Color object."""
    from reportlab.lib.colors import HexColor  # noqa: PLC0415
    return HexColor(hex_color)


def _generate_pdf_for_timetable(tt: dict) -> bytes:
    """
    Generate an A4-landscape PDF for a single class-section timetable.

    Returns raw PDF bytes.
    """
    from reportlab.lib import colors                     # noqa: PLC0415
    from reportlab.lib.pagesizes import A4, landscape   # noqa: PLC0415
    from reportlab.lib.styles import getSampleStyleSheet # noqa: PLC0415
    from reportlab.lib.units import cm                  # noqa: PLC0415
    from reportlab.platypus import (                    # noqa: PLC0415
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1 * cm,
        rightMargin=1 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()
    story = []

    class_num = tt.get("class_num")
    section = tt.get("section")
    sports_day = tt.get("sports_day", "")
    sp = tt.get("sports_periods", (0, 0))

    # ---- Title ----------------------------------------------------------
    title_style = styles["Title"]
    story.append(Paragraph("Sri Vidya Niketan School", title_style))
    story.append(Paragraph(
        f"Class {class_num} — Section {section} | Weekly Timetable",
        styles["Heading2"],
    ))
    story.append(Paragraph(
        f"Sports Day: {sports_day}  (Periods {sp[0]} &amp; {sp[1]})",
        styles["Normal"],
    ))
    story.append(Spacer(1, 0.4 * cm))

    # ---- Table ----------------------------------------------------------
    col_header = ["Period / Time"] + DAYS
    table_data = [col_header]

    schedule = tt.get("schedule", {})

    for entry in DISPLAY_TIMELINE:
        if entry["type"] in ("break", "lunch"):
            label = entry["label"]
            time_str = entry["time"]
            row = [f"{label}\n{time_str}"] + [""] * len(DAYS)
            table_data.append(("__BREAK__", row))
        else:
            p = entry["number"]
            start, end = PERIOD_TIMES[p]
            period_label = f"Period {p}\n{start}–{end}"
            row = [period_label]
            for day in DAYS:
                slots = schedule.get(day, [])
                slot = next((s for s in slots if s.get("period") == p), None)
                if slot:
                    subj = slot.get("subject", "—")
                    teacher = slot.get("teacher", "—")
                    row.append(f"{subj}\n{teacher}")
                else:
                    row.append("—")
            table_data.append(("__PERIOD__", row, p, schedule))

    # Build plain table rows (strip the type markers)
    plain_rows = []
    row_types = []  # 'header', 'break', 'period'
    plain_rows.append(col_header)
    row_types.append("header")

    for item in table_data[1:]:  # skip re-added header
        rtype = item[0]
        row_data = item[1]
        if rtype == "__BREAK__":
            plain_rows.append(row_data)
            row_types.append("break")
        else:
            plain_rows.append(row_data)
            row_types.append("period")

    col_widths = [3.5 * cm] + [3.8 * cm] * len(DAYS)

    tbl = Table(plain_rows, colWidths=col_widths, repeatRows=1)

    # Base style
    tbl_style_cmds = [
        ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#34495E")),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0),  9),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE",    (0, 1), (-1, -1), 8),
        ("ROWBACKGROUND", (0, 0), (-1, -1), [colors.white, colors.HexColor("#F9F9F9")]),
    ]

    # Colour break/lunch rows
    for row_idx, rtype in enumerate(row_types, start=0):
        if rtype == "break":
            tbl_style_cmds.append(
                ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#FDEBD0"))
            )
            tbl_style_cmds.append(
                ("SPAN", (0, row_idx), (-1, row_idx))
            )
            tbl_style_cmds.append(
                ("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Oblique")
            )

    tbl.setStyle(TableStyle(tbl_style_cmds))
    story.append(tbl)

    doc.build(story)
    return buf.getvalue()


def _generate_pdf_all(all_timetables: dict) -> bytes:
    """Generate a single PDF containing all 20 timetables (one per page)."""
    from reportlab.lib import colors                     # noqa: PLC0415
    from reportlab.lib.pagesizes import A4, landscape   # noqa: PLC0415
    from reportlab.lib.styles import getSampleStyleSheet # noqa: PLC0415
    from reportlab.lib.units import cm                  # noqa: PLC0415
    from reportlab.platypus import (                    # noqa: PLC0415
        PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1 * cm,
        rightMargin=1 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()
    story = []

    first = True
    for key in sorted(all_timetables.keys()):
        tt = all_timetables[key]
        if not first:
            story.append(PageBreak())
        first = False

        class_num = tt.get("class_num")
        section = tt.get("section")
        sports_day = tt.get("sports_day", "")
        sp = tt.get("sports_periods", (0, 0))

        story.append(Paragraph("Sri Vidya Niketan School", styles["Title"]))
        story.append(Paragraph(
            f"Class {class_num} — Section {section} | Weekly Timetable",
            styles["Heading2"],
        ))
        story.append(Paragraph(
            f"Sports Day: {sports_day}  (Periods {sp[0]} &amp; {sp[1]})",
            styles["Normal"],
        ))
        story.append(Spacer(1, 0.3 * cm))

        schedule = tt.get("schedule", {})
        col_header = ["Period / Time"] + DAYS
        plain_rows = [col_header]

        for entry in DISPLAY_TIMELINE:
            if entry["type"] in ("break", "lunch"):
                plain_rows.append([f"{entry['label']}  {entry['time']}"] + [""] * len(DAYS))
            else:
                p = entry["number"]
                start, end = PERIOD_TIMES[p]
                row = [f"P{p}  {start}–{end}"]
                for day in DAYS:
                    slots = schedule.get(day, [])
                    slot = next((s for s in slots if s.get("period") == p), None)
                    row.append(f"{slot['subject']}\n{slot['teacher']}" if slot else "—")
                plain_rows.append(row)

        col_widths = [3.2 * cm] + [3.7 * cm] * len(DAYS)
        tbl = Table(plain_rows, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#2C3E50")),
            ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 7),
            ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("GRID",        (0, 0), (-1, -1), 0.4, colors.grey),
        ]))
        story.append(tbl)

    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    """Simple health-check endpoint."""
    return jsonify({"status": "ok"})


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    # Only admin can upload teacher data
    if current_user.role != "admin":
        return jsonify({"success": False, "error": "Admin access required."}), 403

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file field in request."}), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify({"success": False, "error": "No file selected."}), 400

    try:
        teachers_data = parse_teachers_csv(uploaded)
        if not teachers_data:
            return jsonify({"success": False, "error": "No teacher records found."}), 400

        # Generate timetables
        csp = TimetableCSP(teachers_data)
        all_tt = csp.generate_all_timetables()

        # ── Save to MySQL using raw SQL ──────────────────────────────
        user_id = current_user.id

        # INSERT INTO teacher_uploads (...) VALUES (...)
        save_teacher_upload(user_id, teachers_data)

        # INSERT INTO timetables (...) VALUES (...)
        # ON DUPLICATE KEY UPDATE schedule_json = VALUES(schedule_json), ...
        save_timetables(user_id, all_tt)

        # Cache in memory
        _user_timetables[user_id] = all_tt

        # Build RAG index
        try:
            _store["rag_engine"].build_index(all_tt)
        except Exception as rag_err:
            logger.warning("RAG index build failed (non-fatal): %s", rag_err)

        total_periods = 20 * 6 * 8
        tbd_count = _count_tbd(all_tt)

        return jsonify({
            "success": True,
            "timetables": all_tt,
            "stats": {
                "total_classes": 20,
                "total_periods": total_periods,
                "tbd_count": tbd_count,
            },
        })
    except Exception as exc:
        logger.exception("Error during upload processing.")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/timetables", methods=["GET"])
@login_required
def get_all_timetables():
    """Return timetables scoped by role:
       admin   → all 20 timetables (their own)
       teacher → all timetables (for chatbot / browsing)
       student → only their class+section
    """
    # Resolve which admin's timetables to load
    if current_user.role == "admin":
        user_id = current_user.id
    else:
        # SELECT * FROM users WHERE role = 'admin' LIMIT 1
        admin = find_first_admin()
        user_id = admin.id if admin else current_user.id

    # Load from memory or MySQL
    if user_id not in _user_timetables:
        # SELECT * FROM timetables WHERE user_id = %s
        all_tt = load_timetables(user_id)
        if not all_tt:
            return jsonify({"error": "No timetables found. Please ask the admin to upload teacher data."}), 404
        _user_timetables[user_id] = all_tt
        _store["rag_engine"].build_index(all_tt)

    all_tt = _user_timetables[user_id]

    # Student: filter to their registered class only
    if current_user.role == "student":
        cn  = current_user.class_num
        sec = (current_user.section or "").upper()
        key = f"Class_{cn}{sec}"
        tt  = all_tt.get(key)
        if not tt:
            return jsonify({"error": f"No timetable found for Class {cn}{sec}."}), 404
        return jsonify({key: tt})

    return jsonify(all_tt)


@app.route("/api/teacher/my-schedule", methods=["GET"])
@login_required
def teacher_my_schedule():
    """Return a personal schedule for the logged-in teacher."""
    if current_user.role != "teacher":
        return jsonify({"error": "Teacher access only."}), 403

    teacher_name = current_user.teacher_name
    if not teacher_name:
        return jsonify({"error": "No teacher name set on this account."}), 400

    # Load timetables from admin
    # SELECT * FROM users WHERE role = 'admin' LIMIT 1
    admin = find_first_admin()
    if not admin:
        return jsonify({"error": "No timetables available yet."}), 404

    user_id = admin.id
    if user_id not in _user_timetables:
        # SELECT * FROM timetables WHERE user_id = %s
        all_tt = load_timetables(user_id)
        if not all_tt:
            return jsonify({"error": "No timetables available yet."}), 404
        _user_timetables[user_id] = all_tt

    all_tt = _user_timetables[user_id]

    # Build teacher's personal schedule: day -> list of {period, time, subject, class_num, section}
    from csp_scheduler import PERIOD_TIMES, DAYS as SCHED_DAYS
    my_schedule = {day: [] for day in SCHED_DAYS}

    for key, tt in all_tt.items():
        class_num = tt["class_num"]
        section   = tt["section"]
        for day, slots in tt.get("schedule", {}).items():
            for slot in slots:
                if slot.get("teacher", "").lower() == teacher_name.lower():
                    period = slot.get("period")
                    start, end = PERIOD_TIMES.get(period, ("", ""))
                    my_schedule[day].append({
                        "period":    period,
                        "time":      f"{start} - {end}",
                        "subject":   slot.get("subject", ""),
                        "class_num": class_num,
                        "section":   section,
                    })

    # Sort each day's slots by period number
    for day in my_schedule:
        my_schedule[day].sort(key=lambda x: x["period"])

    return jsonify({
        "teacher_name": teacher_name,
        "schedule":     my_schedule,
    })


@app.route("/api/timetable/<int:class_num>/<section>", methods=["GET"])
@login_required
def get_timetable(class_num: int, section: str):
    """Return timetable for a specific class-section (current user)."""
    section = section.upper()
    user_id = current_user.id
    user_tt = _user_timetables.get(user_id)
    if not user_tt:
        return jsonify({"error": "No timetables found. Please upload a CSV first."}), 404
    key = f"Class_{class_num}{section}"
    tt  = user_tt.get(key)
    if tt is None:
        return jsonify({"error": f"Timetable for {key} not found."}), 404
    return jsonify(tt)


@app.route("/api/ask", methods=["POST"])
@login_required
def ask():
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided."}), 400
    try:
        result = _store["rag_engine"].query(question)
        return jsonify(result)
    except Exception as exc:
        logger.exception("RAG query failed.")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/export/pdf/<int:class_num>/<section>", methods=["GET"])
@login_required
def export_pdf_single(class_num: int, section: str):
    section = section.upper()
    user_tt = _user_timetables.get(current_user.id, {})
    tt = user_tt.get(f"Class_{class_num}{section}")
    if tt is None:
        return jsonify({"error": f"Timetable not found."}), 404
    try:
        pdf_bytes = _generate_pdf_for_timetable(tt)
    except Exception as exc:
        return jsonify({"error": f"PDF generation failed: {exc}"}), 500
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"timetable_class{class_num}{section}.pdf")


@app.route("/api/export/pdf/all", methods=["GET"])
@login_required
def export_pdf_all():
    user_tt = _user_timetables.get(current_user.id)
    if not user_tt:
        return jsonify({"error": "No timetables found."}), 404
    try:
        pdf_bytes = _generate_pdf_all(user_tt)
    except Exception as exc:
        return jsonify({"error": f"PDF generation failed: {exc}"}), 500
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name="all_timetables.pdf")


@app.route("/api/sample-csv", methods=["GET"])
def sample_csv():
    """Return the sample teachers.csv file for download."""
    sample_path = os.path.abspath(_SAMPLE_CSV_PATH)
    if not os.path.exists(sample_path):
        return jsonify({"error": "Sample CSV not found on server."}), 404

    return send_file(
        sample_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="teachers_sample.csv",
    )


# ---------------------------------------------------------------------------
# Frontend catch-all — serve index.html for any non-API route
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the main frontend page."""
    return send_file(os.path.join(_FRONTEND_DIR, "index.html"))


@app.route("/<path:filename>")
def frontend_static(filename):
    """Serve any other frontend file (style.css, script.js, etc.)."""
    filepath = os.path.join(_FRONTEND_DIR, filename)
    if os.path.isfile(filepath):
        return send_file(filepath)
    # Fallback to index.html for SPA routing
    return send_file(os.path.join(_FRONTEND_DIR, "index.html"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import threading
    import webbrowser

    # Force UTF-8 output so print() works on Windows terminals
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    PORT = 5000
    URL = f"http://localhost:{PORT}"

    # Open the browser 1.5 seconds after the server starts (non-blocking)
    def _open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(URL)

    threading.Thread(target=_open_browser, daemon=True).start()

    print("")
    print("  *** Smart Timetable Scheduler is running! ***")
    print(f"  --> Open: {URL}")
    print("  --> Press CTRL+C to stop")
    print("")

    app.run(debug=False, port=PORT, use_reloader=False)
