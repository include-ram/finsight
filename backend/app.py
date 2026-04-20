"""
FinSight - Flask API Server
Handles document uploads, OCR processing, and dashboard data retrieval.
All AWS credentials come from the EC2 LabRole — never hardcoded.
"""

import os
import uuid
import logging
import tempfile
from datetime import datetime
from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from s3_handler import S3Handler
from db_handler import DBHandler
from ocr_processor import OCRProcessor
from categorizer import Categorizer
from ses_handler import SESHandler

# ── Bootstrap ────────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload cap
app.secret_key = os.getenv("FLASK_SECRET_KEY", "finsight-dev-secret-change-in-prod")

# Allowed file extensions
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp"}

# Singleton service objects (created once at startup)
s3  = S3Handler()
db  = DBHandler()
ocr = OCRProcessor()
cat = Categorizer()
ses = SESHandler()


# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def error_response(message: str, status: int = 400):
    return jsonify({"error": message}), status


# ── Auth helpers ─────────────────────────────────────────────────────────────
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return error_response("Not authenticated", 401)
        return f(*args, **kwargs)
    return decorated

def current_user_id():
    return session.get("user_id")


# ── Auth endpoints ────────────────────────────────────────────────────────────
@app.route("/auth/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    email    = (data.get("email") or "").strip().lower() or None
    if not username or not password:
        return error_response("Username and password are required")
    if len(username) < 3:
        return error_response("Username must be at least 3 characters")
    if len(password) < 6:
        return error_response("Password must be at least 6 characters")
    try:
        existing = db._execute("SELECT id FROM users WHERE username = %s", (username,), fetch="one")
        if existing:
            return error_response("Username already taken")
        pw_hash = generate_password_hash(password)
        row = db._execute(
            "INSERT INTO users (username, password_hash, email) VALUES (%s, %s, %s)"
            " RETURNING id, username, email",
            (username, pw_hash, email), fetch="one"
        )
        session["user_id"] = str(row["id"])
        session["username"] = row["username"]
        session["email"]    = row.get("email") or ""
        return jsonify({"id": str(row["id"]), "username": row["username"], "email": row.get("email") or ""}), 201
    except Exception as exc:
        logger.exception("Register failed")
        return error_response(f"Registration failed: {exc}", 500)


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    try:
        row = db._execute(
            "SELECT id, username, password_hash, email FROM users WHERE username = %s",
            (username,), fetch="one"
        )
        if not row or not check_password_hash(row["password_hash"], password):
            return error_response("Invalid username or password", 401)
        session["user_id"]  = str(row["id"])
        session["username"] = row["username"]
        session["email"]    = row.get("email") or ""
        return jsonify({"id": str(row["id"]), "username": row["username"], "email": row.get("email") or ""})
    except Exception as exc:
        logger.exception("Login failed")
        return error_response(f"Login failed: {exc}", 500)


@app.route("/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/auth/me", methods=["GET"])
def me():
    if "user_id" not in session:
        return error_response("Not authenticated", 401)
    return jsonify({
        "id": session["user_id"],
        "username": session["username"],
        "email": session.get("email", ""),
    })


@app.route("/auth/email", methods=["PUT"])
@login_required
def update_email():
    """PUT /auth/email — update notification email for the current user."""
    data  = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower() or None
    try:
        db._execute(
            "UPDATE users SET email = %s WHERE id = %s",
            (email, current_user_id()),
        )
        session["email"] = email or ""
        return jsonify({"email": email or ""})
    except Exception as exc:
        return error_response(f"Failed to update email: {exc}", 500)


# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    """Quick liveness probe for load balancers / monitoring."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# ── Upload endpoint ───────────────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
@login_required
def upload_document():
    """
    POST /upload
    Accepts a multipart/form-data file upload.
    1. Validates the file type.
    2. Uploads the raw file to S3.
    3. Inserts a pending document record into RDS.
    4. Runs OCR + categorisation inline (for demo simplicity).
    5. Updates the RDS record with extracted data.
    Returns the new document record as JSON.
    """
    if "file" not in request.files:
        return error_response("No file part in request")

    file = request.files["file"]
    if file.filename == "":
        return error_response("No file selected")

    if not allowed_file(file.filename):
        return error_response(
            f"File type not allowed. Permitted: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    original_name = secure_filename(file.filename)
    doc_id = str(uuid.uuid4())
    s3_key = f"uploads/{doc_id}/{original_name}"

    # ── Step 1: Upload to S3 ────────────────────────────────────────────────
    try:
        file_bytes = file.read()
        s3.upload_bytes(file_bytes, s3_key, file.content_type)
        logger.info("Uploaded %s to S3 key %s", original_name, s3_key)
    except Exception as exc:
        logger.exception("S3 upload failed")
        return error_response(f"S3 upload failed: {exc}", 500)

    # ── Step 2: Insert pending record into RDS ──────────────────────────────
    try:
        db.insert_document(
            doc_id=doc_id,
            filename=original_name,
            s3_key=s3_key,
            status="processing",
            user_id=current_user_id(),
        )
    except Exception as exc:
        logger.exception("DB insert failed")
        return error_response(f"Database error: {exc}", 500)

    # ── Step 3: OCR processing ──────────────────────────────────────────────
    try:
        with tempfile.NamedTemporaryFile(
            suffix=f"_{original_name}", delete=False
        ) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        ocr_result = ocr.process_file(tmp_path)
        os.unlink(tmp_path)  # clean up temp file

        # Categorise the document
        category_result = cat.categorize(ocr_result.get("raw_text", ""))

        # Persist extracted fields
        for field_name, field_value in ocr_result.get("fields", {}).items():
            db.insert_extracted_data(
                document_id=doc_id,
                field_name=field_name,
                field_value=str(field_value),
                confidence=ocr_result.get("confidence", 0.0),
            )

        # Persist category
        db.insert_category(
            document_id=doc_id,
            category=category_result["category"],
            confidence=category_result["confidence"],
        )

        # Update document status to completed
        db.update_document_status(doc_id, "completed")
        logger.info("OCR completed for document %s", doc_id)

    except Exception as exc:
        logger.exception("OCR processing failed for %s", doc_id)
        db.update_document_status(doc_id, "failed")
        # Return partial success — file is in S3, OCR failed
        return (
            jsonify(
                {
                    "document_id": doc_id,
                    "status": "failed",
                    "warning": f"File uploaded but OCR failed: {exc}",
                }
            ),
            207,
        )

    # ── Step 4: Send SES notification (non-blocking best-effort) ────────────
    try:
        user_email = session.get("email") or ""
        if user_email:
            fields_map = {f["field_name"]: f["field_value"] for f in db.get_extracted_data(doc_id)}
            ses.send_upload_notification(
                recipient=user_email,
                username=session.get("username", ""),
                doc_info={
                    "filename": original_name,
                    "category": category_result["category"],
                    "amount":   fields_map.get("total_amount", "–"),
                    "date":     fields_map.get("primary_date", "–"),
                },
            )
    except Exception:
        pass  # email failure must never break the upload response

    # ── Step 5: Return completed record ─────────────────────────────────────
    doc = db.get_document_by_id(doc_id)
    return jsonify(doc), 201


# ── Process endpoint (called by Lambda) ──────────────────────────────────────
@app.route("/process", methods=["POST"])
def process_document():
    """
    POST /process
    Called by the Lambda trigger_ocr function when a new file lands in S3.
    Body: { "s3_key": "uploads/<uuid>/<filename>", "document_id": "<uuid>" }
    """
    data = request.get_json(force=True)
    if not data or "s3_key" not in data:
        return error_response("Missing s3_key in request body")

    s3_key = data["s3_key"]
    doc_id = data.get("document_id")

    # If no doc_id provided, look it up by s3_key
    if not doc_id:
        doc = db.get_document_by_s3_key(s3_key)
        if not doc:
            return error_response(f"No document found for s3_key: {s3_key}", 404)
        doc_id = doc["id"]

    logger.info("Processing document %s from S3 key %s", doc_id, s3_key)

    # Download file from S3 to a temp location
    try:
        with tempfile.NamedTemporaryFile(
            suffix=f"_{os.path.basename(s3_key)}", delete=False
        ) as tmp:
            s3.download_file(s3_key, tmp.name)
            tmp_path = tmp.name
    except Exception as exc:
        logger.exception("S3 download failed for key %s", s3_key)
        return error_response(f"S3 download failed: {exc}", 500)

    try:
        ocr_result = ocr.process_file(tmp_path)
        category_result = cat.categorize(ocr_result.get("raw_text", ""))

        for field_name, field_value in ocr_result.get("fields", {}).items():
            db.insert_extracted_data(
                document_id=doc_id,
                field_name=field_name,
                field_value=str(field_value),
                confidence=ocr_result.get("confidence", 0.0),
            )

        db.insert_category(
            document_id=doc_id,
            category=category_result["category"],
            confidence=category_result["confidence"],
        )

        db.update_document_status(doc_id, "completed")
        os.unlink(tmp_path)

        return jsonify({"document_id": doc_id, "status": "completed", "ocr": ocr_result})

    except Exception as exc:
        logger.exception("Processing failed for document %s", doc_id)
        db.update_document_status(doc_id, "failed")
        return error_response(f"Processing failed: {exc}", 500)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Document listing ──────────────────────────────────────────────────────────
@app.route("/documents", methods=["GET"])
@login_required
def list_documents():
    """
    GET /documents?status=completed&limit=50&offset=0
    Returns paginated list of all documents with their categories.
    """
    status     = request.args.get("status")
    limit      = int(request.args.get("limit", 50))
    offset     = int(request.args.get("offset", 0))
    search     = request.args.get("search") or None
    date_from  = request.args.get("date_from") or None
    date_to    = request.args.get("date_to") or None
    min_amount = float(request.args["min_amount"]) if request.args.get("min_amount") else None
    max_amount = float(request.args["max_amount"]) if request.args.get("max_amount") else None

    try:
        kwargs = dict(
            status=status, limit=limit, offset=offset,
            user_id=current_user_id(), search=search,
            date_from=date_from, date_to=date_to,
            min_amount=min_amount, max_amount=max_amount,
        )
        docs  = db.get_all_documents(**kwargs)
        total = db.count_documents(
            status=status, user_id=current_user_id(), search=search,
            date_from=date_from, date_to=date_to,
            min_amount=min_amount, max_amount=max_amount,
        )
        return jsonify({"total": total, "limit": limit, "offset": offset, "documents": docs})
    except Exception as exc:
        logger.exception("Failed to list documents")
        return error_response(f"Database error: {exc}", 500)


# ── Single document detail ────────────────────────────────────────────────────
@app.route("/documents/<doc_id>", methods=["GET"])
@login_required
def get_document(doc_id: str):
    """
    GET /documents/<id>
    Returns the full document record including extracted fields, category,
    and a presigned S3 URL for viewing the original file.
    """
    try:
        doc = db.get_document_by_id(doc_id)
    except Exception as exc:
        logger.exception("DB lookup failed for %s", doc_id)
        return error_response(f"Database error: {exc}", 500)

    if not doc:
        return error_response("Document not found", 404)

    # Attach a short-lived presigned URL so the frontend can preview the file
    try:
        doc["presigned_url"] = s3.get_presigned_url(doc["s3_key"], expiry_seconds=3600)
    except Exception:
        doc["presigned_url"] = None  # non-fatal — just skip the preview link

    # Fetch extracted fields
    try:
        doc["extracted_data"] = db.get_extracted_data(doc_id)
        doc["categories"] = db.get_categories(doc_id)
    except Exception as exc:
        logger.exception("Could not load extracted data for %s", doc_id)
        doc["extracted_data"] = []
        doc["categories"] = []

    return jsonify(doc)


# ── CSV export ───────────────────────────────────────────────────────────────
@app.route("/documents/export", methods=["GET"])
@login_required
def export_documents():
    """
    GET /documents/export
    Downloads all of the current user's documents as a CSV file.
    """
    import csv, io
    from flask import Response

    docs = db.get_all_documents(limit=10000, user_id=current_user_id())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["filename", "upload_date", "status", "category", "vendor", "amount", "document_date"])
    for doc in docs:
        fields = {f["field_name"]: f["field_value"] for f in db.get_extracted_data(doc["id"])}
        writer.writerow([
            doc["filename"],
            str(doc.get("upload_date", ""))[:19],
            doc.get("status", ""),
            doc.get("category", ""),
            fields.get("vendor_name", ""),
            fields.get("total_amount", ""),
            fields.get("primary_date", ""),
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=finsight_export.csv"},
    )


# ── Delete document ──────────────────────────────────────────────────────────
@app.route("/documents/<doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id: str):
    """
    DELETE /documents/<id>
    Removes the document from S3 and deletes all DB records (cascades to
    extracted_data and categories).
    """
    try:
        doc = db.get_document_by_id(doc_id)
    except Exception as exc:
        return error_response(f"Database error: {exc}", 500)

    if not doc:
        return error_response("Document not found", 404)

    # Delete from S3 (non-fatal if object already gone)
    try:
        s3.delete_object(doc["s3_key"])
    except Exception as exc:
        logger.warning("S3 delete failed for %s (continuing): %s", doc["s3_key"], exc)

    # Delete from DB (cascades to extracted_data + categories)
    try:
        db._execute("DELETE FROM documents WHERE id = %s", (doc_id,))
    except Exception as exc:
        logger.exception("DB delete failed for %s", doc_id)
        return error_response(f"Database error: {exc}", 500)

    logger.info("Deleted document %s (%s)", doc_id, doc["filename"])
    return jsonify({"deleted": True, "id": doc_id}), 200


# ── Reprocess a single document ──────────────────────────────────────────────
@app.route("/documents/<doc_id>/reprocess", methods=["POST"])
@login_required
def reprocess_document(doc_id: str):
    """Re-run OCR + categorisation on an already-uploaded document."""
    try:
        doc = db.get_document_by_id(doc_id)
    except Exception as exc:
        return error_response(f"Database error: {exc}", 500)
    if not doc:
        return error_response("Document not found", 404)

    try:
        with tempfile.NamedTemporaryFile(
            suffix=f"_{os.path.basename(doc['s3_key'])}", delete=False
        ) as tmp:
            s3.download_file(doc["s3_key"], tmp.name)
            tmp_path = tmp.name

        ocr_result = ocr.process_file(tmp_path)
        os.unlink(tmp_path)
        category_result = cat.categorize(ocr_result.get("raw_text", ""))

        # Clear old extracted data and re-insert
        db._execute("DELETE FROM extracted_data WHERE document_id = %s", (doc_id,))
        for field_name, field_value in ocr_result.get("fields", {}).items():
            db.insert_extracted_data(
                document_id=doc_id,
                field_name=field_name,
                field_value=str(field_value),
                confidence=ocr_result.get("confidence", 0.0),
            )
        db.insert_category(
            document_id=doc_id,
            category=category_result["category"],
            confidence=category_result["confidence"],
        )
        db.update_document_status(doc_id, "completed")
        return jsonify({"document_id": doc_id, "status": "completed"})
    except Exception as exc:
        logger.exception("Reprocess failed for %s", doc_id)
        return error_response(f"Reprocess failed: {exc}", 500)


# ── Manual category override ─────────────────────────────────────────────────
@app.route("/documents/<doc_id>/category", methods=["PUT"])
@login_required
def update_category(doc_id: str):
    """PUT /documents/<id>/category  Body: { "category": "Food" }"""
    data = request.get_json(force=True)
    category = (data.get("category") or "").strip()
    if not category:
        return error_response("category is required")
    try:
        doc = db.get_document_by_id(doc_id)
        if not doc:
            return error_response("Document not found", 404)
        db.insert_category(doc_id, category, confidence=1.0)
        return jsonify({"ok": True, "category": category})
    except Exception as exc:
        return error_response(f"Failed to update category: {exc}", 500)


# ── Manual field edits ────────────────────────────────────────────────────────
@app.route("/documents/<doc_id>/fields", methods=["PUT"])
@login_required
def update_fields(doc_id: str):
    """PUT /documents/<id>/fields  Body: { "vendor_name": "...", "total_amount": "...", "primary_date": "..." }"""
    data = request.get_json(force=True)
    allowed = {"vendor_name", "total_amount", "primary_date"}
    updates = {k: str(v).strip() for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return error_response("No valid fields provided")
    try:
        doc = db.get_document_by_id(doc_id)
        if not doc:
            return error_response("Document not found", 404)
        for field_name, field_value in updates.items():
            db.update_extracted_field(doc_id, field_name, field_value)
        return jsonify({"ok": True, "updated": list(updates.keys())})
    except Exception as exc:
        return error_response(f"Failed to update fields: {exc}", 500)


# ── Document notes ────────────────────────────────────────────────────────────
@app.route("/documents/<doc_id>/notes", methods=["GET"])
@login_required
def get_notes(doc_id: str):
    try:
        notes = db.get_document_notes(doc_id)
        return jsonify({"notes": notes})
    except Exception as exc:
        return error_response(f"Failed to load notes: {exc}", 500)


@app.route("/documents/<doc_id>/notes", methods=["POST"])
@login_required
def add_note(doc_id: str):
    data = request.get_json(force=True)
    note_text = (data.get("note_text") or "").strip()
    if not note_text:
        return error_response("note_text is required")
    if len(note_text) > 2000:
        return error_response("Note must be under 2000 characters")
    try:
        note = db.insert_document_note(doc_id, current_user_id(), note_text)
        return jsonify(note), 201
    except Exception as exc:
        return error_response(f"Failed to save note: {exc}", 500)


@app.route("/documents/<doc_id>/notes/<int:note_id>", methods=["DELETE"])
@login_required
def delete_note(doc_id: str, note_id: int):
    try:
        db.delete_document_note(note_id, current_user_id())
        return jsonify({"ok": True, "deleted": note_id})
    except Exception as exc:
        return error_response(f"Failed to delete note: {exc}", 500)


# ── Budget goals ──────────────────────────────────────────────────────────────
@app.route("/budget/goals", methods=["GET"])
@login_required
def get_budget_goals():
    try:
        goals = db.get_budget_goals(current_user_id())
        return jsonify({"goals": goals})
    except Exception as exc:
        return error_response(f"Failed to load budget goals: {exc}", 500)


@app.route("/budget/goals", methods=["POST"])
@login_required
def upsert_budget_goal():
    """POST /budget/goals  Body: { "category": "Food", "monthly_limit": 500 }"""
    data = request.get_json(force=True)
    category = (data.get("category") or "").strip()
    try:
        monthly_limit = float(data.get("monthly_limit", 0))
    except (TypeError, ValueError):
        return error_response("monthly_limit must be a number")
    if not category:
        return error_response("category is required")
    if monthly_limit <= 0:
        return error_response("monthly_limit must be positive")
    try:
        db.upsert_budget_goal(current_user_id(), category, monthly_limit)
        return jsonify({"ok": True, "category": category, "monthly_limit": monthly_limit})
    except Exception as exc:
        return error_response(f"Failed to save budget goal: {exc}", 500)


@app.route("/budget/goals/<category>", methods=["DELETE"])
@login_required
def delete_budget_goal(category: str):
    try:
        db.delete_budget_goal(current_user_id(), category)
        return jsonify({"ok": True, "deleted": category})
    except Exception as exc:
        return error_response(f"Failed to delete budget goal: {exc}", 500)


# ── Dashboard summary ─────────────────────────────────────────────────────────
@app.route("/dashboard/summary", methods=["GET"])
@login_required
def dashboard_summary():
    """
    GET /dashboard/summary?month=2024-03
    Returns aggregated financial statistics:
    - total_income, total_expenses
    - breakdown by category
    - monthly cash flow (last 12 months)
    - top vendors
    """
    month_filter = request.args.get("month")  # optional YYYY-MM

    try:
        summary = db.get_summary_stats(month=month_filter, user_id=current_user_id())
        return jsonify(summary)
    except Exception as exc:
        logger.exception("Failed to generate dashboard summary")
        return error_response(f"Database error: {exc}", 500)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info("Starting FinSight API on port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
