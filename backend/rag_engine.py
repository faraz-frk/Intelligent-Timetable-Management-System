"""
rag_engine.py
-------------
Retrieval-Augmented Generation (RAG) engine for timetable Q&A.

Uses:
  • sentence-transformers (all-MiniLM-L6-v2) for dense embeddings
  • sklearn NearestNeighbors for vector similarity search
  • Groq (Llama 3.1-8b-instant) for natural language answer generation
  • Direct timetable lookup for structured queries (who teaches, what subject)

Public API
----------
engine = RAGEngine()
engine.build_index(all_timetables)
result = engine.query("Who teaches Maths to Class 5A?")
# result -> {"answer": str, "sources": list[str]}
"""

import logging
import os
import re
from typing import Dict, List, Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()  # loads GROQ_API_KEY from .env

logger = logging.getLogger(__name__)

_MODEL_NAME   = "all-MiniLM-L6-v2"
_GROQ_MODEL   = "llama-3.1-8b-instant"
_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


class RAGEngine:
    """
    RAG engine for timetable Q&A.
    - Retrieval : sentence-transformers + NearestNeighbors (always)
    - Generation: Groq Llama 3.1-8b-instant (if API key set)
                  Direct timetable lookup for structured questions
                  Rule-based fallback if Groq unavailable
    """

    def __init__(self) -> None:
        self._model = None                    # SentenceTransformer (lazy)
        self.index  = None                    # NearestNeighbors or None
        self._dim   = None
        self.documents: List[str] = []
        self.all_timetables: Optional[dict] = None
        self._groq_client = None              # Groq client (lazy)

    # ------------------------------------------------------------------
    # Groq client
    # ------------------------------------------------------------------

    def _get_groq(self):
        """Lazily initialise the Groq client. Returns None if no API key."""
        if self._groq_client is not None:
            return self._groq_client
        if not _GROQ_API_KEY or _GROQ_API_KEY == "your_groq_api_key_here":
            return None
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=_GROQ_API_KEY)
            logger.info("Groq client initialised (model=%s)", _GROQ_MODEL)
            return self._groq_client
        except Exception as e:
            logger.warning("Groq client init failed: %s", e)
            return None

    def _ask_groq(self, question: str, context_docs: List[str]) -> Optional[str]:
        """
        Send question + retrieved timetable context to Groq LLM.
        Returns the answer string, or None if Groq is unavailable.
        """
        client = self._get_groq()
        if not client:
            return None

        context = "\n".join(f"- {doc}" for doc in context_docs[:12])
        prompt = f"""You are a helpful school timetable assistant.
Understand the user's intent before answering. Correct minor spelling mistakes,
abbreviations, spacing differences, and natural variations in class, section,
subject, teacher, and day names using the timetable context. For example,
"comptuer" can mean "Computer" when the context supports that correction.
Answer using ONLY the timetable information provided below.
Answer only the question asked. Never mention retrieval, context, or documents,
and never list unrelated timetable entries.
For yes/no questions, answer yes or no and include matching period(s) when available.
For "how many periods" questions, return the number of matching periods.
If the requested information cannot be identified confidently, say
"I could not find that information."

Timetable Context:
{context}

Question: {question}
Answer:"""

        try:
            response = client.chat.completions.create(
                model=_GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150,
            )
            answer = response.choices[0].message.content.strip()
            logger.info("Groq answered: %s", answer[:80])
            return answer
        except Exception as e:
            logger.warning("Groq query failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Internal: lazy loader
    # ------------------------------------------------------------------

    def _load_model(self):
        """Load the SentenceTransformer model (once)."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
            logger.info("Loading SentenceTransformer model: %s", _MODEL_NAME)
            self._model = SentenceTransformer(_MODEL_NAME)
            logger.info("Model loaded.")

    def _embed(self, texts: List[str]) -> np.ndarray:
        """Embed a list of texts and return a float32 numpy array."""
        self._load_model()
        embeddings = self._model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embeddings.astype(np.float32)

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def build_index(self, all_timetables: dict) -> None:
        """
        Convert timetable data to text documents and build an hnswlib index.

        Three document types are created:
        1. **Slot-centric**: "Class N Section S has <subject> with <teacher>
                             on <day> at Period <p> (<time>)"
        2. **Teacher-centric**: "Teacher <name> teaches <subject> to Class
                                 N<S> on <day> Period <p>"
        3. **Day-centric**: "On <day>, Class N<S> has: Period 1: <subj>(<teacher>), ..."
        """
        self.all_timetables = all_timetables
        docs: List[str] = []

        for key, tt in all_timetables.items():
            class_num = tt.get("class_num")
            section = tt.get("section")
            schedule = tt.get("schedule", {})

            for day, periods in schedule.items():
                day_summary_parts: List[str] = []

                for slot in periods:
                    subject = slot.get("subject", "Unknown")
                    teacher = slot.get("teacher", "Unknown")
                    period = slot.get("period", "?")
                    time_str = slot.get("time", "")

                    # 1. Slot-centric document
                    slot_doc = (
                        f"Class {class_num} Section {section} has {subject} "
                        f"with {teacher} on {day} at Period {period} ({time_str})."
                    )
                    docs.append(slot_doc)

                    # 2. Teacher-centric document
                    if teacher and teacher != "TBD":
                        teacher_doc = (
                            f"Teacher {teacher} teaches {subject} to "
                            f"Class {class_num}{section} on {day} Period {period} ({time_str})."
                        )
                        docs.append(teacher_doc)

                    # Accumulate for day-centric summary
                    day_summary_parts.append(
                        f"Period {period}: {subject} ({teacher})"
                    )

                # 3. Day-centric document
                day_doc = (
                    f"On {day}, Class {class_num}{section} has: "
                    + ", ".join(day_summary_parts)
                    + "."
                )
                docs.append(day_doc)

            # Sports info document
            sports_day = tt.get("sports_day", "Unknown")
            sp = tt.get("sports_periods", (0, 0))
            sports_doc = (
                f"Class {class_num} Section {section} has Sports on {sports_day} "
                f"at Periods {sp[0]} and {sp[1]}."
            )
            docs.append(sports_doc)

        self.documents = docs
        logger.info("Built %d RAG documents.", len(docs))

        # ---- Embed and build sklearn NearestNeighbors index -----------
        embeddings = self._embed(docs)
        dim = embeddings.shape[1]
        self._dim = dim
        self._embeddings_matrix = embeddings  # store for query

        from sklearn.neighbors import NearestNeighbors  # noqa: PLC0415
        self.index = NearestNeighbors(n_neighbors=min(10, len(docs)), algorithm='brute', metric='cosine')
        self.index.fit(embeddings)
        logger.info("sklearn NearestNeighbors index built with %d vectors (dim=%d).", len(docs), dim)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, question: str, top_k: int = 10) -> Dict[str, object]:
        """
        Retrieve top_k relevant documents and generate a template-based answer.

        Returns
        -------
        dict
            {"answer": str, "sources": list[str]}
        """
        if self.index is None or not self.documents:
            return {
                "answer": (
                    "The timetable index has not been built yet. "
                    "Please upload a teachers CSV first."
                ),
                "sources": [],
            }

        # Embed question
        q_vec = self._embed([question])
        k = min(top_k, len(self.documents))
        distances, indices = self.index.kneighbors(q_vec, n_neighbors=k)

        retrieved: List[str] = []
        for idx in indices[0]:
            if 0 <= idx < len(self.documents):
                retrieved.append(self.documents[idx])

        answer = self._format_answer(question, retrieved)
        return {"answer": answer, "sources": retrieved}

    # ------------------------------------------------------------------
    # Template-based answer generation
    # ------------------------------------------------------------------

    def _format_answer(self, question: str, retrieved_docs: List[str]) -> str:
        """
        Generate an answer with Groq using the retrieved timetable context.

        Intent detection, spelling correction, and answer formatting are delegated
        to the language model instead of brittle keyword and name parsing.
        """
        q = question.lower().strip()

        if self._match_who_teaches(q):
            return self._answer_who_teaches(q, retrieved_docs)
        if self._match_all_teachers(q):
            return self._answer_all_teachers(q)
        if self._match_all_subjects(q):
            return self._answer_all_subjects(q)
        if self._match_what_subject(q):
            return self._answer_what_subject(q, retrieved_docs)
        if self._match_has_subject(q):
            return self._answer_has_subject(q)
        if self._match_when(q):
            return self._answer_when(q, retrieved_docs)
        if self._match_schedule(q):
            return self._answer_schedule(q, retrieved_docs)
        if "sports" in q:
            return self._answer_sports(q, retrieved_docs)
        if "how many" in q and "period" in q:
            return self._answer_how_many(q, retrieved_docs)

        groq_answer = self._ask_groq(question, retrieved_docs)
        if groq_answer:
            return groq_answer

        return self._fallback_answer(question, retrieved_docs)

    # ------------------------------------------------------------------
    # Pattern matchers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_who_teaches(q: str) -> bool:
        # Explicit teacher-question keywords
        if ("who teach" in q or "which teacher" in q or "who is the teacher" in q
                or ("who" in q and "teach" in q)):
            return True
        # Natural phrasings: "who goes to social for class 1A"
        #                    "who handles maths in class 5"
        #                    "who takes english for 3B"
        #                    "who is for physics class 9"
        if "who" in q and re.search(
            r'\b(telugu|hindi|english|maths|math|mathematics|social|physics|'
            r'biology|environmental science|computer|sports|science|chemistry|'
            r'history|geography|moral|art|music)\b', q, re.IGNORECASE
        ):
            return True
        return False

    @staticmethod
    def _match_all_teachers(q: str) -> bool:
        """Match questions asking for ALL teachers of a class."""
        return (
            ("teacher" in q or "teachers" in q)
            and ("class" in q or "section" in q)
            and not ("teach" in q and re.search(r'\b(math|maths|english|hindi|telugu|physics|biology|social|computer|sports|science)\b', q))
        )

    @staticmethod
    def _match_what_subject(q: str) -> bool:
        # Keep plural class-subject questions in _match_all_subjects().
        return re.search(r"\b(?:what|which)\s+subject\b(?!s)", q) is not None

    @staticmethod
    def _match_when(q: str) -> bool:
        """Match all variations: 'when is', 'when does', 'when do', 'what time',
        'which period', 'what period', 'when is Maths for class 4A', etc."""
        return any(kw in q for kw in [
            "when is", "when does", "when do", "when are",
            "what time", "which period", "what period",
            "which day", "what day", "on which day",
        ])

    @staticmethod
    def _match_schedule(q: str) -> bool:
        if any(kw in q for kw in [
            "schedule for", "timetable for", "full schedule", "schedule", "timetable"
        ]):
            return True

        has_class = re.search(r"\bclass\s*\d+\s*(?:section\s*)?[ab]\b", q) is not None
        has_day = RAGEngine._extract_day(q) is not None
        return has_class and has_day and any(
            kw in q for kw in ("what happens", "what's happening", "what is happening", "about")
        )

    @staticmethod
    def _match_has_subject(q: str) -> bool:
        """Match yes/no questions asking whether a class has a subject on a day."""
        return (
            ("does " in q or "do " in q or "is " in q)
            and any(day in q for day in (
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
            ))
            and re.search(
                r"\b(telugu|hindi|english|maths|math|mathematics|social|physics|"
                r"biology|environmental science|computer|sports|science|chemistry|"
                r"history|geography|moral|art|music|pe)\b",
                q,
                re.IGNORECASE,
            ) is not None
        )

    @staticmethod
    def _match_all_subjects(q: str) -> bool:
        """Match 'list all subjects for class 8A', 'subjects in class 5B',
        'what subjects does class 3 have', 'what subjects does class 6A have',
        'which subjects are in class 2A', 'subjects of class 7B'."""
        subject_kws = ("subject", "subjects", "list subject", "all subject")
        class_kws   = ("class", "section")
        # Also match "what subjects" even without explicit "class" if a number follows
        if any(kw in q for kw in subject_kws):
            if any(kw in q for kw in class_kws):
                return True
            # Catch "subjects for 5A", "subjects of 3B"
            if re.search(r'\d+\s*[ab]?\b', q):
                return True
        return False

    # ------------------------------------------------------------------
    # Specialised answer builders
    # ------------------------------------------------------------------

    def _answer_all_subjects(self, q: str) -> str:
        """Return the unique subjects taught in the requested class/section."""
        if not self.all_timetables:
            return "No timetable data available."

        cn, sec = self._parse_class_section(q)
        if cn is None:
            # No specific class — list subjects across all classes
            subjects = set()
            for tt in self.all_timetables.values():
                for slots in tt.get("schedule", {}).values():
                    for slot in slots:
                        subj = slot.get("subject", "")
                        if subj and subj.lower() not in ("free", "tbd", ""):
                            subjects.add(subj)
            if not subjects:
                return "I could not find subject information."
            return "Subjects taught across all classes: " + ", ".join(sorted(subjects)) + "."

        key = f"Class_{cn}{sec}"
        tt  = self.all_timetables.get(key)
        if not tt:
            return f"No timetable found for Class {cn}{sec}."

        subjects = set()
        for slots in tt.get("schedule", {}).values():
            for slot in slots:
                subj = slot.get("subject", "")
                if subj and subj.lower() not in ("free", "tbd", ""):
                    subjects.add(subj)

        if not subjects:
            return f"No subjects found for Class {cn}{sec}."
        return f"Subjects for Class {cn}{sec}: " + ", ".join(sorted(subjects)) + "."

    def _answer_all_teachers(self, q: str) -> str:
        """Return just the unique teacher names for a class — no periods/times."""
        cn, sec = self._parse_class_section(q)
        if not self.all_timetables or cn is None:
            return "Please specify a class number (e.g. 'teachers for Class 1A')."

        sections = [sec] if sec else ["A", "B"]

        all_names: set = set()
        for s in sections:
            tt = self.all_timetables.get(f"Class_{cn}{s}")
            if not tt:
                continue
            for day_slots in tt.get("schedule", {}).values():
                for slot in day_slots:
                    t = slot.get("teacher", "")
                    if t and t not in ("TBD", "Not Assigned", "—", ""):
                        all_names.add(t)

        if not all_names:
            return f"No teacher data found for Class {cn}{sec or ''}."

        label = f"Class {cn}{sec}" if sec else f"Class {cn} (both sections)"
        names_str = ", ".join(sorted(all_names))
        return f"Teachers for {label}:\n{names_str}"

    def _answer_who_teaches(self, q: str, docs: List[str]) -> str:
        """Answer 'who teaches X to class Y?' by direct timetable lookup."""
        # Extract subject
        subj_match = re.search(
            r"\b(telugu|hindi|english|maths|math|mathematics|social|physics|biology|"
            r"environmental science|computer|sports|science|chemistry|history|geography|moral|art|music)\b",
            q, re.IGNORECASE
        )
        subject = subj_match.group(1).strip().title() if subj_match else None
        if subject and subject.lower() in ("mathematics", "math"):
            subject = "Maths"
        if subject and subject.lower() == "environmental science":
            subject = "Environmental Science"

        # Extract class number and optional section
        cn, sec = self._parse_class_section(q)

        # ── Direct lookup from stored timetables (most reliable) ──────────
        if self.all_timetables and cn is not None:
            sections = [sec] if sec else ["A", "B"]

            results = []
            for s in sections:
                key = f"Class_{cn}{s}"
                tt = self.all_timetables.get(key)
                if not tt:
                    continue
                teachers_found: set = set()
                for day_slots in tt.get("schedule", {}).values():
                    for slot in day_slots:
                        slot_subj = slot.get("subject", "")
                        if subject is None or slot_subj.lower() == subject.lower():
                            t = slot.get("teacher", "")
                            if t and t not in ("TBD", "Not Assigned", "—", ""):
                                teachers_found.add(t)
                if teachers_found:
                    t_str = " and ".join(sorted(teachers_found))
                    subj_label = subject or "subjects"
                    results.append(f"{t_str} teaches {subj_label} to Class {cn}{s}.")

            if results:
                return "\n".join(results)
            subj_label = subject or "that subject"
            return f"No teacher found for {subj_label} in Class {cn}{sec or ''}."

        # ── Fallback: parse retrieved docs ────────────────────────────────
        teachers: List[str] = []
        for doc in docs:
            # teacher-centric: "Teacher X teaches Y to Class NZ on..."
            m = re.match(r"Teacher\s+(.+?)\s+teaches", doc, re.IGNORECASE)
            if m:
                t = m.group(1).strip()
                if t not in teachers:
                    teachers.append(t)
            # slot-centric: "Class N Section S has Y with X on..."
            m2 = re.search(r"\bwith\s+(.+?)\s+on\s+\w+day\b", doc, re.IGNORECASE)
            if m2:
                t = m2.group(1).strip()
                if t not in teachers:
                    teachers.append(t)

        if teachers:
            t_str = " and ".join(teachers)
            subj_label = subject or "the subject"
            if cn is not None:
                return f"{t_str} teaches {subj_label} to Class {cn}{sec or ''}."
            return f"{t_str} teaches {subj_label}."

        return "I could not find that information in the timetable."

    def _answer_what_subject(self, q: str, docs: List[str]) -> str:
        """Answer 'what subject does Teacher X teach?' by direct lookup."""
        t_match = re.search(r"(?:does|do)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+teach", q, re.IGNORECASE)
        teacher_name = t_match.group(1).strip() if t_match else None

        # Direct lookup
        if self.all_timetables and teacher_name:
            subjects_found: set = set()
            for tt in self.all_timetables.values():
                for day_slots in tt.get("schedule", {}).values():
                    for slot in day_slots:
                        t = slot.get("teacher", "")
                        if t.lower() == teacher_name.lower():
                            subjects_found.add(slot.get("subject", ""))
            if subjects_found:
                return f"{teacher_name} teaches: {', '.join(sorted(subjects_found))}."
            return f"No records found for teacher '{teacher_name}'."

        # Fallback
        subjects: List[str] = []
        for doc in docs:
            m = re.match(r"Teacher\s+.+?\s+teaches\s+(.+?)\s+to\s+Class", doc, re.IGNORECASE)
            if m:
                subj = m.group(1).strip()
                if subj not in subjects:
                    subjects.append(subj)
        t_label = teacher_name or "That teacher"
        if subjects:
            return f"{t_label} teaches: {', '.join(subjects)}."
        return "I could not find that information."

    def _answer_when(self, q: str, docs: List[str]) -> str:
        """Answer 'when does class X have subject Y?' with a clean direct lookup."""
        # Extract class/section
        cn, sec = self._parse_class_section(q)

        # Extract subject from question
        subj_match = re.search(
            r'\b(telugu|hindi|english|maths|math|mathematics|social|physics|'
            r'biology|environmental science|computer|sports|science|chemistry|'
            r'history|geography|moral|art|music|pe)\b', q, re.IGNORECASE
        )

        if self.all_timetables and cn is not None and subj_match:
            subject = subj_match.group(1).title()
            if subject.lower() in ("math", "mathematics"):
                subject = "Maths"
            secs = [sec] if sec else ["A", "B"]
            lines = []
            for s in secs:
                tt = self.all_timetables.get(f"Class_{cn}{s}")
                if not tt:
                    continue
                day_slots: dict = {}
                for day, slots in tt.get("schedule", {}).items():
                    for slot in slots:
                        if slot.get("subject", "").lower() == subject.lower():
                            period = slot.get("period")
                            time   = slot.get("time", "")
                            day_slots.setdefault(day, []).append(f"Period {period} ({time})")
                if day_slots:
                    parts = ", ".join(
                        f"{day}: {', '.join(ps)}" for day, ps in day_slots.items()
                    )
                    lines.append(f"Class {cn}{s} has {subject} on — {parts}.")
            if lines:
                return "\n".join(lines)
            return f"Could not find {subject} periods for Class {cn}{sec or ''}."

        # Fallback: filter retrieved docs
        if not docs:
            return "I could not find timing information for that query."
        slot_docs = [d for d in docs if "Period" in d and "teaches" in d][:3]
        result = slot_docs if slot_docs else docs[:3]
        lines = list(dict.fromkeys(result))
        return "Timing information:\n" + "\n".join(f"  • {line}" for line in lines)

    def _answer_schedule(self, q: str, docs: List[str]) -> str:
        """Answer whole-class and day-specific schedule questions."""
        cn, sec = self._parse_class_section(q)
        day = self._extract_day(q)

        if self.all_timetables and cn is not None:
            sections = [sec] if sec else ["A", "B"]
            results = []
            for section in sections:
                tt = self.all_timetables.get(f"Class_{cn}{section}")
                if not tt:
                    continue

                if day:
                    slots = tt.get("schedule", {}).get(day, [])
                    entries = [
                        f"Period {slot.get('period')}: {slot.get('subject', 'Unknown')} "
                        f"({slot.get('teacher', 'Unknown')})"
                        for slot in slots
                    ]
                    detail = ", ".join(entries) if entries else "No classes"
                    results.append(f"Class {cn}{section} on {day}: {detail}.")
                else:
                    results.append(f"Schedule for Class {cn}{section}:")
                    for schedule_day, slots in tt.get("schedule", {}).items():
                        entries = ", ".join(
                            f"P{slot.get('period')}: {slot.get('subject', 'Unknown')}"
                            for slot in slots
                        )
                        results.append(f"{schedule_day}: {entries}.")

            if results:
                return "\n".join(results)
            return f"No timetable found for Class {cn}{sec or ''}."

        if not docs:
            return "No schedule information found for that class."
        day_docs = [d for d in docs if d.startswith("On ")]
        result_docs = day_docs if day_docs else docs
        lines = list(dict.fromkeys(result_docs[:5]))
        return "Schedule information:\n" + "\n".join(f"  • {line}" for line in lines)

    def _answer_sports(self, q: str, docs: List[str]) -> str:
        """Answer sports-related questions — directly from timetable data."""
        if not self.all_timetables:
            return "No sports information found."

        cn, sec = self._parse_class_section(q)

        # If a specific class is given, look it up directly
        if cn is not None:
            secs = [sec] if sec else ["A", "B"]
            results = []
            for s in secs:
                tt = self.all_timetables.get(f"Class_{cn}{s}")
                if not tt:
                    continue
                sports_day = tt.get("sports_day", "")
                sp = tt.get("sports_periods", [])
                if sports_day and sp:
                    periods_str = " and ".join(f"Period {p}" for p in sp if p)
                    results.append(f"Class {cn} Section {s} has Sports on {sports_day} at {periods_str}.")
            if results:
                return "\n".join(results)
            return f"No sports period found for Class {cn}{sec or ''}."

        # No specific class — search all
        sports_docs = [d for d in docs if "sports" in d.lower() or "Sports" in d]
        if sports_docs:
            return sports_docs[0]
        return "No sports information found in the retrieved context."

    @staticmethod
    def _extract_day(q: str) -> Optional[str]:
        days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
        match = re.search(r"\b(" + "|".join(days) + r")\b", q, re.IGNORECASE)
        return match.group(1).capitalize() if match else None

    @staticmethod
    def _extract_subject(q: str) -> Optional[str]:
        match = re.search(
            r"\b(telugu|hindi|english|maths|math|mathematics|social|physics|"
            r"biology|environmental science|computer|sports|science|chemistry|"
            r"history|geography|moral|art|music|pe)\b",
            q,
            re.IGNORECASE,
        )
        if not match:
            return None
        subject = match.group(1).title()
        return "Maths" if subject.lower() in ("math", "mathematics") else subject

    def _answer_has_subject(self, q: str) -> str:
        """Answer a class/subject/day question using an exact timetable lookup."""
        cn, sec = self._parse_class_section(q)
        day = self._extract_day(q)
        subject = self._extract_subject(q)

        if cn is None or sec is None or day is None or subject is None:
            return "Please specify a class section, subject, and day."

        tt = self.all_timetables.get(f"Class_{cn}{sec}") if self.all_timetables else None
        if not tt:
            return f"No timetable found for Class {cn}{sec}."

        matching_slots = [
            slot for slot in tt.get("schedule", {}).get(day, [])
            if slot.get("subject", "").lower() == subject.lower()
        ]
        if matching_slots:
            periods = ", ".join(str(slot.get("period")) for slot in matching_slots)
            return (
                f"Yes, Class {cn}{sec} has {subject} on {day}"
                f" during Period{'s' if len(matching_slots) != 1 else ''} {periods}."
            )
        return f"No, Class {cn}{sec} does not have {subject} on {day}."

    # ------------------------------------------------------------------
    # Shared class/section parser (handles all phrasings)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_class_section(q: str):
        """
        Robustly extract (class_num, section) from any question phrasing.

        Handles:
          "class 3B", "class 3 section b", "class3b", "3b",
          "class b" (section-only follow-up → (None, 'B')),
          "class 3"  (no section → (3, None))
        Returns (int|None, str|None).
        """
        q = q.lower()
        # Full match: class <num> [section] <letter>
        m = re.search(r'class\s*(\d+)\s*(?:section\s*)?([ab])\b', q)
        if m:
            return int(m.group(1)), m.group(2).upper()
        # class <num> only
        m = re.search(r'class\s*(\d+)', q)
        if m:
            # Check for a trailing section letter anywhere in q
            sec_m = re.search(r'\bsection\s*([ab])\b|\b([ab])\b(?!.*\d)', q)
            sec = None
            if sec_m:
                sec = (sec_m.group(1) or sec_m.group(2) or "").upper() or None
            return int(m.group(1)), sec
        # Bare "section b" or "class b" follow-up with no number
        m = re.search(r'\b([ab])\b', q)
        if m:
            return None, m.group(1).upper()
        return None, None

    def _answer_how_many(self, q: str, docs: List[str]) -> str:
        """Count periods for the requested class and day."""
        cn, sec = self._parse_class_section(q)
        day = self._extract_day(q)
        if cn is None or sec is None or day is None:
            return "Please specify a class section and day."

        tt = self.all_timetables.get(f"Class_{cn}{sec}") if self.all_timetables else None
        if not tt:
            return f"No timetable found for Class {cn}{sec}."

        periods = tt.get("schedule", {}).get(day, [])
        return f"Class {cn}{sec} has {len(periods)} periods on {day}."

    def _fallback_answer(self, question: str, docs: List[str]) -> str:
        """Try Groq first, then return a concise no-answer message."""
        # Try Groq even here (some questions don't match any pattern)
        groq_answer = self._ask_groq(question, docs)
        if groq_answer:
            return groq_answer

        if not docs:
            return (
                f"I could not find relevant timetable information for: '{question}'. "
                "Try asking about a specific class, teacher, subject, or day."
            )
        return (
            f"I could not find a direct answer for '{question}'. "
            "Please specify a class section, subject, teacher, or day."
        )
