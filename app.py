import os
import re
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import mysql.connector
from mysql.connector import pooling
from flask import (
    Flask, jsonify, request, render_template, session, redirect
)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


# =========================================================
# CONFIGURATION
# =========================================================

ADMIN_EMAIL = os.environ.get(
    "ADMIN_EMAIL", "admin@gatehub.local"
).strip().lower()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "gate2026")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "event_hub"),
    "charset": "utf8mb4",
}

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "false").lower() == "true"
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
STUDENT_ID_RE = re.compile(r"^\d+(?:-\d+)*$")
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ .'’\-]+[^\W\d_]+)*\.?$")

# Allowed year levels for the registration form
YEAR_LEVELS = ["1st Year", "2nd Year", "3rd Year", "4th Year"]


# =========================================================
# MYSQL CONNECTION
#
# A small connection pool is used. transaction() gives one
# cursor, commits when the block ends normally, and rolls
# back if an error happens.
# =========================================================

_pool = None


def _get_pool():
    global _pool

    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="event_hub_pool",
            pool_size=5,
            **DB_CONFIG,
        )

    return _pool


@contextmanager
def transaction():
    conn = _get_pool().get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


# =========================================================
# OOP MODELS
#
# Inheritance:
#   Account (abstract)  -> Participant, Admin
#   Event (abstract)    -> UpcomingEvent, ReopenedEvent, FinishedEvent
#   Registration (abstract) -> AttendingRegistration, DeclinedRegistration
#
# Polymorphism:
#   to_public()   behaves differently for Participant and Admin
#   is_finished() behaves differently for each Event subclass
#   spots_used()  behaves differently for each Registration subclass
#
# The models wrap plain dictionaries built from MySQL rows,
# so every route returns the same JSON as before.
# =========================================================

class BaseModel:
    """Parent of every model. Wraps one dictionary."""

    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return self._data


# ---------------- Accounts ----------------

class Account(BaseModel, ABC):
    """Abstract parent for every kind of logged-in user."""

    role = "account"

    @abstractmethod
    def to_public(self):
        """Return public account fields only (never password hashes)."""


class Participant(Account):
    role = "participant"

    def to_public(self):
        return {
            "id": self.get("id", ""),
            "student_id": self.get("student_id", ""),
            "name": self.get("name", ""),
            "email": self.get("email", ""),
            "role": self.role,
            "createdAt": self.get("createdAt"),
        }


class Admin(Account):
    role = "admin"

    def to_public(self):
        return {
            "email": self.get("email", ADMIN_EMAIL),
            "role": self.role,
        }


# ---------------- Events ----------------

class Event(BaseModel, ABC):
    """Abstract parent for events. Each status decides if it is finished."""

    @abstractmethod
    def is_finished(self):
        """Return True when the event is finished."""

    @staticmethod
    def from_dict(data):
        """Pick the right Event subclass from the saved status."""

        status = data.get("status", "upcoming")

        if status == "finished":
            return FinishedEvent(data)

        if status == "reopened":
            return ReopenedEvent(data)

        return UpcomingEvent(data)


class FinishedEvent(Event):
    """The admin marked the event as done."""

    def is_finished(self):
        return True


class ReopenedEvent(Event):
    """The admin reopened it, so it will not auto-finish again until
    the admin clicks Mark as Done."""

    def is_finished(self):
        return False


class UpcomingEvent(Event):
    """Finishes automatically once its date and time have passed."""

    def is_finished(self):
        try:
            event_time = datetime.strptime(
                f"{self.get('date')} {self.get('time')}",
                "%Y-%m-%d %H:%M",
            )
            return event_time < datetime.now()
        except (ValueError, TypeError):
            return False


# ---------------- Registrations ----------------

class Registration(BaseModel, ABC):
    """Abstract parent for registrations. Each type decides its spots."""

    @abstractmethod
    def spots_used(self):
        """Return how many event spots this registration uses."""

    @staticmethod
    def from_dict(data):
        """Pick the right Registration subclass from the answer."""

        if str(data.get("attending", "yes")).lower() == "no":
            return DeclinedRegistration(data)

        return AttendingRegistration(data)


class AttendingRegistration(Registration):
    def spots_used(self):
        try:
            return max(0, int(self.get("quantity", 1)))
        except (ValueError, TypeError):
            return 1


class DeclinedRegistration(Registration):
    """A 'no' answer uses no spot."""

    def spots_used(self):
        return 0


# =========================================================
# ROW -> JSON HELPERS
#
# MySQL columns are snake_case. The browser code expects the
# same camelCase JSON as the old data.json version, so rows
# are converted here.
# =========================================================

def _iso(value):
    """DATETIME (stored as UTC) -> ISO text the browser can parse."""

    if value is None:
        return None

    return value.replace(tzinfo=timezone.utc).isoformat()


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_time(value):
    """MySQL TIME comes back as timedelta. Return 'HH:MM'."""

    if value is None:
        return ""

    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"

    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")

    return str(value)[:5]


def _parse_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_time(text):
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).time()
        except ValueError:
            continue

    raise ValueError("Invalid time.")


def user_to_dict(row):
    return {
        "id": row["id"],
        "student_id": row["student_id"],
        "name": row["name"],
        "email": row["email"],
        "createdAt": _iso(row["created_at"]),
    }


# Event query that also counts the reserved spots.
EVENT_SELECT = """
    SELECT e.*,
           COALESCE((
               SELECT SUM(r.quantity)
               FROM registrations r
               WHERE r.event_id = e.id AND r.attending = 'yes'
           ), 0) AS registered
    FROM events e
"""


def event_to_dict(row):
    event = {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "date": row["event_date"].isoformat(),
        "time": _fmt_time(row["event_time"]),
        "location": row["location"],
        "capacity": row["capacity"],
        "registered": int(row["registered"]),
        "description": row["description"] or "",
        "status": row["status"],
    }

    if row.get("finished_at"):
        event["finishedAt"] = _iso(row["finished_at"])

    return event


REGISTRATION_SELECT = """
    SELECT r.*, e.title AS event_title
    FROM registrations r
    JOIN events e ON e.id = r.event_id
"""


def registration_to_dict(row):
    return {
        "id": row["id"],
        "eventId": row["event_id"],
        "eventTitle": row["event_title"],
        "student_id": row["student_id"],
        "name": row["name"],
        "email": row["email"],
        "course": row["course"],
        "section": row["section"],
        "year": row["year_level"],
        "attending": row["attending"],
        "quantity": row["quantity"],
        "owner": row["user_id"],
        "attendanceStatus": row["attendance_status"],
        "registeredAt": _iso(row["registered_at"]),
    }


def _get_json():
    return request.get_json(silent=True) or {}


# =========================================================
# QUERY HELPERS
# =========================================================

def _fetch_event(cursor, event_id, lock=False):
    cursor.execute(
        EVENT_SELECT + " WHERE e.id = %s" + (" FOR UPDATE" if lock else ""),
        (event_id,),
    )
    row = cursor.fetchone()
    return event_to_dict(row) if row else None


def _fetch_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cursor.fetchone()


def _event_reserved_count(cursor, event_id):
    """Spots used by all registrations of one event."""

    cursor.execute(
        "SELECT attending, quantity FROM registrations WHERE event_id = %s",
        (event_id,),
    )

    return sum(
        Registration.from_dict(row).spots_used()
        for row in cursor.fetchall()
    )


def _is_finished(event):
    """Finished if the admin marked it done, or its date and time passed."""

    return Event.from_dict(event).is_finished()


# =========================================================
# AUTHENTICATION AND PERMISSIONS
# =========================================================

def _current_role():
    return session.get("role")


def _require_login():
    if not session.get("user_id"):
        return jsonify({"error": "Please log in first."}), 401

    if _current_role() != "participant":
        return jsonify({
            "error": "Participant account required."
        }), 403

    return None


def _require_admin():
    if _current_role() != "admin":
        return jsonify({
            "error": "Admin access required."
        }), 403

    return None


def _safe_user(user_dict):
    """Return public account fields only. Never expose password hashes."""

    return Participant(user_dict).to_public()


# =========================================================
# PAGES
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin")
def admin_page():
    # Anyone who is not signed in as the admin goes back to sign-in.
    if _current_role() != "admin":
        return redirect("/")

    return render_template("admin.html")


# =========================================================
# AUTH: SIGN UP (participants only)
#
# The new account is NOT logged in automatically. The person
# goes to the Sign In form and logs in themselves.
# =========================================================

@app.route("/api/auth/register", methods=["POST"])
def register_account():
    body = _get_json()

    student_id = str(body.get("student_id", "")).strip()
    name = str(body.get("name", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = body.get("password", "")

    if not all([student_id, name, email, password]):
        return jsonify({
            "error": (
                "Student ID, name, email, and password are required."
            )
        }), 400

    if len(student_id) > 50:
        return jsonify({"error": "Student ID is too long."}), 400

    if len(name) > 100:
        return jsonify({"error": "Name is too long."}), 400

    if not STUDENT_ID_RE.fullmatch(student_id):
        return jsonify({
            "error": "Student ID can only contain numbers (and hyphens)."
        }), 400

    if not NAME_RE.fullmatch(name):
        return jsonify({
            "error": (
                "Name can only contain letters, spaces, periods, "
                "apostrophes and hyphens."
            )
        }), 400

    if not EMAIL_RE.fullmatch(email) or len(email) > 254:
        return jsonify({"error": "Enter a valid email address."}), 400

    if not isinstance(password, str) or len(password) < 8:
        return jsonify({
            "error": "Password must contain at least 8 characters."
        }), 400

    if len(password) > 128:
        return jsonify({"error": "Password is too long."}), 400

    if email == ADMIN_EMAIL:
        return jsonify({
            "error": "This email is reserved for the administrator."
        }), 409

    user_id = "usr" + uuid.uuid4().hex[:12]

    try:
        with transaction() as cursor:
            cursor.execute(
                "SELECT 1 FROM users WHERE email = %s", (email,)
            )
            if cursor.fetchone():
                return jsonify({
                    "error": "An account with this email already exists."
                }), 409

            cursor.execute(
                "SELECT 1 FROM users WHERE student_id = %s",
                (student_id,),
            )
            if cursor.fetchone():
                return jsonify({
                    "error": "This Student ID is already registered."
                }), 409

            cursor.execute(
                """
                INSERT INTO users
                    (id, student_id, name, email, password_hash, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id, student_id, name, email,
                    generate_password_hash(password), _utc_now(),
                ),
            )

            user = _fetch_user_by_id(cursor, user_id)

    except mysql.connector.IntegrityError:
        # Two people used the same email / student ID at the same time.
        return jsonify({
            "error": "This email or Student ID is already registered."
        }), 409

    session.clear()

    return jsonify({
        "message": "Account created successfully. Please sign in.",
        "user": _safe_user(user_to_dict(user)),
        "role": "participant",
    }), 201


# =========================================================
# AUTH: ONE SHARED LOGIN FOR ADMIN AND PARTICIPANTS
# =========================================================

@app.route("/api/auth/login", methods=["POST"])
def login():
    body = _get_json()

    email = str(body.get("email", "")).strip().lower()
    password = body.get("password", "")

    if not email or not isinstance(password, str) or not password:
        return jsonify({
            "error": "Email and password are required."
        }), 400

    # ---- Administrator ----
    if email == ADMIN_EMAIL:
        if password != ADMIN_PASSWORD:
            return jsonify({
                "error": "Invalid email or password."
            }), 401

        session.clear()
        session["role"] = "admin"
        session["admin_email"] = ADMIN_EMAIL

        return jsonify({
            "message": "Login successful.",
            "role": "admin",
            "redirect": "/admin",
            "user": Admin({"email": ADMIN_EMAIL}).to_public(),
        })

    # ---- Participant ----
    with transaction() as cursor:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({
            "error": "Invalid email or password."
        }), 401

    session.clear()
    session["user_id"] = user["id"]
    session["role"] = "participant"

    return jsonify({
        "message": "Login successful.",
        "role": "participant",
        "user": _safe_user(user_to_dict(user)),
    })


@app.route("/api/auth/me", methods=["GET"])
def get_current_user():
    role = _current_role()

    if role == "admin":
        admin = Admin({"email": session.get("admin_email", ADMIN_EMAIL)})

        return jsonify({
            "authenticated": True,
            "role": "admin",
            "redirect": "/admin",
            "user": admin.to_public(),
        })

    if role == "participant":
        with transaction() as cursor:
            user = _fetch_user_by_id(cursor, session.get("user_id"))

        if user:
            return jsonify({
                "authenticated": True,
                "role": "participant",
                "user": _safe_user(user_to_dict(user)),
            })

        session.clear()

    return jsonify({
        "authenticated": False,
        "role": None,
        "user": None,
    })


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out successfully."})


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"ok": True})


# =========================================================
# PUBLIC EVENTS AND CATEGORIES
# =========================================================

@app.route("/api/events", methods=["GET"])
def get_events():
    with transaction() as cursor:
        cursor.execute(EVENT_SELECT + " ORDER BY e.created_at, e.event_date")
        rows = cursor.fetchall()

    # "isFinished" is computed on the fly and never saved.
    events = []

    for row in rows:
        event = event_to_dict(row)
        event["isFinished"] = _is_finished(event)
        events.append(event)

    return jsonify(events)


@app.route("/api/categories", methods=["GET"])
def get_categories():
    # Categories are simply the ones used by existing events.
    with transaction() as cursor:
        cursor.execute(
            "SELECT DISTINCT category FROM events ORDER BY category"
        )
        names = [row["category"] for row in cursor.fetchall()]

    return jsonify(names)


# =========================================================
# ADMIN: CREATE EVENTS
# =========================================================

@app.route("/api/events", methods=["POST"])
def create_event():
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    body = _get_json()

    title = str(body.get("title", "")).strip()
    date = str(body.get("date", "")).strip()
    time = str(body.get("time", "")).strip()
    location = str(body.get("location", "")).strip()
    category = str(body.get("category", "Meetup")).strip() or "Meetup"
    description = str(
        body.get("description", "Details coming soon.")
    ).strip()

    if not all([title, date, time, location]):
        return jsonify({
            "error": "Title, date, time, and location are required."
        }), 400

    try:
        capacity = int(body.get("capacity", 1))
    except (ValueError, TypeError):
        return jsonify({
            "error": "Capacity must be a whole number."
        }), 400

    if capacity < 1:
        return jsonify({
            "error": "Capacity must be at least 1."
        }), 400

    if len(title) > 150 or len(location) > 200 or len(category) > 50:
        return jsonify({
            "error": "Title, category, or location is too long."
        }), 400

    try:
        event_date = _parse_date(date)
        event_time = _parse_time(time)
    except ValueError:
        return jsonify({
            "error": "Enter a valid date and time."
        }), 400

    event_id = "ev" + uuid.uuid4().hex[:10]

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO events
                (id, title, category, event_date, event_time, location,
                 capacity, description, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'upcoming', %s)
            """,
            (
                event_id, title, category, event_date, event_time,
                location, capacity, description, _utc_now(),
            ),
        )

        event = _fetch_event(cursor, event_id)

    return jsonify(event), 201


# =========================================================
# ADMIN: UPDATE EVENT
# =========================================================

@app.route("/api/events/<event_id>", methods=["PUT"])
def update_event(event_id):
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    body = _get_json()

    title = str(body.get("title", "")).strip()
    category = str(body.get("category", "")).strip()
    date = str(body.get("date", "")).strip()
    time = str(body.get("time", "")).strip()
    location = str(body.get("location", "")).strip()
    description = str(body.get("description", "")).strip()

    if not all([title, category, date, time, location]):
        return jsonify({
            "error": (
                "Title, category, date, time, and location are required."
            )
        }), 400

    if len(title) > 150 or len(location) > 200 or len(category) > 50:
        return jsonify({
            "error": "Title, category, or location is too long."
        }), 400

    try:
        capacity = int(body.get("capacity"))
    except (ValueError, TypeError):
        return jsonify({
            "error": "Capacity must be a whole number."
        }), 400

    if capacity < 1:
        return jsonify({
            "error": "Capacity must be at least 1."
        }), 400

    try:
        event_date = _parse_date(date)
        event_time = _parse_time(time)
    except ValueError:
        return jsonify({
            "error": "Enter a valid date and time."
        }), 400

    with transaction() as cursor:
        # FOR UPDATE locks this event row while it is being changed.
        event = _fetch_event(cursor, event_id, lock=True)

        if not event:
            return jsonify({"error": "Event not found."}), 404

        reserved = _event_reserved_count(cursor, event_id)

        if capacity < reserved:
            return jsonify({
                "error": (
                    f"Capacity cannot be lower than the "
                    f"{reserved} already reserved spots."
                )
            }), 400

        cursor.execute(
            """
            UPDATE events
            SET title = %s, category = %s, event_date = %s,
                event_time = %s, location = %s, capacity = %s,
                description = %s
            WHERE id = %s
            """,
            (
                title, category, event_date, event_time, location,
                capacity, description, event_id,
            ),
        )

        event = _fetch_event(cursor, event_id)

    return jsonify({
        "message": "Event updated successfully.",
        "event": event,
    })


# =========================================================
# ADMIN: DELETE EVENT
# =========================================================

@app.route("/api/events/<event_id>", methods=["DELETE"])
def delete_event(event_id):
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with transaction() as cursor:
        event = _fetch_event(cursor, event_id, lock=True)

        if not event:
            return jsonify({"error": "Event not found."}), 404

        # Preserve registration and attendance history.
        cursor.execute(
            "SELECT COUNT(*) AS total FROM registrations WHERE event_id = %s",
            (event_id,),
        )

        if cursor.fetchone()["total"] > 0:
            return jsonify({
                "error": (
                    "This event has registration records and cannot "
                    "be deleted. Preserve those records or resolve "
                    "the registrations first."
                )
            }), 409

        cursor.execute("DELETE FROM events WHERE id = %s", (event_id,))

    return jsonify({"message": "Event deleted successfully."})


# =========================================================
# ADMIN: MARK EVENT AS FINISHED / REOPEN EVENT
# =========================================================

@app.route("/api/events/<event_id>/finish", methods=["POST"])
def finish_event(event_id):
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET status = 'finished', finished_at = %s
            WHERE id = %s
            """,
            (_utc_now(), event_id),
        )

        if cursor.rowcount == 0 and not _fetch_event(cursor, event_id):
            return jsonify({"error": "Event not found."}), 404

    return jsonify({"message": "Event marked as finished."})


@app.route("/api/events/<event_id>/reopen", methods=["POST"])
def reopen_event(event_id):
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET status = 'reopened', finished_at = NULL
            WHERE id = %s
            """,
            (event_id,),
        )

        if cursor.rowcount == 0 and not _fetch_event(cursor, event_id):
            return jsonify({"error": "Event not found."}), 404

    return jsonify({"message": "Event reopened."})


# =========================================================
# ADMIN: LIST PARTICIPANT ACCOUNTS
# =========================================================

@app.route("/api/admin/users", methods=["GET"])
def get_participant_accounts():
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with transaction() as cursor:
        cursor.execute("SELECT * FROM users ORDER BY created_at")
        users = [_safe_user(user_to_dict(row)) for row in cursor.fetchall()]

    return jsonify(users)


# =========================================================
# ADMIN: VIEW ALL REGISTRATIONS
# =========================================================

@app.route("/api/admin/registrations", methods=["GET"])
def get_all_registrations():
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with transaction() as cursor:
        cursor.execute(REGISTRATION_SELECT + " ORDER BY r.registered_at")
        registrations = [
            registration_to_dict(row) for row in cursor.fetchall()
        ]

    return jsonify(registrations)


# =========================================================
# PARTICIPANT: REGISTER FOR A FREE EVENT
# =========================================================

@app.route("/api/events/<event_id>/register", methods=["POST"])
def reserve_spot(event_id):
    permission_error = _require_login()
    if permission_error:
        return permission_error

    body = _get_json()
    user_id = session["user_id"]

    name = str(body.get("name", "")).strip()
    course = str(body.get("course", "")).strip()
    section = str(body.get("section", "")).strip()
    year = str(body.get("year", "")).strip()
    attending = str(body.get("attending", "")).strip().lower()

    if not all([name, course, section, year, attending]):
        return jsonify({
            "error": (
                "Name, course, section, year level, and your "
                "attendance answer are required."
            )
        }), 400

    if len(name) > 100 or len(course) > 100 or len(section) > 30:
        return jsonify({
            "error": "Name, course, or section is too long."
        }), 400

    if not NAME_RE.fullmatch(name):
        return jsonify({
            "error": (
                "Name can only contain letters, spaces, periods, "
                "apostrophes and hyphens."
            )
        }), 400

    if year not in YEAR_LEVELS:
        return jsonify({"error": "Choose a valid year level."}), 400

    if attending not in ("yes", "no"):
        return jsonify({
            "error": "Please answer Yes or No."
        }), 400

    registration_id = "reg" + uuid.uuid4().hex[:10]

    try:
        with transaction() as cursor:
            user = _fetch_user_by_id(cursor, user_id)

            if not user:
                session.clear()
                return jsonify({
                    "error": "Account not found. Please log in again."
                }), 401

            # Lock the event row so two people cannot take the last spot.
            event = _fetch_event(cursor, event_id, lock=True)

            if not event:
                return jsonify({"error": "Event not found."}), 404

            # Block registration for finished events.
            if _is_finished(event):
                return jsonify({
                    "error": "This event has already finished."
                }), 400

            cursor.execute(
                """
                SELECT 1 FROM registrations
                WHERE event_id = %s AND user_id = %s
                """,
                (event_id, user_id),
            )

            if cursor.fetchone():
                return jsonify({
                    "error": "You have already registered for this event."
                }), 409

            # Only a "yes" answer takes a spot.
            if attending == "yes":
                reserved = _event_reserved_count(cursor, event_id)

                if int(event["capacity"]) - reserved < 1:
                    return jsonify({
                        "error": "No spots are available for this event."
                    }), 400

            cursor.execute(
                """
                INSERT INTO registrations
                    (id, event_id, user_id, student_id, name, email,
                     course, section, year_level, attending, quantity,
                     attendance_status, registered_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1,
                        'registered', %s)
                """,
                (
                    registration_id, event_id, user_id,
                    user["student_id"], name, user["email"],
                    course, section, year, attending, _utc_now(),
                ),
            )

            cursor.execute(
                REGISTRATION_SELECT + " WHERE r.id = %s",
                (registration_id,),
            )
            registration = registration_to_dict(cursor.fetchone())

    except mysql.connector.IntegrityError:
        return jsonify({
            "error": "You have already registered for this event."
        }), 409

    return jsonify({
        "message": (
            "You are registered for this event!"
            if attending == "yes"
            else "Your response has been recorded."
        ),
        "registration": registration,
    }), 201


# =========================================================
# PARTICIPANT: VIEW OWN REGISTRATIONS
# =========================================================

@app.route("/api/my-registrations", methods=["GET"])
def get_my_registrations():
    permission_error = _require_login()
    if permission_error:
        return permission_error

    with transaction() as cursor:
        cursor.execute(
            REGISTRATION_SELECT + " WHERE r.user_id = %s "
            "ORDER BY r.registered_at",
            (session["user_id"],),
        )
        registrations = [
            registration_to_dict(row) for row in cursor.fetchall()
        ]

    return jsonify(registrations)


# =========================================================
# PARTICIPANT: CANCEL OWN REGISTRATION
# =========================================================

@app.route(
    "/api/registrations/<registration_id>/cancel",
    methods=["POST"],
)
def cancel_registration(registration_id):
    permission_error = _require_login()
    if permission_error:
        return permission_error

    user_id = session["user_id"]

    with transaction() as cursor:
        cursor.execute(
            REGISTRATION_SELECT + " WHERE r.id = %s AND r.user_id = %s",
            (registration_id, user_id),
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({
                "error": "Registration not found."
            }), 404

        if row["attendance_status"] == "attended":
            return jsonify({
                "error": "An attended registration cannot be cancelled."
            }), 400

        event = _fetch_event(cursor, row["event_id"])

        # Finished events keep their records unchanged.
        if event and _is_finished(event):
            return jsonify({
                "error": "This event has finished, so it cannot be cancelled."
            }), 400

        cursor.execute(
            "DELETE FROM registrations WHERE id = %s", (registration_id,)
        )

    return jsonify({"message": "Reservation cancelled."})


# =========================================================
# ADMIN: UPDATE ATTENDANCE
# =========================================================

@app.route(
    "/api/admin/registrations/<registration_id>/attendance",
    methods=["POST"],
)
def update_attendance(registration_id):
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    body = _get_json()
    status = body.get("status")

    if status not in ["registered", "attended", "absent"]:
        return jsonify({
            "error": "Invalid attendance status."
        }), 400

    with transaction() as cursor:
        cursor.execute(
            "UPDATE registrations SET attendance_status = %s WHERE id = %s",
            (status, registration_id),
        )

        cursor.execute(
            REGISTRATION_SELECT + " WHERE r.id = %s", (registration_id,)
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({
                "error": "Registration not found."
            }), 404

        registration = registration_to_dict(row)

    return jsonify({
        "message": "Attendance updated.",
        "registration": registration,
    })


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":
    # Fail early with a clear message if MySQL is not reachable.
    try:
        with transaction() as cursor:
            cursor.execute("SELECT 1")
    except mysql.connector.Error as error:
        raise SystemExit(
            f"Cannot connect to MySQL: {error}\n"
            "Check DB_HOST, DB_USER, DB_PASSWORD and DB_NAME in .env."
        )

    app.run(
        debug=True,
        use_reloader=False,
        host="0.0.0.0",
        port=5000,
    )