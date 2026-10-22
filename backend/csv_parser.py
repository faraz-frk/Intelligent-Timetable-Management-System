"""
csv_parser.py
-------------
Module for parsing and validating the teachers data file.

Supported file formats
----------------------
  CSV  (.csv)  — comma-separated, semicolons used as internal delimiters
  Excel (.xlsx, .xls) — same column structure, semicolons in cells for multi-values

Public API
----------
parse_teachers_csv(file_path_or_buffer) -> list[dict]
validate_csv(df)                         -> (bool, list[str])
"""

import io
import logging
from typing import IO, List, Tuple, Union

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "teacher_name",
    "subject",
    "classes",
    "available_days",
    "available_periods",
    "max_periods_per_day",
]

VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
VALID_PERIODS = set(range(1, 9))          # 1-8 inclusive
VALID_SUBJECTS = {
    "Telugu", "Hindi", "English", "Social", "Maths",
    "Environmental Science", "Computer",
    "Physics", "Biology", "Sports",
}
VALID_CLASSES = set(range(1, 11))         # 1-10 inclusive

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _split_semicolon(value: str) -> List[str]:
    """Split a semicolon-delimited string, stripping whitespace from each part."""
    return [v.strip() for v in str(value).split(";") if v.strip()]


def _parse_classes(raw: str) -> List[int]:
    """Convert '1;2;3' → [1, 2, 3] as integers."""
    parts = _split_semicolon(raw)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            logger.warning("Could not convert class value '%s' to int; skipping.", p)
    return result


def _parse_periods(raw: str) -> List[int]:
    """Convert '1;2;3;4;5;6;7;8' → [1, 2, 3, 4, 5, 6, 7, 8] as integers."""
    parts = _split_semicolon(raw)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            logger.warning("Could not convert period value '%s' to int; skipping.", p)
    return result


# ---------------------------------------------------------------------------
# Core public functions
# ---------------------------------------------------------------------------

def parse_teachers_csv(file_path_or_buffer: Union[str, IO]) -> List[dict]:
    """
    Parse a teachers CSV file and return a list of structured teacher dicts.

    Parameters
    ----------
    file_path_or_buffer : str | file-like object
        Absolute path to the CSV file or any file-like object (e.g. BytesIO
        from a Flask upload).

    Returns
    -------
    list[dict]
        Each dict contains:
          - name               (str)
          - subject            (str)
          - classes            (list[int])
          - available_days     (list[str])
          - available_periods  (list[int])
          - max_periods_per_day (int)

    Raises
    ------
    ValueError
        If required columns are missing or the file cannot be parsed.
    """
    # ---- Detect format and load -----------------------------------------
    try:
        filename = getattr(file_path_or_buffer, 'filename', '') or ''
        is_excel = filename.lower().endswith(('.xlsx', '.xls'))

        if isinstance(file_path_or_buffer, (str, bytes)):
            path = str(file_path_or_buffer)
            if path.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(path, dtype=str)
            else:
                df = pd.read_csv(path, dtype=str)
        else:
            raw = file_path_or_buffer.read()
            if is_excel:
                df = pd.read_excel(io.BytesIO(raw), dtype=str)
            else:
                # Try CSV first; fall back to Excel if it fails
                try:
                    text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
                    df = pd.read_csv(io.StringIO(text), dtype=str)
                except Exception:
                    df = pd.read_excel(io.BytesIO(raw if isinstance(raw, bytes) else raw.encode()), dtype=str)
    except Exception as exc:
        raise ValueError(f"Failed to read file: {exc}") from exc

    # Strip leading/trailing whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    # ---- Validate columns -----------------------------------------------
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # ---- Build teacher list ---------------------------------------------
    teachers: List[dict] = []
    for idx, row in df.iterrows():
        try:
            teacher = {
                "name": str(row["teacher_name"]).strip(),
                "subject": str(row["subject"]).strip(),
                "classes": _parse_classes(row["classes"]),
                "available_days": _split_semicolon(row["available_days"]),
                "available_periods": _parse_periods(row["available_periods"]),
                "max_periods_per_day": int(str(row["max_periods_per_day"]).strip()),
            }
            teachers.append(teacher)
        except Exception as exc:
            logger.warning("Skipping row %d due to parse error: %s", idx, exc)

    logger.info("Parsed %d teacher records from CSV.", len(teachers))
    return teachers


# ---------------------------------------------------------------------------

def validate_csv(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate a teachers DataFrame and return (is_valid, errors).

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame already loaded from CSV (columns NOT yet stripped of spaces;
        this function handles that).

    Returns
    -------
    (bool, list[str])
        ``True`` and an empty list when the data is valid; ``False`` and a
        list of human-readable error messages when issues are found.
    """
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]

    errors: List[str] = []

    # ---- Required columns -----------------------------------------------
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns: {missing_cols}")
        # Cannot validate further without the required columns
        return False, errors

    # Strip string values
    for col in REQUIRED_COLUMNS:
        df[col] = df[col].astype(str).str.strip()

    # ---- Row-level validation -------------------------------------------
    for idx, row in df.iterrows():
        row_id = f"Row {idx + 2}"  # +2 for 1-indexed + header row

        # teacher_name must not be blank
        name = row["teacher_name"]
        if not name or name.lower() == "nan":
            errors.append(f"{row_id}: teacher_name is empty.")
            continue  # Skip further checks for this row

        # subject must be in known set
        subject = row["subject"]
        if subject not in VALID_SUBJECTS:
            errors.append(
                f"{row_id} ({name}): Unknown subject '{subject}'. "
                f"Valid subjects: {sorted(VALID_SUBJECTS)}"
            )

        # classes
        classes = _parse_classes(row["classes"])
        if not classes:
            errors.append(f"{row_id} ({name}): 'classes' field is empty or unparseable.")
        else:
            bad_classes = [c for c in classes if c not in VALID_CLASSES]
            if bad_classes:
                errors.append(
                    f"{row_id} ({name}): Invalid class numbers {bad_classes}. "
                    f"Must be 1–10."
                )

        # available_days
        days = _split_semicolon(row["available_days"])
        if not days:
            errors.append(f"{row_id} ({name}): 'available_days' field is empty.")
        else:
            bad_days = [d for d in days if d not in VALID_DAYS]
            if bad_days:
                errors.append(
                    f"{row_id} ({name}): Invalid day abbreviations {bad_days}. "
                    f"Valid: {sorted(VALID_DAYS)}"
                )

        # available_periods
        periods = _parse_periods(row["available_periods"])
        if not periods:
            errors.append(
                f"{row_id} ({name}): 'available_periods' field is empty or unparseable."
            )
        else:
            bad_periods = [p for p in periods if p not in VALID_PERIODS]
            if bad_periods:
                errors.append(
                    f"{row_id} ({name}): Invalid period numbers {bad_periods}. "
                    f"Must be 1–8."
                )

        # max_periods_per_day
        try:
            mpd = int(row["max_periods_per_day"])
            if mpd < 1 or mpd > 8:
                errors.append(
                    f"{row_id} ({name}): max_periods_per_day={mpd} is out of range (1–8)."
                )
        except ValueError:
            errors.append(
                f"{row_id} ({name}): max_periods_per_day '{row['max_periods_per_day']}' "
                f"is not a valid integer."
            )

    is_valid = len(errors) == 0
    return is_valid, errors
