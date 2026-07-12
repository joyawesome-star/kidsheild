import os
import time
import random
from datetime import datetime, timedelta
import io
import zipfile
import secrets

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    abort,
    session,
    redirect,
    url_for,
)

# QR generation
from qrCodeGenerator import generate_bulk_qr_codes, get_batch_qr_codes_from_db_by_created_at_limit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(env_path: str) -> None:
    """Minimal dotenv-like loader."""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


# Load MG91 + Supabase connection details from envFile.txt
_load_env_file(os.path.join(BASE_DIR, "envFile.txt"))

# Import Supabase helpers only after DATABASE_URL is loaded
from supabaseConnect import insert_and_get_id, get_query_result, execute_query

# NOTE: This app historically used SQLAlchemy + raw SQL.
# Deployment on Render must include a PostgreSQL driver.



def create_app():
    app = Flask(__name__, static_folder=None)

    # ==============================
    # Session-based auth (10 min inactivity)
    # ==============================
    app.secret_key = os.environ.get("SESSION_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,  # set True behind HTTPS
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=600),
    )

    INACTIVITY_SECONDS = 600

    def _now():
        return time.time()

    def _touch_session():
        session["last_activity_ts"] = _now()
        session.modified = True

    def _session_role_and_user_id():
        role = session.get("role")
        user_id = session.get("user_id")
        last_ts = session.get("last_activity_ts")
        if not role or last_ts is None:
            return role, user_id, False
        expired = (_now() - float(last_ts)) > INACTIVITY_SECONDS
        if expired:
            return role, user_id, False
        return role, user_id, True

    def _require_auth(expected_role=None):
        role, user_id, ok = _session_role_and_user_id()
        if not ok:
            session.clear()
            return False
        if expected_role and role != expected_role:
            return False
        _touch_session()
        return True

    @app.before_request
    def _session_inactivity_guard():
        # Protect HTML pages
        protected_html = {
            "/userProfile.html": "user",
            "/admin_dashboard.html": "admin",
        }
        if request.path in protected_html:
            expected = protected_html[request.path]
            if not _require_auth(expected_role=expected):
                return redirect(url_for("userlogin_page" if expected == "user" else "adminlogin_page"))

        # Protect APIs
        protected_api = {
            "/api/user/qr_codes": "user",
            "/api/user/qr_codes/activate": "user",
            "/api/user/students/by_qr_code_id": "user",
            "/api/user/students/upsert_by_qr_code_id": "user",

            "/api/admin/qr_codes": "admin",
            "/api/admin/qr_codes/mark_sold": "admin",
            "/api/admin/qr_codes/attach_sold_to_user": "admin",
            "/api/qrcode_batch_items": "admin",
            "/generate_qrcode": "admin",
        }

        if request.path in protected_api:
            expected = protected_api[request.path]
            if not _require_auth(expected_role=expected):
                return jsonify({"message": "Session expired or unauthorized."}), 401

        return None

    @app.post("/logout")
    def logout():
        session.clear()
        return jsonify({"message": "Logged out"}), 200

    # ==============================
    # Existing app (static pages + endpoints)
    # ==============================

    # --- DB-backed OTP + verification (Supabase) ---
    def _normalize_contact(contact_no: str) -> str:
        """Expected Indian format with +91 prefix: +91XXXXXXXXXX"""
        if contact_no is None:
            return ""
        c = str(contact_no).strip()
        if c.startswith("00"):
            c = "+" + c[2:]
        if not c.startswith("+"):
            if c.isdigit() and len(c) == 10:
                c = "+91" + c
        if not c.startswith("+91"):
            return ""
        digits = c[3:]
        if not digits.isdigit() or len(digits) != 10:
            return ""
        return "+91" + digits

    def _is_contact_verified(contact_no: str) -> bool:
        return True

    # --- Static file serving for HTML pages ---
    def _send_html(filename: str):
        return send_from_directory(BASE_DIR, filename)

    @app.get("/")
    def index():
        for candidate in ["welcome.html", "userLogin.html", "usersignup.html"]:
            try:
                return _send_html(candidate)
            except Exception:
                pass
        abort(404)

    @app.get("/usersignup.html")
    def usersignup_page():
        return _send_html("usersignup.html")

    @app.get("/userLogin.html")
    def userlogin_page():
        return _send_html("userLogin.html")

    @app.get("/welcome.html")
    def welcome_page():
        return _send_html("welcome.html")

    @app.get("/adminLogin.html")
    def adminlogin_page():
        return _send_html("adminLogin.html")

    @app.get("/admin_dashboard.html")
    def admin_dashboard_page():
        return _send_html("admin_dashboard.html")

    # Generic static route for images/assets referred by html via relative paths
    @app.get("/<path:filename>")
    def static_catchall(filename):
        return send_from_directory(BASE_DIR, filename)

    # --- OTP endpoints (disabled) ---
    @app.post("/send_otp")
    def send_otp():
        return jsonify({"message": "OTP system disabled for now."}), 200

    @app.post("/verify_otp")
    def verify_otp():
        return jsonify({"verified": True, "message": "OTP verification bypassed (disabled)."}), 200

    # --- User signup endpoint called after OTP verification ---
    @app.post("/user_signup_action")
    def user_signup_action():
        try:
            payload = request.get_json(force=True) or {}
            name = (payload.get("name") or "").strip()
            email = (payload.get("email") or "").strip()
            password = payload.get("password")
            contact_no = payload.get("contact_no") or payload.get("contactNumber") or payload.get("contact")

            if not name or not email or not password or not contact_no:
                return jsonify({"message": "Missing required fields."}), 400

            normalized_contact = _normalize_contact(contact_no)
            if not normalized_contact:
                return jsonify({"message": "Invalid contact number."}), 400

            if not _is_contact_verified(normalized_contact):
                return jsonify({"message": "OTP verification required before signup."}), 403

            if not isinstance(password, str) or len(password) < 6:
                return jsonify({"message": "Password must be at least 6 characters."}), 400

            existing = get_query_result(f"SELECT id FROM public.users WHERE email = '{email}' LIMIT 1;")
            if existing:
                return jsonify({"message": "user already exists"}), 409

            max_id_res = get_query_result("SELECT COALESCE(MAX(id), 0) AS max_id FROM public.users;")
            max_id = max_id_res[0][0] if max_id_res else 0
            new_id = int(max_id) + 1

            safe_name = name.replace("'", "''")
            safe_email = email.replace("'", "''")
            safe_contact = normalized_contact.replace("'", "''")
            safe_password = str(password).replace("'", "''")

            inserted_id = insert_and_get_id(
                "INSERT INTO public.users (id, name, email, contact_no, password) "
                f"VALUES ({new_id}, '{safe_name}', '{safe_email}', '{safe_contact}', '{safe_password}') "
                "RETURNING id;"
            )

            if inserted_id is None:
                return jsonify({"message": "Failed to create user."}), 500

            # normalize insert_and_get_id output to a scalar id
            created_user_id = None
            try:
                # insert_and_get_id may return int or list/row
                if isinstance(inserted_id, (int, float)):
                    created_user_id = int(inserted_id)
                elif isinstance(inserted_id, (list, tuple)):
                    # common patterns: [[id]] or [id]
                    if inserted_id and isinstance(inserted_id[0], (list, tuple)):
                        created_user_id = inserted_id[0][0]
                    elif inserted_id:
                        created_user_id = inserted_id[0]
                else:
                    created_user_id = inserted_id
            except Exception:
                created_user_id = None

            if created_user_id is None:
                # fallback to new_id (best effort)
                created_user_id = new_id

            return jsonify({"message": "User created successfully", "user_id": int(created_user_id)}), 200
        except Exception as e:
            return jsonify({"message": f"Signup failed: {e}"}), 500

    # --- User login (creates session) ---
    @app.post("/user_login")
    def user_login():
        try:
            payload = request.get_json(force=True) or {}
            email = (payload.get("email") or "").strip()
            password = payload.get("password")

            if not email or not password:
                return jsonify({"message": "Missing required fields."}), 400
            if not isinstance(password, str) or len(password) < 6:
                return jsonify({"message": "Password must be at least 6 characters."}), 400

            safe_email = email.replace("'", "''")
            safe_password = str(password).replace("'", "''")

            rows = get_query_result(
                "SELECT name FROM public.users WHERE email = '{email}' AND password = '{pwd}' LIMIT 1;".format(
                    email=safe_email,
                    pwd=safe_password,
                )
            )

            if not rows:
                return jsonify({"message": "Invalid credentials. Please try again."}), 401

            name = rows[0][0] if rows[0] and len(rows[0]) > 0 else None
            user_id_res = get_query_result(
                "SELECT id FROM public.users WHERE email = '{email}' LIMIT 1;".format(email=safe_email)
            )
            user_id = user_id_res[0][0] if user_id_res else None

            # Create session
            session.clear()
            session["role"] = "user"
            session["user_id"] = user_id
            _touch_session()

            return jsonify({"message": "Login successful", "name": name, "user_id": user_id}), 200
        except Exception as e:
            return jsonify({"message": f"Login failed: {e}"}), 500

    # --- Admin login (creates session) ---
    @app.post("/admin_login")
    def admin_login():
        try:
            auth = request.authorization

            payload = None
            if auth is None:
                payload = request.get_json(force=True, silent=True) or {}

            if auth:
                user_id = (auth.username or "").strip()
                password = auth.password or ""
            else:
                user_id = (payload.get("user_id") or "").strip()
                password = payload.get("password") or ""

            if not user_id or not password:
                return jsonify({"success": False, "message": "Missing user_id or password"}), 400
            if len(user_id) < 3:
                return jsonify({"success": False, "message": "User ID must be at least 3 characters"}), 400
            if not isinstance(password, str) or len(password) < 6:
                return jsonify({"success": False, "message": "Password must be at least 6 characters"}), 400

            admin_user = (os.environ.get("ADMIN_USER") or "").strip()
            admin_pass = os.environ.get("ADMIN_PASSWORD") or ""

            # 1) Environment-based admin login (recommended for Render)
            if admin_user and admin_pass:
                if user_id == admin_user and password == admin_pass:
                    session.clear()
                    session["role"] = "admin"
                    session["user_id"] = user_id
                    _touch_session()
                    return jsonify({"success": True, "message": "Admin login successful."}), 200
                return jsonify({"success": False, "message": "Invalid credentials."}), 401

            # 2) Fallback: DB-based lookup (legacy behavior)
            existing = get_query_result(
                "SELECT id FROM public.users WHERE (id::text = '{uid}' OR name = '{uid}' OR email = '{uid}') "
                "AND password = '{pwd}' LIMIT 1;".format(
                    uid=user_id.replace("'", "''"),
                    pwd=str(password).replace("'", "''"),
                )
            )

            if not existing:
                return jsonify({"success": False, "message": "Invalid credentials."}), 401


            session.clear()
            session["role"] = "admin"
            session["user_id"] = user_id
            _touch_session()

            return jsonify({"success": True, "message": "Admin login successful."}), 200
        except Exception as e:
            return jsonify({"success": False, "message": f"Admin login failed: {e}"}), 500

    # --- Admin QR generation (creates QR codes) ---
    @app.post("/generate_qrcode")
    def generate_qrcode():
        try:
            payload = request.get_json(force=True) or {}
            quantity = payload.get("quantity", 1)
            try:
                quantity = int(quantity)
            except Exception:
                quantity = 1

            if quantity < 1 or quantity > 100:
                return jsonify({"message": "quantity must be between 1 and 100"}), 400

            result = generate_bulk_qr_codes(quantity)
            if not result:
                return jsonify({"message": "Error generating QR codes"}), 500

            if result.get("error"):
                return jsonify({"message": result.get("error")}), 500

            return jsonify({"batch_id": result.get("batch_id")}), 200

        except Exception as e:
            return jsonify({"message": f"Failed to generate QR codes: {e}"}), 500

    @app.get("/api/qrcode_batch_items")
    def api_qrcode_batch_items():
        try:
            count = request.args.get("count", "1")
            try:
                count = int(count)
            except Exception:
                count = 1

            count = max(1, min(100, count))

            rows = get_batch_qr_codes_from_db_by_created_at_limit(limit=count)

            items = []
            for r in (rows or []):
                file_path = r.get("file_path")
                if not file_path:
                    continue

                image_url = "/" + os.path.relpath(file_path, BASE_DIR).replace("\\\\", "/")
                items.append({"image_url": image_url, "qr_unique_id": r.get("qr_unique_id")})

            return jsonify({"items": items}), 200
        except Exception as e:
            return jsonify({"message": f"Failed to load QR batch items: {e}"}), 500

    @app.get("/api/admin/qr_codes")
    def api_admin_qr_codes():
        try:
            page = request.args.get("page", "1")
            page_size = request.args.get("page_size", "10")
            debug = str(request.args.get("debug", "0")).strip() in {"1", "true", "yes", "on"}

            try:
                page = int(page)
            except Exception:
                page = 1
            try:
                page_size = int(page_size)
            except Exception:
                page_size = 10

            page = max(1, page)
            page_size = max(1, min(100, page_size))
            offset = (page - 1) * page_size

            id_raw = request.args.get("id", "").strip()
            qr_unique_id = request.args.get("qr_unique_id", "").strip()
            created_at_order = request.args.get("created_at_order", "desc").strip().lower()
            activated_at_order = request.args.get("activated_at_order", "desc").strip().lower()

            is_sold = request.args.get("is_sold", "").strip().lower()
            is_activated = request.args.get("is_activated", "").strip().lower()

            id_value = None
            if id_raw:
                try:
                    id_value = int(id_raw)
                except Exception:
                    id_value = None

            if is_sold not in {"yes", "no", ""}:
                is_sold = ""
            if is_activated not in {"yes", "no", ""}:
                is_activated = ""

            if id_value is not None or qr_unique_id or is_sold or is_activated:
                total = 0
            else:
                total_res = get_query_result('SELECT COUNT(*) FROM public."qr_codes";')
                total = int(total_res[0][0]) if total_res else 0

            if created_at_order not in {"asc", "desc"}:
                created_at_order = "desc"
            if activated_at_order not in {"asc", "desc"}:
                activated_at_order = "desc"

            where_clauses = []
            if id_value is not None:
                where_clauses.append(f"id = {id_value}")

            if qr_unique_id:
                safe_q = str(qr_unique_id).replace("'", "''")
                where_clauses.append(f"qr_unique_id = '{safe_q}'")

            if is_sold:
                is_sold_bool = "true" if is_sold == "yes" else "false"
                where_clauses.append(f"is_sold = {is_sold_bool}")

            if is_activated:
                is_activated_bool = "true" if is_activated == "yes" else "false"
                where_clauses.append(f"is_activated = {is_activated_bool}")

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            sql_query = (
                'SELECT id,is_sold,is_activated,user_id,activated_at,created_at '
                'FROM public."qr_codes" '
                f"{where_sql} "
                f'ORDER BY created_at {created_at_order}, activated_at {activated_at_order}, id DESC '
                f'LIMIT {page_size} OFFSET {offset};'
            )

            rows_res = get_query_result(sql_query) or []
            if id_value is not None or qr_unique_id or is_sold or is_activated:
                total = len(rows_res)

            col_order = ["id", "is_sold", "is_activated", "user_id", "activated_at", "created_at"]
            rows = []
            for r in rows_res:
                obj = {col_order[i]: r[i] if i < len(r) else None for i in range(len(col_order))}

                qr_id = obj.get("id")
                if qr_id is not None:
                    file_row = get_query_result(
                        "SELECT file_path FROM public.\"qr_codes\" WHERE id = {q} LIMIT 1;".format(q=int(qr_id))
                    )
                    if file_row and file_row[0] and file_row[0][0]:
                        fp = file_row[0][0]
                        obj["image_url"] = "/" + os.path.relpath(fp, BASE_DIR).replace("\\\\", "/")

                rows.append(obj)

            payload = {"rows": rows, "page": page, "page_size": page_size, "total": total}
            if debug:
                payload["sql_query"] = sql_query
            return jsonify(payload), 200
        except Exception as e:
            return jsonify({"message": f"Failed to load admin QR codes: {e}"}), 500

    @app.post("/api/admin/qr_codes/attach_sold_to_user")
    def api_admin_attach_sold_to_user():
        """Attach an existing/ newly created user to a qr_code and mark it sold.

        Expected JSON payload:
          {
            "id": <qr_codes.id>,
            "user_id": <public.users.id>
          }
        """
        try:
            payload = request.get_json(force=True, silent=True) or {}
            id_raw = (payload.get("id") or request.args.get("id") or "").strip()
            user_id_raw = (payload.get("user_id") or request.args.get("user_id") or "").strip()

            if not id_raw:
                return jsonify({"message": "id is required"}), 400
            if not user_id_raw:
                return jsonify({"message": "user_id is required"}), 400

            try:
                id_value = int(id_raw)
                user_id_value = int(user_id_raw)
            except Exception:
                return jsonify({"message": "id and user_id must be integers"}), 400

            execute_query(
                f'UPDATE public."qr_codes" '
                f'SET is_sold = true, user_id = {user_id_value} '
                f'WHERE id = {id_value};'
            )

            return jsonify({"message": "Attached sold QR to user", "id": id_value, "user_id": user_id_value}), 200
        except Exception as e:
            return jsonify({"message": f"Failed to attach sold QR: {e}"}), 500

    @app.post("/api/admin/qr_codes/mark_sold")
    def api_admin_qr_mark_sold():
        """Mark a QR as sold and attach a user_id.

        Expected JSON payload:
          {
            "id": <qr_codes.id>,
            "flow": "existing",
            "email": "..."

            OR

            "id": <qr_codes.id>,
            "flow": "new",  (legacy; can also be used by admin directly)
            "name": "...",
            "email": "...",
            "password": "...",
            "contact_no": "+91XXXXXXXXXX"  (or any supported variant)
          }

        Also supports legacy querystring: ?id=<qr id>
        """

        try:
            # qr id (querystring or body)
            id_raw = request.args.get("id", "").strip()
            payload = request.get_json(force=True, silent=True) or {}
            if not id_raw:
                id_raw = str(payload.get("id") or "").strip()

            if not id_raw:
                return jsonify({"message": "id is required"}), 400

            try:
                id_value = int(id_raw)
            except Exception:
                return jsonify({"message": "id must be an integer"}), 400

            flow = (payload.get("flow") or "").strip().lower()
            if flow not in {"new", "existing"}:
                # backward/legacy compatibility: if no flow was sent, default to existing behavior
                # (since UI currently always sends flow, this mainly protects old clients)
                flow = "existing"
                if flow not in {"new", "existing"}:
                    return jsonify({"message": "flow must be 'new' or 'existing'"}), 400

            email = (payload.get("email") or "").strip()
            if not email:
                return jsonify({"message": "email is required"}), 400

            safe_email = email.replace("'", "''")

            def _get_user_id_by_email(email_in: str):
                safe_e = email_in.replace("'", "''")
                rows = get_query_result(
                    "SELECT id FROM public.users WHERE email = '{email}' LIMIT 1;".format(email=safe_e)
                )
                return rows[0][0] if rows else None

            def _create_user_and_get_id(name_in: str, email_in: str, password_in: str, contact_in: str):
                normalized_contact = _normalize_contact(contact_in)
                if not normalized_contact:
                    raise ValueError("Invalid contact number.")
                if not isinstance(password_in, str) or len(password_in) < 6:
                    raise ValueError("Password must be at least 6 characters.")

                existing_id = _get_user_id_by_email(email_in)
                if existing_id is not None:
                    raise ValueError("user already exists")

                max_id_res = get_query_result("SELECT COALESCE(MAX(id), 0) AS max_id FROM public.users;")
                max_id = max_id_res[0][0] if max_id_res else 0
                new_id = int(max_id) + 1

                safe_name = str(name_in).replace("'", "''")
                safe_email_inner = email_in.replace("'", "''")
                safe_contact = normalized_contact.replace("'", "''")
                safe_pwd = str(password_in).replace("'", "''")

                inserted_id = insert_and_get_id(
                    "INSERT INTO public.users (id, name, email, contact_no, password) "
                    f"VALUES ({new_id}, '{safe_name}', '{safe_email_inner}', '{safe_contact}', '{safe_pwd}') "
                    "RETURNING id;"
                )
                return inserted_id

            # Resolve user_id and update qr_codes
            user_id = None
            if flow == "existing":
                user_id = _get_user_id_by_email(email)
                if user_id is None:
                    return jsonify({"message": "No existing user found for provided email."}), 404

            else:  # new user
                name = (payload.get("name") or "").strip()
                password = payload.get("password")
                contact_no = payload.get("contact_no") or payload.get("contactNo") or payload.get("contactNumber")

                if not name or not password or not contact_no:
                    return jsonify({"message": "name, email, password, and contact_no are required for new flow"}), 400

                user_id = _create_user_and_get_id(name, email, password, contact_no)
                if user_id is None:
                    return jsonify({"message": "Failed to create user."}), 500

            execute_query(
                f'UPDATE public."qr_codes" '
                f'SET is_sold = true, user_id = {int(user_id)} '
                f'WHERE id = {int(id_value)};'
            )

            return jsonify({"message": "Marked as sold", "id": id_value, "user_id": int(user_id)}), 200
        except Exception as e:
            return jsonify({"message": f"Failed to mark as sold: {e}"}), 500


    @app.post("/api/user/qr_codes")
    def api_user_qr_codes():
        """Fetch QR codes linked to the current session user."""
        try:
            user_id = session.get("user_id")
            if user_id is None:
                return jsonify({"message": "Unauthorized"}), 401

            try:
                user_id_int = int(user_id)
            except Exception:
                return jsonify({"message": "Unauthorized"}), 401

            page = request.args.get("page", "1")
            page_size = request.args.get("page_size", "10")
            try:
                page = int(page)
            except Exception:
                page = 1
            try:
                page_size = int(page_size)
            except Exception:
                page_size = 10

            page = max(1, page)
            page_size = max(1, min(100, page_size))
            offset = (page - 1) * page_size

            total_res = get_query_result(
                'SELECT COUNT(*) FROM public."qr_codes" WHERE user_id = {uid};'.format(uid=user_id_int)
            )
            total = int(total_res[0][0]) if total_res else 0

            rows_res = get_query_result(
                'SELECT id,is_sold,is_activated,user_id,activated_at,created_at '
                'FROM public."qr_codes" '
                'WHERE user_id = {uid} '
                'ORDER BY created_at DESC '
                'LIMIT {ps} OFFSET {off};'.format(uid=user_id_int, ps=page_size, off=offset)
            ) or []

            col_order = ["id", "is_sold", "is_activated", "user_id", "activated_at", "created_at"]
            rows = []
            for r in rows_res:
                obj = {col_order[i]: r[i] if i < len(r) else None for i in range(len(col_order))}

                qr_id = obj.get("id")
                if qr_id is not None:
                    file_row = get_query_result(
                        'SELECT file_path FROM public."qr_codes" WHERE id = {q} LIMIT 1;'.format(q=int(qr_id))
                    )
                    if file_row and file_row[0] and file_row[0][0]:
                        fp = file_row[0][0]
                        obj["image_url"] = "/" + os.path.relpath(fp, BASE_DIR).replace("\\\\", "/")

                rows.append(obj)

            return jsonify({"rows": rows, "page": page, "page_size": page_size, "total": total}), 200
        except Exception as e:
            return jsonify({"message": f"Failed to load user QR codes: {e}"}), 500

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})



    @app.post("/api/user/qr_codes/activate")
    def api_user_activate_qr():
        """Activate a QR code for the current logged-in user.

        Expected JSON:
          { "qr_code_id": <qr_codes.id> }
        """
        try:
            payload = request.get_json(force=True, silent=True) or {}
            qr_code_id_val = payload.get("qr_code_id")
            if qr_code_id_val is None:
                qr_code_id_val = payload.get("id")
            if qr_code_id_val is None:
                qr_code_id_val = request.args.get("qr_code_id")

            # qr_code_id can arrive as int or string; normalize safely.
            qr_code_id_raw = "" if qr_code_id_val is None else str(qr_code_id_val).strip()
            if not qr_code_id_raw:
                return jsonify({"message": "qr_code_id is required"}), 400


            try:
                qr_code_id = int(qr_code_id_raw)
            except Exception:
                return jsonify({"message": "qr_code_id must be an integer"}), 400

            user_id = session.get("user_id")
            if user_id is None:
                return jsonify({"message": "Unauthorized"}), 401

            try:
                user_id_int = int(user_id)
            except Exception:
                return jsonify({"message": "Unauthorized"}), 401

            # Only allow activation if qr is owned by the session user.
            execute_query(
                f'UPDATE public."qr_codes" '
                f'SET is_activated = true, activated_at = COALESCE(activated_at, NOW()) '
                f'WHERE id = {qr_code_id} AND user_id = {user_id_int};'
            )

            return jsonify({"message": "QR activated", "qr_code_id": qr_code_id}), 200
        except Exception as e:
            return jsonify({"message": f"Failed to activate QR: {e}"}), 500

    @app.post("/api/user/students/by_qr_code_id")
    def api_user_get_students_by_qr_code_id():
        """Fetch saved student details for a given qr_code_id.

        IMPORTANT:
        - Frontend may open this form using either:
          - qr_code_id (public.qr_codes.id), OR
          - qr_unique_id (public.qr_codes.qr_unique_id)
        - This endpoint supports both and returns student details.
        """
        try:
            payload = request.get_json(force=True, silent=True) or {}

            qr_code_id_val = payload.get("qr_code_id")
            qr_unique_id_val = payload.get("qr_unique_id")

            # Support legacy key "id" or url params
            if qr_code_id_val is None:
                qr_code_id_val = payload.get("id")
            if qr_unique_id_val is None and qr_code_id_val is not None:
                # payload sent only one identifier; we'll attempt parsing as int first,
                # then fallback to unique-id lookup.
                # (No-op here; logic below already handles fallback.)
                pass


            if qr_code_id_val is None:
                qr_code_id_val = request.args.get("qr_code_id")
            if qr_unique_id_val is None:
                qr_unique_id_val = request.args.get("qr_unique_id")

            # Normalize inputs
            qr_code_id_raw = "" if qr_code_id_val is None else str(qr_code_id_val).strip()
            qr_unique_id_raw = "" if qr_unique_id_val is None else str(qr_unique_id_val).strip()

            if not qr_code_id_raw and not qr_unique_id_raw:
                return jsonify({"message": "qr_code_id or qr_unique_id is required"}), 400

            user_id = session.get("user_id")
            if user_id is None:
                return jsonify({"message": "Unauthorized"}), 401

            try:
                user_id_int = int(user_id)
            except Exception:
                return jsonify({"message": "Unauthorized"}), 401

            # Resolve qr_codes.id from either qr_code_id or qr_unique_id
            if qr_code_id_raw:
                try:
                    qr_code_id = int(qr_code_id_raw)
                except Exception:
                    qr_code_id = None
            else:
                qr_code_id = None

            if qr_code_id is None:
                # fallback: lookup by qr_unique_id
                if not qr_unique_id_raw:
                    return jsonify({"message": "qr_code_id or qr_unique_id is required"}), 400

                safe_q = str(qr_unique_id_raw).replace("'", "''")
                rows = get_query_result(
                    f"SELECT id FROM public.\"qr_codes\" WHERE qr_unique_id = '{safe_q}' LIMIT 1;"
                )

                if not rows:
                    return jsonify({"message": "QR not found"}), 404
                qr_code_id = int(rows[0][0])

            # Ensure QR belongs to session user.
            qr_owner = get_query_result(
                f'SELECT user_id FROM public."qr_codes" WHERE id = {qr_code_id} LIMIT 1;'
            )
            if not qr_owner:
                return jsonify({"message": "QR not found"}), 404
            if int(qr_owner[0][0]) != user_id_int:
                return jsonify({"message": "Forbidden"}), 403

            # Fetch student row by qr_code_id.
            sql = (
                'SELECT student_name, date_of_birth, age, blood_group, '
                'guardian_name, relationship, contact_number, email, address '
                'FROM public."students" '
                f'WHERE qr_code_id = {qr_code_id} '
                'LIMIT 1;'
            )

            rows = get_query_result(sql) or []
            if not rows:
                return jsonify({"message": "No details found"}), 404

            r = rows[0]
            details = {
                "name": r[0],
                "dob": r[1].strftime('%Y-%m-%d') if hasattr(r[1], 'strftime') else r[1],
                "age": r[2],
                "blood_group": r[3],
                "guardian_name": r[4],
                "relationship": r[5],
                "guardian_contact": r[6],
                "guardian_email": r[7],
                "address": (r[8] if len(r) > 8 else None),
            }
            return jsonify({"details": details}), 200

        except Exception as e:
            return jsonify({"message": f"Failed to fetch student details: {e}"}), 500


            # qr_code_id can arrive as int or string; normalize safely.
            qr_code_id_raw = "" if qr_code_id_val is None else str(qr_code_id_val).strip()
            if not qr_code_id_raw:
                return jsonify({"message": "qr_code_id is required"}), 400

            try:
                qr_code_id = int(qr_code_id_raw)
            except Exception:
                return jsonify({"message": "qr_code_id must be an integer"}), 400

            user_id = session.get("user_id")
            if user_id is None:
                return jsonify({"message": "Unauthorized"}), 401

            try:
                user_id_int = int(user_id)
            except Exception:
                return jsonify({"message": "Unauthorized"}), 401

            # Ensure the QR belongs to the user.
            qr_owner = get_query_result(
                f'SELECT user_id FROM public."qr_codes" WHERE id = {qr_code_id} LIMIT 1;'
            )
            if not qr_owner:
                return jsonify({"message": "QR not found"}), 404
            if int(qr_owner[0][0]) != user_id_int:
                return jsonify({"message": "Forbidden"}), 403

            # Fetch student row by qr_code_id.
            # Column names: best-effort mapping to what studentForm submits.
            sql = (
                'SELECT student_name, date_of_birth, age, blood_group, '
                'guardian_name, relationship, contact_number, email, address '
                'FROM public."students" '
                f'WHERE qr_code_id = {qr_code_id} '
                'LIMIT 1;'
            )

            rows = get_query_result(sql) or []
            if not rows:
                return jsonify({"message": "No details found"}), 404

            r = rows[0]
            # Map DB columns to frontend-expected keys.
            # Backend field order from SELECT:
            # 0 student_name, 1 date_of_birth, 2 age, 3 blood_group,
            # 4 guardian_name, 5 relationship, 6 contact_number, 7 email, 8 address
            details = {
                "name": r[0],
                "dob": r[1].strftime('%Y-%m-%d') if hasattr(r[1], 'strftime') else r[1],
                "age": r[2],
                "blood_group": r[3],
                "guardian_name": r[4],
                "relationship": r[5],
                "guardian_contact": r[6],
                "guardian_email": r[7],
                "address": (r[8] if len(r) > 8 else None),
            }
            return jsonify({"details": details}), 200

        except Exception as e:
            return jsonify({"message": f"Failed to fetch student details: {e}"}), 500

    @app.post("/api/user/students/upsert_by_qr_code_id")
    def api_user_students_upsert_by_qr_code_id():

        """Insert/update student details for the QR.

        Expected JSON:
          {
            qr_code_id: <qr_codes.id>,
            name, dob, age, blood_group, address,
            guardian_name, relationship, guardian_contact
          }
        """
        try:
            payload = request.get_json(force=True, silent=True) or {}
            qr_code_id_val = payload.get("qr_code_id")
            if qr_code_id_val is None:
                qr_code_id_val = payload.get("id")

            # qr_code_id can arrive as int or string; normalize safely.
            qr_code_id_raw = "" if qr_code_id_val is None else str(qr_code_id_val).strip()
            if not qr_code_id_raw:
                return jsonify({"message": "qr_code_id is required"}), 400

            try:
                qr_code_id = int(qr_code_id_raw)
            except Exception:
                return jsonify({"message": "qr_code_id must be an integer"}), 400

            user_id = session.get("user_id")
            if user_id is None:
                return jsonify({"message": "Unauthorized"}), 401
            user_id_int = int(user_id)

            # Verify QR belongs to user.
            qr_owner = get_query_result(
                f'SELECT user_id FROM public."qr_codes" WHERE id = {qr_code_id} LIMIT 1;'
            )
            if not qr_owner:
                return jsonify({"message": "QR not found"}), 404
            if int(qr_owner[0][0]) != user_id_int:
                return jsonify({"message": "Forbidden"}), 403

            name = (payload.get("name") or "").strip()
            dob = (payload.get("dob") or "").strip()
            age = payload.get("age")
            blood_group = (payload.get("blood_group") or "").strip()
            address = (payload.get("address") or "").strip()
            guardian_name = (payload.get("guardian_name") or "").strip()
            relationship = (payload.get("relationship") or "").strip()
            guardian_contact = (payload.get("guardian_contact") or "").strip()
            guardian_email = (payload.get("guardian_email") or "").strip()

            # Match current DB schema:
            # public.students has:
            #   student_name, date_of_birth, age, blood_group,
            #   guardian_name, relationship, contact_number, email (nullable), address (nullable)
            # So: address and guardian_email are optional; do NOT require them.
            required = [name, dob, blood_group, guardian_name, relationship, guardian_contact]
            if any(not v for v in required):
                return jsonify({"message": "Missing required fields"}), 400

            try:
                age_int = int(age)
            except Exception:
                age_int = None


            # basic escaping for single quotes
            def esc(s):
                return str(s).replace("'", "''")

            # Upsert pattern: try update first, if zero rows then insert.
            # Match actual schema in public.students:
            #   student_name, date_of_birth, guardian_name, contact_number, etc.
            # UI payload fields are:
            #   name, dob, guardian_name, guardian_contact, address (no address column in schema)
            address_sql = 'NULL' if not address else "'{}'".format(esc(address))
            email_sql = 'NULL'
            if guardian_email:
                email_sql = "'{}'".format(esc(guardian_email))

            update_sql = (
                'UPDATE public."students" '
                'SET student_name = \'{student_name}\', '
                'date_of_birth = \'{dob}\', '
                'age = {age}, '
                'blood_group = \'{bg}\', '
                'guardian_name = \'{gname}\', '
                'relationship = \'{rel}\', '
                'contact_number = \'{contact}\', '
                'email = {email}, '
                'address = {address} '
                'WHERE qr_code_id = {qr_code_id};'
            ).format(
                student_name=esc(name),
                dob=esc(dob),
                age='NULL' if age_int is None else str(age_int),
                bg=esc(blood_group),
                gname=esc(guardian_name),
                rel=esc(relationship),
                contact=esc(guardian_contact),
                email=email_sql,
                address=address_sql,
                qr_code_id=qr_code_id,
            )


            # Execute UPDATE; if it fails, return details to frontend for fast debugging.
            updated_ok = execute_query(update_sql)
            if not updated_ok:
                return jsonify({
                    "message": "Failed to update student details.",
                    "debug": {
                        "update_sql": update_sql,
                        "payload": payload,
                    },
                }), 500

            # Check if row exists
            check = get_query_result(
                f'SELECT COUNT(*) FROM public."students" WHERE qr_code_id = {qr_code_id};'
            ) or []
            count = int(check[0][0]) if check else 0

            if count == 0:
                insert_sql = (
                    'INSERT INTO public."students" '
                    '(qr_code_id, student_name, date_of_birth, age, blood_group, guardian_name, relationship, contact_number, email, address) '
                    'VALUES '
                    '({qr_id}, \'{student_name}\', \'{dob}\', {age}, \'{bg}\', \'{gname}\', \'{rel}\', \'{contact}\', {email}, {address});'
                ).format(
                    qr_id=qr_code_id,
                    student_name=esc(name),
                    dob=esc(dob),
                    age='NULL' if age_int is None else str(age_int),
                    bg=esc(blood_group),
                    gname=esc(guardian_name),
                    rel=esc(relationship),
                    contact=esc(guardian_contact),
                    email=email_sql,
                    address=address_sql,
                )
                inserted_ok = execute_query(insert_sql)
                if not inserted_ok:
                    return jsonify({
                        "message": "Failed to insert student details.",
                        "debug": {
                            "insert_sql": insert_sql,
                            "payload": payload,
                        },
                    }), 500


            return jsonify({"message": "Details saved successfully"}), 200

        except Exception as e:
            return jsonify({"message": f"Failed to save student details: {e}"}), 500

    def _resolve_qr_file_path_by_id(qr_id: int) -> str | None:
        try:
            rows = get_query_result(
                'SELECT file_path, file_name FROM public."qr_codes" WHERE id = {q} LIMIT 1;'.format(q=int(qr_id))
            )
            if not rows:
                return None
            fp = rows[0][0] if rows[0] and len(rows[0]) > 0 else None
            return fp
        except Exception:
            return None

    @app.get("/download_single_qr")
    def download_single_qr():
        """Download a single QR image as an attachment.

        Querystring:
          ?id=<qr_codes.id>
        """
        try:
            id_raw = (request.args.get("id") or "").strip()
            if not id_raw:
                return jsonify({"message": "id is required"}), 400
            try:
                qr_id = int(id_raw)
            except Exception:
                return jsonify({"message": "id must be an integer"}), 400

            file_path = _resolve_qr_file_path_by_id(qr_id)
            if not file_path:
                return jsonify({"message": "QR not found"}), 404
            if not os.path.exists(file_path):
                return jsonify({"message": "QR file not found on server"}), 404

            file_name = os.path.basename(file_path) or f"qr_{qr_id}.jpg"
            return send_from_directory(
                os.path.dirname(file_path),
                os.path.basename(file_path),
                as_attachment=True,
                download_name=file_name,
            )
        except Exception as e:
            return jsonify({"message": f"Failed to download QR: {e}"}), 500

    @app.get("/download_qr_batch")
    def download_qr_batch():

        try:
            count = request.args.get("count", "1")
            try:
                count = int(count)
            except Exception:
                count = 1

            if count < 1:
                count = 1
            if count > 100:
                count = 100

            rows = get_batch_qr_codes_from_db_by_created_at_limit(limit=count)
            if not rows:
                return jsonify({"message": "No QR codes found to download."}), 404

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for r in rows:
                    file_path = r.get("file_path")
                    file_name = r.get("file_name") or os.path.basename(file_path or "")

                    if not file_path or not os.path.exists(file_path):
                        continue

                    zf.write(file_path, arcname=file_name)

            zip_buffer.seek(0)

            zip_filename = f"qr_codes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            return zip_buffer.getvalue(), 200, {
                "Content-Type": "application/zip",
                "Content-Disposition": f'attachment; filename="{zip_filename}"',
            }
        except Exception as e:
            return jsonify({"message": f"Failed to download QR batch: {e}"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

