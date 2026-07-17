import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

import qrcode
from sqlalchemy import create_engine, text

# --- DB connection (as provided by user) ---
DATABASE_URL = "postgresql://postgres.yjakxhhmesbncbxmibfp:m4JFPkWCxOtBayj7@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres"
_engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# --- QR code configuration ---
# Store images inside this project folder at: QR code project/qrCodes
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_CODE_FOLDER = os.path.join(BASE_DIR, "qrCodes")
QR_CODE_SIZE = 10  # Box size
QR_CODE_BORDER = 5  # Border size in boxes


def ensure_qr_folder() -> None:
    os.makedirs(QR_CODE_FOLDER, exist_ok=True)


def _insert_qr_row_and_get_new_id(qr_unique_id: str) -> int:
    """Insert a qr_codes row and return computed new id.

    This function tries a safer INSERT first (only columns we assume exist),
    and then falls back to including file fields.
    """
    with _engine.begin() as conn:
        max_id_res = conn.execute(text("SELECT COALESCE(MAX(id), 0) AS max_id FROM public.qr_codes;"))
        max_id = max_id_res.fetchall()[0][0]
        new_id = int(max_id) + 1

        # Variant A: omit file_name/file_path (most schemas allow null defaults)
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO public.qr_codes
                      (id, qr_unique_id, is_sold, is_activated, user_id, activated_at)
                    VALUES
                      (:id, :qr_unique_id, false, false, NULL, NULL);
                    """
                ),
                {"id": new_id, "qr_unique_id": qr_unique_id},
            )
            return new_id
        except Exception:
            # Variant B: include file_name/file_path if required by schema
            conn.execute(
                text(
                    """
                    INSERT INTO public.qr_codes
                      (id, qr_unique_id, is_sold, is_activated, user_id, activated_at, file_name, file_path)
                    VALUES
                      (:id, :qr_unique_id, false, false, NULL, NULL, NULL, NULL);
                    """
                ),
                {"id": new_id, "qr_unique_id": qr_unique_id},
            )
            return new_id



def _update_qr_file_fields(qr_unique_id: str, file_name: str, file_path: str) -> None:
    # IMPORTANT: wrap in a transaction so UPDATE is committed.
    with _engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE public.qr_codes
                SET file_name = :file_name,
                    file_path = :file_path,
                    activated_at = NULL
                WHERE qr_unique_id = :qr_unique_id;
                """
            ),
            {"qr_unique_id": qr_unique_id, "file_name": file_name, "file_path": file_path},
        )


def _generate_qr_image(data: str, output_path: str) -> None:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=QR_CODE_SIZE,
        border=QR_CODE_BORDER,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(output_path)


def generate_qr_code_with_db() -> Optional[Dict[str, Any]]:
    """Generate a single QR and persist metadata in public.qr_codes."""
    qr_unique_id = None
    try:
        ensure_qr_folder()

        qr_unique_id = uuid.uuid4().hex
        qr_id = _insert_qr_row_and_get_new_id(qr_unique_id=qr_unique_id)

        file_name = f"{qr_unique_id}.jpg"
        file_path = os.path.join(QR_CODE_FOLDER, file_name)

        # QR payload can be the unique id (safer + stable)
        _generate_qr_image(data=qr_unique_id, output_path=file_path)

        _update_qr_file_fields(
            qr_unique_id=qr_unique_id,
            file_name=file_name,
            file_path=file_path,
        )

        return {
            "qr_id": qr_id,
            "qr_unique_id": qr_unique_id,
            "file_name": file_name,
            "file_path": file_path,
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        err = str(e)
        print(f"Error generating QR code (qr_unique_id={qr_unique_id}): {err}")
        return {
            "error": err,
            "qr_unique_id": qr_unique_id,
        }



def generate_bulk_qr_codes(quantity: int) -> Optional[Dict[str, Any]]:
    """Generate multiple QR codes and persist them. Returns details on failure."""
    try:
        ensure_qr_folder()

        batch_id = uuid.uuid4().hex
        created: List[Dict[str, Any]] = []

        for i in range(quantity):
            qr_result = generate_qr_code_with_db()
            if not qr_result:
                return {
                    "batch_id": batch_id,
                    "quantity": len(created),
                    "error": f"Failed to generate qr code #{i + 1}/{quantity} (empty result)",
                }

            if "error" in qr_result:
                return {
                    "batch_id": batch_id,
                    "quantity": len(created),
                    "error": f"Failed to generate qr code #{i + 1}/{quantity}: {qr_result.get('error')}",
                    "qr_unique_id": qr_result.get("qr_unique_id"),
                }

            created.append(qr_result)
            print(f"Generated QR {i + 1}/{quantity} - qr_unique_id={qr_result['qr_unique_id']}")

        return {
            "batch_id": batch_id,
            "qr_codes": created,
            "quantity": len(created),
        }
    except Exception as e:
        err = str(e)
        print(f"Error generating bulk QR codes: {err}")
        return {
            "quantity": 0,
            "error": err,
        }



def get_batch_qr_codes_from_db_by_created_at_limit(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Since the required schema doesn't include a batch table, the simplest way to
    display "recently created" QR codes is by created_at order.
    This can be replaced later if you add a qr_batches table.
    """
    try:
        with _engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, qr_unique_id, file_path, file_name, created_at
                    FROM public.qr_codes
                    ORDER BY created_at DESC
                    LIMIT :limit;
                    """
                ),
                {"limit": int(limit)},
            ).fetchall()

        return [
            {
                "qr_id": r[0],
                "qr_unique_id": r[1],
                "file_path": r[2],
                "file_name": r[3],
                "created_at": r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4]),
            }
            for r in rows
        ]
    except Exception as e:
        print(f"Error retrieving QR codes: {e}")
        return []


def delete_qr_code(qr_unique_id: str) -> bool:
    """
    Soft-delete not requested by schema; implement hard delete for now.
    """
    try:
        with _engine.connect() as conn:
            conn.execute(text("DELETE FROM public.qr_codes WHERE qr_unique_id = :q;"), {"q": qr_unique_id})
        return True
    except Exception as e:
        print(f"Error deleting QR code: {e}")
        return False
