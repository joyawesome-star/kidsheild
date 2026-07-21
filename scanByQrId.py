"""
scanByQrId.py - QR Code Scan Handler

When a QR code is scanned:
1. Not sold     → Display message: "This QR code has not been sold yet."
2. Sold         → Display message: "This QR code is sold but not yet activated."
3. Activated    → Redirect to studentForm.html in readonly mode with student data.

QR codes encode: /scan/<qr_unique_id>
The scan endpoint looks up the qr_unique_id in the qr_codes table to determine the state.
"""

import os
from flask import Blueprint, jsonify, redirect, request, render_template_string, url_for
from urllib.parse import urlencode

from supabaseConnect import get_query_result

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
scan_bp = Blueprint("scan", __name__)


# ---------------------------------------------------------------------------
# Helper: resolve qr_codes.id from qr_unique_id, return full QR row
# ---------------------------------------------------------------------------
def _resolve_qr_by_unique_id(qr_unique_id: str) -> dict | None:
    """Look up a QR code by its qr_unique_id and return row dict or None."""
    safe_q = str(qr_unique_id).replace("'", "''")
    rows = get_query_result(
        f'SELECT id, is_sold, is_activated, user_id, qr_unique_id '
        f'FROM public."qr_codes" '
        f"WHERE qr_unique_id = '{safe_q}' "
        f'LIMIT 1;'
    )
    if not rows:
        return None

    r = rows[0]
    return {
        "id": int(r[0]) if r[0] is not None else None,
        "is_sold": bool(r[1]) if r[1] is not None else False,
        "is_activated": bool(r[2]) if r[2] is not None else False,
        "user_id": int(r[3]) if r[3] is not None else None,
        "qr_unique_id": str(r[4]) if r[4] is not None else qr_unique_id,
    }


def _fetch_student_details_by_qr_code_id(qr_code_id: int) -> dict | None:
    """Fetch student details for the given qr_code_id (no auth check, public)."""
    rows = get_query_result(
        'SELECT student_name, date_of_birth, age, blood_group, '
        'guardian_name, relationship, contact_number, email, address '
        'FROM public."students" '
        f'WHERE qr_code_id = {qr_code_id} '
        'LIMIT 1;'
    )
    if not rows:
        return None

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
    return details


# ---------------------------------------------------------------------------
# NOT_SOLD page template
# ---------------------------------------------------------------------------
NOT_SOLD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Code - Not Sold</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a2e;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .card {
            background: rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 15px;
            box-shadow: 0 15px 50px rgba(0, 0, 0, 0.4);
            max-width: 480px;
            width: 100%;
            padding: 40px 30px;
            text-align: center;
            color: white;
        }
        .icon { font-size: 64px; margin-bottom: 20px; }
        h1 { font-size: 1.8em; margin-bottom: 15px; }
        p { font-size: 15px; color: #a5b4fc; line-height: 1.6; }
        .badge {
            display: inline-block;
            margin-top: 20px;
            padding: 8px 20px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">🔒</div>
        <h1>QR Code Not Yet Sold</h1>
        <p>This QR code has not been sold yet. Please contact the administrator if you believe this is an error.</p>
        <div class="badge">QR ID: {{ qr_unique_id }}</div>
    </div>
</body>
</html>"""

NOT_ACTIVATED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Code - Not Activated</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a2e;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .card {
            background: rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 15px;
            box-shadow: 0 15px 50px rgba(0, 0, 0, 0.4);
            max-width: 480px;
            width: 100%;
            padding: 40px 30px;
            text-align: center;
            color: white;
        }
        .icon { font-size: 64px; margin-bottom: 20px; }
        h1 { font-size: 1.8em; margin-bottom: 15px; }
        p { font-size: 15px; color: #a5b4fc; line-height: 1.6; }
        .badge {
            display: inline-block;
            margin-top: 20px;
            padding: 8px 20px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⏳</div>
        <h1>QR Code Not Yet Activated</h1>
        <p>This QR code has been sold but not yet activated. The owner needs to activate it before the student details can be viewed.</p>
        <div class="badge">QR ID: {{ qr_unique_id }}</div>
    </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Route: GET /scan/<qr_unique_id>  (public, no auth required)
# ---------------------------------------------------------------------------
@scan_bp.route("/scan/<qr_unique_id>")
def handle_scan(qr_unique_id: str):
    """
    Main scan handler.  All QR codes encode /scan/<qr_unique_id>.
    Based on the QR state in the database, we:
      - Not sold     → show an info page
      - Sold         → show an info page
      - Activated    → redirect to studentForm.html in readonly mode
    """
    qr = _resolve_qr_by_unique_id(qr_unique_id)
    if qr is None:
        # QR not found in DB
        return render_template_string(
            """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>QR Not Found</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'Segoe UI',sans-serif;background:#1a1a2e;min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px;color:white;}
.card{background:rgba(255,255,255,0.08);backdrop-filter:blur(15px);border:1px solid rgba(255,255,255,0.15);border-radius:15px;padding:40px 30px;max-width:480px;text-align:center;}
.icon{font-size:64px;margin-bottom:20px;}
h1{font-size:1.8em;margin-bottom:15px;}
p{color:#a5b4fc;line-height:1.6;}
</style></head>
<body><div class="card"><div class="icon">❓</div><h1>QR Code Not Found</h1><p>The QR code you scanned could not be found in the system. It may have been deleted or is invalid.</p></div></body></html>"""
        ), 404

    # --- State 1: NOT SOLD ---
    if not qr["is_sold"]:
        return render_template_string(
            NOT_SOLD_HTML,
            qr_unique_id=qr["qr_unique_id"],
        )

    # --- State 2: SOLD but NOT ACTIVATED ---
    if qr["is_sold"] and not qr["is_activated"]:
        return render_template_string(
            NOT_ACTIVATED_HTML,
            qr_unique_id=qr["qr_unique_id"],
        )

    # --- State 3: ACTIVATED ---
    # Redirect to studentForm.html in readonly/static mode with qr_code_id
    # The form will fetch data at runtime using the public API endpoint.
    params = urlencode({
        "qr_code_id": qr["id"],
        "mode": "view",
        "readonly": "1",
        "_scanned": "1",  # flag to tell the form it was opened via scan (hide buttons, use public API)
    })
    student_form_url = f"/studentForm.html?{params}"
    return redirect(student_form_url)


# ---------------------------------------------------------------------------
# Route: GET /api/public/student-by-qr-code  (public, no auth required)
# Used by studentForm.html when opened via QR scan (_scanned=1)
# ---------------------------------------------------------------------------
@scan_bp.route("/api/public/student-by-qr-code")
def api_public_student_by_qr_code():
    """
    Public endpoint to fetch student details by qr_code_id.
    No authentication required – this is for scanned QR codes.
    """
    qr_code_id_raw = request.args.get("qr_code_id", "").strip()
    if not qr_code_id_raw:
        return jsonify({"message": "qr_code_id is required"}), 400

    try:
        qr_code_id = int(qr_code_id_raw)
    except ValueError:
        return jsonify({"message": "qr_code_id must be an integer"}), 400

    details = _fetch_student_details_by_qr_code_id(qr_code_id)
    if details is None:
        return jsonify({"message": "No student details found"}), 404

    return jsonify({"details": details}), 200

