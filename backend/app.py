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
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from s3_handler import S3Handler
from db_handler import DBHandler
from ocr_processor import OCRProcessor
from categorizer import Categorizer

# ── Bootstrap ────────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # allow requests from frontend (nginx or local dev)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload cap

# Allowed file extensions
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp"}

# Singleton service objects (created once at startup)
s3 = S3Handler()
db = DBHandler()
ocr = OCRProcessor()
cat = Categorizer()


# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def error_response(message: str, status: int = 400):
    return jsonify({"error": message}), status


# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    """Quick liveness probe for load balancers / monitoring."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# ── Upload endpoint ───────────────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
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

    # ── Step 4: Return completed record ─────────────────────────────────────
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
def list_documents():
    """
    GET /documents?status=completed&limit=50&offset=0
    Returns paginated list of all documents with their categories.
    """
    status = request.args.get("status")          # optional filter
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    try:
        docs = db.get_all_documents(status=status, limit=limit, offset=offset)
        total = db.count_documents(status=status)
        return jsonify({"total": total, "limit": limit, "offset": offset, "documents": docs})
    except Exception as exc:
        logger.exception("Failed to list documents")
        return error_response(f"Database error: {exc}", 500)


# ── Single document detail ────────────────────────────────────────────────────
@app.route("/documents/<doc_id>", methods=["GET"])
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


# ── Delete document ──────────────────────────────────────────────────────────
@app.route("/documents/<doc_id>", methods=["DELETE"])
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


# ── Dashboard summary ─────────────────────────────────────────────────────────
@app.route("/dashboard/summary", methods=["GET"])
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
        summary = db.get_summary_stats(month=month_filter)
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
