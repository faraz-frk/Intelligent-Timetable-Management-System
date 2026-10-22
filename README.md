# Intelligent Timetable Management System

A full-stack timetable application for schools. It generates clash-aware weekly timetables for Classes 1–10 (Sections A and B), provides role-based access, supports PDF export, and includes a natural-language timetable assistant.

## Features

- Constraint-based timetable generation for 20 class sections.
- Admin, teacher, and student accounts.
- Teacher availability, subject, class, day, period, and daily workload constraints.
- CSV, `.xlsx`, and `.xls` teacher-file upload.
- Timetable browsing and PDF export for one class or all classes.
- Local semantic search with `sentence-transformers`.
- Optional Groq-powered natural-language answers, with deterministic timetable lookups as a fallback.
- MySQL persistence using PyMySQL and raw SQL.

## Requirements

Install these before setup:

- Windows 10/11 (the commands below use Windows paths).
- Python 3.10 or newer.
- MySQL Server 8 or newer, running locally or reachable over the network.
- Node.js/npm is optional; it is only needed for the convenience scripts in `package.json`.

## Setup on a new computer

### 1. Copy the project

Copy or clone the project to a local folder. Do not copy the existing `venv` folder between computers; virtual environments contain machine-specific paths and binaries.

### 2. Create the MySQL database

Start MySQL and create the database:

```sql
CREATE DATABASE timetable_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Use an existing MySQL user or create a dedicated one. The user must have permission to create tables in `timetable_db`.

### 3. Create and configure `.env`

Create a file named `.env` in the project root. Use your own database password and secret key:

```env
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=timetable_db

SECRET_KEY=replace-with-a-long-random-secret

# Optional. Without this key, the chatbot uses local deterministic answers.
GROQ_API_KEY=your_groq_api_key
```

Never commit `.env` or share its contents. The first application startup creates the required MySQL tables automatically.

### 4. Create the Python virtual environment

Open PowerShell in the project folder:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `py` is not available, use:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

The first chatbot index build downloads the `all-MiniLM-L6-v2` embedding model from Hugging Face (approximately 90 MB). The model is cached for later runs.

### 5. Start the application

```powershell
.\venv\Scripts\python.exe backend\app.py
```

The server starts at <http://localhost:5000> and normally opens the browser automatically. If it does not, open the URL manually.

To stop the server, press `Ctrl+C`.

### Optional npm commands

If Node.js is installed, these commands provide shortcuts:

```powershell
npm run setup       # Creates venv and installs requirements
npm run dev         # Starts the application
```

For a new computer, the manual Python commands above are recommended because they make the selected Python interpreter explicit.

## First use

1. Open the login page and register an admin account.
2. Log in as the admin.
3. Upload `sample_data\teachers.csv`, or upload a file using the format below.
4. Wait for all 20 timetables to be generated and the search index to finish building.
5. Browse classes, ask chatbot questions, or export PDFs.
6. Register teacher and student accounts as needed.

Generated timetables are saved in the MySQL `timetables` table and are loaded again
when the application restarts. Uploading the teacher file again generates a new
schedule and replaces the previously saved schedule for each class-section.
The scheduler uses randomized candidate ordering, so repeated generations can
produce different valid timetables even when the uploaded teacher data is the
same. Keep the existing saved schedule if you want to preserve it.

## User roles

- **Admin**: Upload teacher data, generate timetables, browse every class, and export PDFs.
- **Teacher**: Register with the exact teacher name from the uploaded file and view the teacher schedule.
- **Student**: Register with a class and section and view that timetable.

## Teacher file format

The upload file must contain these columns. CSV fields with multiple values use semicolons:

| Column | Example | Description |
|---|---|---|
| `teacher_name` | `Ravi Kumar` | Teacher's full name |
| `subject` | `Maths` | Subject taught |
| `classes` | `1;2;3` | Classes the teacher can teach |
| `available_days` | `Mon;Tue;Wed;Thu;Fri;Sat` | Available days |
| `available_periods` | `1;2;3;4;5;6;7;8` | Available periods |
| `max_periods_per_day` | `8` | Maximum periods per day |

Supported subjects and class ranges are defined by the scheduler and sample file. The bundled sample file contains 49 teacher records.

## School schedule

| Slot | Time |
|---|---|
| Period 1 | 09:00–09:45 |
| Period 2 | 09:45–10:30 |
| Break | 10:30–10:40 |
| Period 3 | 10:40–11:25 |
| Period 4 | 11:25–12:10 |
| Lunch | 12:10–13:00 |
| Periods 5–8 | 13:00–16:00 |

## Project structure

```text
automatic timetable/
├── backend/
│   ├── app.py               # Flask server, API routes, PDF export
│   ├── auth.py              # Registration, login, logout, current-user API
│   ├── models.py            # MySQL connection and raw SQL helpers
│   ├── csp_scheduler.py     # Constraint-based scheduling engine
│   ├── rag_engine.py        # Semantic search and chatbot answers
│   └── csv_parser.py        # CSV/Excel parsing and validation
├── frontend/
│   ├── index.html           # Main application
│   ├── login.html           # Login and registration
│   ├── style.css            # Main styles
│   ├── login.css            # Login styles
│   └── script.js            # Frontend behavior and API calls
├── sample_data/
│   └── teachers.csv         # Example teacher data
├── requirements.txt
├── package.json
├── .env                    # Local secrets; do not commit
└── venv/                   # Local-only virtual environment
```

## Troubleshooting

### MySQL connection error

Confirm that MySQL is running, the database exists, and the `MYSQL_*` values in `.env` are correct. The application cannot create tables until it can connect to MySQL.

### `ModuleNotFoundError`

Install dependencies using the virtual environment's interpreter:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Chatbot says the index is not built

Upload the teacher file again and wait for the upload request to finish. The server log should show that the RAG index was built. If the embedding model download is interrupted, rerun the upload after restoring network access.

### Port 5000 is already in use

Stop the other process using port 5000, then start the application again.
