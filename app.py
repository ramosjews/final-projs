
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash


# =========================================================
# CONFIGURATION
# =========================================================

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_ROOT, "data.json")

ADMIN_EMAIL = os.environ.get(
    "ADMIN_EMAIL", "admin@gatehub.local"
).strip().lower()

ADMIN_PASSWORD = os.environ.get("admin", "gate2026")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "false").lower() == "true"
)

_lock = threading.Lock()
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# =========================================================
# JSON DATABASE
# =========================================================

def _save_data(data):
    """Save JSON data safely using a temporary file."""

    temp_file = DATA_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    os.replace(temp_file, DATA_FILE)


def _load_data():
    """Load JSON data and migrate older records safely."""

    if not os.path.exists(DATA_FILE):
        return {
            "events": [],
            "registrations": [],
            "categories": [],
            "users": [],
        }

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    changed = False

    # Ensure required database collections exist.
    for key in ("events", "categories", "users"):
        if key not in data or not isinstance(data[key], list):
            data[key] = []
            changed = True

    # Migrate old tickets into registrations.
    if "registrations" not in data:
        data["registrations"] = data.pop("tickets", [])
        changed = True
    elif "tickets" in data:
        data.pop("tickets", None)
        changed = True

    if not isinstance(data["registrations"], list):
        data["registrations"] = []
        changed = True

    # Repair and migrate existing participant accounts.
    # IMPORTANT: Save newly generated IDs so they remain stable.
    for user in data["users"]:
        if not isinstance(user, dict):
            continue

        if not user.get("id"):
            user["id"] = "usr" + uuid.uuid4().hex[:12]
            changed = True

        if "student_id" not in user and "studentId" in user:
            user["student_id"] = user.pop("studentId")
            changed = True

        if "student_id" not in user:
            user["student_id"] = ""
            changed = True

        if "name" not in user:
            user["name"] = ""
            changed = True

        if "email" not in user:
            user["email"] = ""
            changed = True

        if "createdAt" not in user:
            user["createdAt"] = None
            changed = True

    # Migrate old registration fields.
    for registration in data["registrations"]:
        if not isinstance(registration, dict):
            continue

        for old_field in ("code", "total", "purchasedAt"):
            if old_field in registration:
                registration.pop(old_field, None)
                changed = True

        if not registration.get("id"):
            registration["id"] = "reg" + uuid.uuid4().hex[:10]
            changed = True

        if "quantity" not in registration:
            registration["quantity"] = 1
            changed = True

        if "attendanceStatus" not in registration:
            registration["attendanceStatus"] = "registered"
            changed = True

    # Remove old ticket pricing and ensure event capacities.
    for event in data["events"]:
        if not isinstance(event, dict):
            continue

        if "price" in event:
            event.pop("price", None)
            changed = True

        if "capacity" not in event:
            event["capacity"] = 1
            changed = True

        category = event.get("category", "Meetup")

        if not event.get("category"):
            event["category"] = category
            changed = True

        if category not in data["categories"]:
            data["categories"].append(category)
            changed = True

    # Recalculate registration counts from saved registrations.
    for event in data["events"]:
        if not isinstance(event, dict):
            continue

        registered = sum(
            max(0, int(registration.get("quantity", 1) or 1))
            for registration in data["registrations"]
            if isinstance(registration, dict)
            and registration.get("eventId") == event.get("id")
        )

        if event.get("registered") != registered:
            event["registered"] = registered
            changed = True

    # Persist repairs so generated IDs do not change on every request.
    if changed:
        _save_data(data)

    return data


def _get_json():
    return request.get_json(silent=True) or {}


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


def _safe_user(user):
    """Return public account fields only. Never expose password hashes."""

    return {
        "id": user.get("id", ""),
        "student_id": user.get(
            "student_id", user.get("studentId", "")
        ),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": "participant",
        "createdAt": user.get("createdAt"),
    }


def _find_event(data, event_id):
    return next(
        (
            event for event in data["events"]
            if isinstance(event, dict)
            and str(event.get("id")) == str(event_id)
        ),
        None,
    )


def _event_reserved_count(data, event_id):
    total = 0

    for registration in data["registrations"]:
        if not isinstance(registration, dict):
            continue

        if str(registration.get("eventId")) == str(event_id):
            try:
                total += max(
                    0, int(registration.get("quantity", 1) or 1)
                )
            except (ValueError, TypeError):
                continue

    return total


# =========================================================
# PAGES
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin")
def admin_page():
    if _current_role() != "admin":
        return render_template("index.html")

    return render_template("admin.html")


# =========================================================
# AUTH: PARTICIPANT SIGN UP
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

    with _lock:
        data = _load_data()

        existing_email = any(
            isinstance(user, dict)
            and str(user.get("email", "")).strip().lower() == email
            for user in data["users"]
        )

        if existing_email:
            return jsonify({
                "error": "An account with this email already exists."
            }), 409

        existing_student_id = any(
            isinstance(user, dict)
            and str(
                user.get("student_id", user.get("studentId", ""))
            ).strip().casefold() == student_id.casefold()
            for user in data["users"]
        )

        if existing_student_id:
            return jsonify({
                "error": "This Student ID is already registered."
            }), 409

        user = {
            "id": "usr" + uuid.uuid4().hex[:12],
            "student_id": student_id,
            "name": name,
            "email": email,
            "passwordHash": generate_password_hash(password),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }

        data["users"].append(user)
        _save_data(data)

    session.clear()
    session["user_id"] = user["id"]
    session["role"] = "participant"

    return jsonify({
        "message": "Account created successfully.",
        "user": _safe_user(user),
        "role": "participant",
    }), 201


# =========================================================
# AUTH: PARTICIPANT LOGIN
# =========================================================

@app.route("/api/auth/login", methods=["POST"])
def participant_login():
    body = _get_json()

    email = str(body.get("email", "")).strip().lower()
    password = body.get("password", "")

    if not email or not isinstance(password, str) or not password:
        return jsonify({
            "error": "Email and password are required."
        }), 400

    if email == ADMIN_EMAIL:
        return jsonify({
            "error": "Use the administrator login."
        }), 403

    with _lock:
        data = _load_data()

        user = next(
            (
                item for item in data["users"]
                if isinstance(item, dict)
                and str(item.get("email", "")).strip().lower() == email
            ),
            None,
        )

        if not user or not check_password_hash(
            user.get("passwordHash", ""), password
        ):
            return jsonify({
                "error": "Invalid email or password."
            }), 401

        user_response = _safe_user(user)
        user_id = user["id"]

    session.clear()
    session["user_id"] = user_id
    session["role"] = "participant"

    return jsonify({
        "message": "Login successful.",
        "role": "participant",
        "user": user_response,
    })


# =========================================================
# AUTH: ADMIN LOGIN
# =========================================================

@app.route("/api/auth/admin-login", methods=["POST"])
def admin_login():
    body = _get_json()

    email = str(body.get("email", "")).strip().lower()
    password = body.get("password", "")

    if (
        email != ADMIN_EMAIL
        or not isinstance(password, str)
        or password != ADMIN_PASSWORD
    ):
        return jsonify({
            "error": "Invalid administrator credentials."
        }), 401

    session.clear()
    session["role"] = "admin"
    session["admin_email"] = ADMIN_EMAIL

    return jsonify({
        "message": "Administrator login successful.",
        "role": "admin",
        "user": {
            "email": ADMIN_EMAIL,
            "role": "admin",
        },
    })


@app.route("/api/admin/verify", methods=["POST"])
def verify_admin():
    """Backward compatibility with the password-only admin form."""

    body = _get_json()
    password = body.get("password", "")

    if not isinstance(password, str) or password != ADMIN_PASSWORD:
        return jsonify({
            "error": "Incorrect administrator password."
        }), 401

    session.clear()
    session["role"] = "admin"
    session["admin_email"] = ADMIN_EMAIL

    return jsonify({"ok": True, "role": "admin"})


@app.route("/api/auth/me", methods=["GET"])
def get_current_user():
    role = _current_role()

    if role == "admin":
        return jsonify({
            "authenticated": True,
            "role": "admin",
            "user": {
                "email": session.get("admin_email", ADMIN_EMAIL),
                "role": "admin",
            },
        })

    if role == "participant":
        user_id = session.get("user_id")

        with _lock:
            data = _load_data()
            user = next(
                (
                    item for item in data["users"]
                    if isinstance(item, dict)
                    and str(item.get("id")) == str(user_id)
                ),
                None,
            )

        if user:
            return jsonify({
                "authenticated": True,
                "role": "participant",
                "user": _safe_user(user),
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
    with _lock:
        data = _load_data()
        return jsonify(data["events"])


@app.route("/api/categories", methods=["GET"])
def get_categories():
    with _lock:
        data = _load_data()
        return jsonify(data["categories"])


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

    if len(title) > 150 or len(location) > 200:
        return jsonify({
            "error": "Title or location is too long."
        }), 400

    with _lock:
        data = _load_data()

        event = {
            "id": "ev" + uuid.uuid4().hex[:10],
            "title": title,
            "category": category,
            "date": date,
            "time": time,
            "location": location,
            "capacity": capacity,
            "registered": 0,
            "description": description,
        }

        data["events"].append(event)

        if category not in data["categories"]:
            data["categories"].append(category)

        _save_data(data)

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

    if len(title) > 150 or len(location) > 200:
        return jsonify({
            "error": "Title or location is too long."
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

    with _lock:
        data = _load_data()
        event = _find_event(data, event_id)

        if not event:
            return jsonify({"error": "Event not found."}), 404

        reserved = _event_reserved_count(data, event_id)

        if capacity < reserved:
            return jsonify({
                "error": (
                    f"Capacity cannot be lower than the "
                    f"{reserved} already reserved spots."
                )
            }), 400

        event.update({
            "title": title,
            "category": category,
            "date": date,
            "time": time,
            "location": location,
            "capacity": capacity,
            "registered": reserved,
            "description": description,
        })

        if category not in data["categories"]:
            data["categories"].append(category)

        _save_data(data)

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

    with _lock:
        data = _load_data()
        event = _find_event(data, event_id)

        if not event:
            return jsonify({"error": "Event not found."}), 404

        # Preserve registration and attendance history.
        has_registrations = any(
            isinstance(registration, dict)
            and str(registration.get("eventId")) == str(event_id)
            for registration in data["registrations"]
        )

        if has_registrations:
            return jsonify({
                "error": (
                    "This event has registration records and cannot "
                    "be deleted. Preserve those records or resolve "
                    "the registrations first."
                )
            }), 409

        data["events"].remove(event)

        category = event.get("category")

        if category and not any(
            item.get("category") == category
            for item in data["events"]
            if isinstance(item, dict)
        ):
            data["categories"] = [
                item for item in data["categories"]
                if item != category
            ]

        _save_data(data)

    return jsonify({"message": "Event deleted successfully."})


# =========================================================
# ADMIN: LIST PARTICIPANT ACCOUNTS
# =========================================================

@app.route("/api/admin/users", methods=["GET"])
def get_participant_accounts():
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with _lock:
        data = _load_data()
        users = [
            _safe_user(user)
            for user in data["users"]
            if isinstance(user, dict)
        ]

    return jsonify(users)


# =========================================================
# ADMIN: VIEW ALL REGISTRATIONS
# =========================================================

@app.route("/api/admin/registrations", methods=["GET"])
def get_all_registrations():
    permission_error = _require_admin()
    if permission_error:
        return permission_error

    with _lock:
        data = _load_data()
        return jsonify(data["registrations"])


# =========================================================
# PARTICIPANT: RESERVE A FREE EVENT
# =========================================================

@app.route("/api/events/<event_id>/register", methods=["POST"])
def reserve_spot(event_id):
    permission_error = _require_login()
    if permission_error:
        return permission_error

    body = _get_json()
    user_id = session["user_id"]

    try:
        quantity = int(body.get("quantity", 1))
    except (ValueError, TypeError):
        return jsonify({
            "error": "Number of spots must be a whole number."
        }), 400

    if quantity < 1:
        return jsonify({
            "error": "Reserve at least one spot."
        }), 400

    with _lock:
        data = _load_data()

        user = next(
            (
                item for item in data["users"]
                if isinstance(item, dict)
                and str(item.get("id")) == str(user_id)
            ),
            None,
        )

        if not user:
            session.clear()
            return jsonify({
                "error": "Account not found. Please log in again."
            }), 401

        event = _find_event(data, event_id)

        if not event:
            return jsonify({"error": "Event not found."}), 404

        already_reserved = any(
            isinstance(registration, dict)
            and str(registration.get("eventId")) == str(event_id)
            and str(registration.get("owner")) == str(user_id)
            for registration in data["registrations"]
        )

        if already_reserved:
            return jsonify({
                "error": "You have already reserved this event."
            }), 409

        reserved = _event_reserved_count(data, event_id)

        try:
            capacity = int(event.get("capacity", 1))
        except (ValueError, TypeError):
            capacity = 1

        available = capacity - reserved

        if quantity > available:
            return jsonify({
                "error": f"Only {max(available, 0)} spots are available."
            }), 400

        registration = {
            "id": "reg" + uuid.uuid4().hex[:10],
            "eventId": event_id,
            "eventTitle": event["title"],
            "student_id": user.get(
                "student_id", user.get("studentId", "")
            ),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "quantity": quantity,
            "owner": user_id,
            "attendanceStatus": "registered",
            "registeredAt": datetime.now(timezone.utc).isoformat(),
        }

        data["registrations"].append(registration)
        event["registered"] = reserved + quantity
        _save_data(data)

    return jsonify({
        "message": "Your spot has been reserved!",
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

    user_id = session["user_id"]

    with _lock:
        data = _load_data()
        registrations = [
            registration
            for registration in data["registrations"]
            if isinstance(registration, dict)
            and str(registration.get("owner")) == str(user_id)
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

    with _lock:
        data = _load_data()

        registration = next(
            (
                item for item in data["registrations"]
                if isinstance(item, dict)
                and str(item.get("id")) == str(registration_id)
                and str(item.get("owner")) == str(user_id)
            ),
            None,
        )

        if not registration:
            return jsonify({
                "error": "Registration not found."
            }), 404

        if registration.get("attendanceStatus") == "attended":
            return jsonify({
                "error": "An attended registration cannot be cancelled."
            }), 400

        data["registrations"].remove(registration)

        event = _find_event(data, registration.get("eventId"))

        if event:
            event["registered"] = _event_reserved_count(
                data, event["id"]
            )

        _save_data(data)

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

    with _lock:
        data = _load_data()

        registration = next(
            (
                item for item in data["registrations"]
                if isinstance(item, dict)
                and str(item.get("id")) == str(registration_id)
            ),
            None,
        )

        if not registration:
            return jsonify({
                "error": "Registration not found."
            }), 404

        registration["attendanceStatus"] = status
        _save_data(data)

    return jsonify({
        "message": "Attendance updated.",
        "registration": registration,
    })


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":
    with _lock:
        data = _load_data()
        _save_data(data)

    app.run(
        debug=True,
        use_reloader=False,
        host="0.0.0.0",
        port=5000,
    )
