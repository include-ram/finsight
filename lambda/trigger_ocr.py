"""
FinSight - Lambda Trigger
Triggered by S3 ObjectCreated events.
Calls the EC2 backend /process endpoint to run OCR on the new file.
Logs all activity to CloudWatch automatically (Lambda does this by default).

Deploy this as a Lambda function in us-east-1.
Attach the LabRole execution role.
Add an S3 trigger for your finsight-documents bucket on ObjectCreated events.
"""

import json
import os
import logging
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

# Lambda uses Python's built-in logging — no third-party deps
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# EC2 backend URL — set this as a Lambda environment variable
EC2_BACKEND_URL = os.environ.get("EC2_BACKEND_URL", "http://localhost:5000")
PROCESS_ENDPOINT = f"{EC2_BACKEND_URL}/process"

# Timeout for the HTTP request to EC2 (seconds)
# OCR can take a while — give it 60 seconds
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 60))


def lambda_handler(event: dict, context) -> dict:
    """
    Main Lambda entry point.
    event: S3 event notification (one or more records).
    context: Lambda runtime context (used for request ID in logs).
    """
    request_id = context.aws_request_id if context else "local"
    logger.info("FinSight OCR trigger invoked | request_id=%s | records=%d",
                request_id, len(event.get("Records", [])))

    results = []

    for record in event.get("Records", []):
        result = _process_record(record, request_id)
        results.append(result)

    # Summary log for CloudWatch Insights queries
    success_count = sum(1 for r in results if r["status"] == "success")
    failure_count = len(results) - success_count
    logger.info(
        "Processing complete | success=%d | failure=%d | request_id=%s",
        success_count, failure_count, request_id,
    )

    return {
        "statusCode": 200 if failure_count == 0 else 207,
        "body": json.dumps({
            "processed": len(results),
            "success": success_count,
            "failure": failure_count,
            "results": results,
        }),
    }


def _process_record(record: dict, request_id: str) -> dict:
    """Process a single S3 event record."""
    # ── Parse S3 event ────────────────────────────────────────────────────────
    try:
        bucket = record["s3"]["bucket"]["name"]
        # S3 keys in event notifications are URL-encoded
        raw_key = record["s3"]["object"]["key"]
        s3_key = urllib.parse.unquote_plus(raw_key)
        file_size = record["s3"]["object"].get("size", 0)
        event_time = record.get("eventTime", datetime.utcnow().isoformat())
    except KeyError as exc:
        logger.error("Malformed S3 event record: missing key %s | record=%s", exc, record)
        return {"status": "error", "error": f"Malformed event: {exc}"}

    logger.info(
        "New S3 object | bucket=%s | key=%s | size=%d bytes | event_time=%s",
        bucket, s3_key, file_size, event_time,
    )

    # ── Skip non-document files ───────────────────────────────────────────────
    allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
    file_ext = os.path.splitext(s3_key)[1].lower()
    if file_ext not in allowed_extensions:
        logger.info("Skipping non-document file: %s", s3_key)
        return {"status": "skipped", "reason": "not a document file", "s3_key": s3_key}

    # ── Call EC2 backend ──────────────────────────────────────────────────────
    payload = json.dumps({
        "s3_key": s3_key,
        "bucket": bucket,
        "file_size": file_size,
        "event_time": event_time,
        "lambda_request_id": request_id,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            PROCESS_ENDPOINT,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Lambda-Request-Id": request_id,
            },
            method="POST",
        )

        logger.info("Calling EC2 backend: POST %s | s3_key=%s", PROCESS_ENDPOINT, s3_key)

        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            status_code = response.status
            response_body = response.read().decode("utf-8")

        response_data = json.loads(response_body)
        logger.info(
            "EC2 response | status=%d | document_id=%s | s3_key=%s",
            status_code,
            response_data.get("document_id", "unknown"),
            s3_key,
        )

        return {
            "status": "success",
            "s3_key": s3_key,
            "http_status": status_code,
            "document_id": response_data.get("document_id"),
            "ocr_status": response_data.get("status"),
        }

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        logger.error(
            "EC2 HTTP error | status=%d | s3_key=%s | body=%s",
            exc.code, s3_key, error_body,
        )
        return {
            "status": "error",
            "s3_key": s3_key,
            "error": f"HTTP {exc.code}: {error_body[:200]}",
        }

    except urllib.error.URLError as exc:
        logger.error(
            "EC2 connection error | url=%s | s3_key=%s | reason=%s",
            PROCESS_ENDPOINT, s3_key, exc.reason,
        )
        return {
            "status": "error",
            "s3_key": s3_key,
            "error": f"Cannot reach EC2 backend: {exc.reason}",
        }

    except TimeoutError:
        logger.error("EC2 request timed out after %ds | s3_key=%s", REQUEST_TIMEOUT, s3_key)
        return {
            "status": "error",
            "s3_key": s3_key,
            "error": f"Timeout after {REQUEST_TIMEOUT}s",
        }

    except Exception as exc:
        logger.exception("Unexpected error processing %s", s3_key)
        return {
            "status": "error",
            "s3_key": s3_key,
            "error": str(exc),
        }


# ── Local testing ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Simulate an S3 event for local testing
    test_event = {
        "Records": [
            {
                "eventTime": "2024-03-01T12:00:00.000Z",
                "s3": {
                    "bucket": {"name": "finsight-documents-test"},
                    "object": {
                        "key": "uploads/test-uuid/bank_statement.pdf",
                        "size": 102400,
                    },
                },
            }
        ]
    }

    class MockContext:
        aws_request_id = "local-test-001"

    result = lambda_handler(test_event, MockContext())
    print(json.dumps(result, indent=2))
