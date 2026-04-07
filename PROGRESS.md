# FinSight — What We Built (Progress Document)

## Project Overview

**FinSight** is an AI-powered financial document intelligence platform hosted on AWS.  
Users upload PDFs or images (bank statements, invoices, receipts), and the system automatically:
- Extracts text using OCR
- Pulls out financial fields (amounts, dates, vendors)
- Classifies the document into a spending category
- Stores structured data in a PostgreSQL database
- Displays insights in a live Metabase dashboard

---

## Architecture

```
  User
   │
   │  Upload PDF / Image
   ▼
Flask API (EC2 :5000)
   │
   ├──► S3 Bucket  (stores original file, AES-256 encrypted)
   │         │
   │         └──► Lambda trigger (ObjectCreated event)
   │                   │
   │◄──────────────────┘
   │  POST /process
   │
   ├──► Tesseract OCR  (extract raw text)
   ├──► Field Extraction (amounts, dates, vendors, invoice numbers)
   ├──► Categorizer  (Income / Food / Utilities / Rent / Travel / etc.)
   ├──► PostgreSQL RDS  ◄──── Metabase (:3000)  (live dashboard)
   └──► SES  (email notification on completion)
```

---

## What Was Built

### 1. Flask REST API — `backend/app.py`

The core of the application. A Flask server running on port 5000 with these endpoints:

| Method | Endpoint | What it does |
|--------|----------|--------------|
| `GET` | `/health` | Liveness probe for load balancers |
| `POST` | `/upload` | Accept a file upload, run OCR inline, store results |
| `POST` | `/process` | Called by Lambda — download from S3, run OCR, store results |
| `GET` | `/documents` | Paginated list of all documents (filterable by status) |
| `GET` | `/documents/<id>` | Full detail for one document including extracted fields and a presigned S3 URL |
| `GET` | `/dashboard/summary` | Aggregated stats: total income, total expenses, monthly cashflow, top vendors |

Key design decisions:
- Max upload size: 50 MB
- Accepted file types: PDF, PNG, JPG, JPEG, TIFF, BMP
- Each document gets a UUID on upload
- OCR runs synchronously on `/upload` (for demo simplicity) and asynchronously via Lambda on `/process`
- On OCR failure: returns HTTP 207 (partial success) — file is in S3 but OCR failed
- Gunicorn runs 4 worker processes in production

---

### 2. OCR Processor — `backend/ocr_processor.py`

Uses **Tesseract OCR** (via `pytesseract`) to extract text from uploaded files.

**What it does:**
- PDFs → converted to images at 300 DPI using `pdf2image` + Poppler, then OCR'd page by page
- Images → opened with Pillow and OCR'd directly
- Tesseract settings: `--psm 6 --oem 3` (uniform text block, best engine)

**Field extraction via regex:**

| Field | How it's extracted |
|-------|--------------------|
| `amounts` | Regex for `$1,234.56` style dollar amounts |
| `total_amount` | Largest amount found in the document |
| `dates` | Regex for MM/DD/YYYY, YYYY-MM-DD, "January 1 2024", etc. |
| `primary_date` | First date found |
| `invoice_number` | Looks for keywords like "Invoice #", "Receipt:", "TXN" |
| `vendor_name` | Looks for signal words ("Vendor:", "Billed by:", "From:"), falls back to first line |
| `tax_id` | Detects EIN/TIN/SSN patterns — **redacted** in output for privacy |
| `document_type` | Heuristic classification: bank_statement / invoice / receipt / tax_form / pay_stub / insurance |

**Confidence scoring:** ratio of alphanumeric characters to total characters in OCR output (0.0–1.0).

---

### 3. Document Categorizer — `backend/categorizer.py`

Keyword-based classifier that assigns each document to a spending category.

**Categories supported:**

| Category | Example documents |
|----------|------------------|
| Income | Payslips, direct deposit records, freelance invoices |
| Utilities | Electric bill, internet bill, phone bill |
| Food | Restaurant receipts, grocery store, DoorDash, Whole Foods |
| Medical | Hospital bills, pharmacy receipts, insurance claims |
| Insurance | Auto/home/life insurance premium notices |
| Tax | W-2, 1099, IRS correspondence, tax returns |
| Rent | Lease agreements, rent receipts, mortgage statements |
| Travel | Flight bookings, hotel receipts, Uber/Lyft |
| Shopping | Amazon, Walmart, Target, online orders |
| Subscription | Netflix, Spotify, Adobe, Microsoft 365 |

**How it works:**
- Two-tier keyword matching: weight-2 (strong signal) and weight-1 (supporting evidence)
- Confidence = best category score / total score across all categories, capped at 0.95
- Returns `"Other"` with confidence 0.0 if no keywords match

---

### 4. S3 Handler — `backend/s3_handler.py`

Wraps all AWS S3 operations using boto3. Credentials come from the EC2 **LabRole** instance profile — no keys stored anywhere.

**Capabilities:**
- `upload_file()` — upload from local path
- `upload_bytes()` — upload raw bytes (used when file is already in memory)
- `upload_fileobj()` — upload from a file-like object
- `download_file()` — download to a local path
- `download_bytes()` — download and return raw bytes
- `get_presigned_url()` — generate a 1-hour signed URL for the frontend to view the original file
- `object_exists()` — check if a key exists (HEAD request)
- `list_objects()` — paginated list of objects under a prefix
- `delete_object()` — delete one object

All uploads use **AES-256 server-side encryption**.

---

### 5. Database Handler — `backend/db_handler.py`

Thread-safe PostgreSQL interface backed by a **connection pool** (`psycopg2.ThreadedConnectionPool`, 1–10 connections).

Connects to AWS RDS PostgreSQL. All connection config (host, port, db, user, password) comes from environment variables. SSL is required.

**Tables managed:**

| Table | Purpose |
|-------|---------|
| `documents` | One row per uploaded file: id, filename, s3_key, upload_date, status |
| `extracted_data` | Key-value pairs of OCR fields per document (e.g. `total_amount = 1234.56`) |
| `categories` | Category classification result per document |
| `summary_cache` | Pre-computed monthly rollups for fast dashboard queries |

**Key queries:**
- `insert_document()` — create a new document record with status `processing`
- `update_document_status()` — flip to `completed` or `failed`
- `get_all_documents()` — paginated list with LEFT JOIN to primary category
- `insert_extracted_data()` — store one field extracted by OCR
- `insert_category()` — upsert the category result
- `get_summary_stats()` — aggregate income, expenses, monthly cashflow, top vendors (supports optional `YYYY-MM` month filter)

Connection errors trigger one automatic retry with pool recreation (handles AWS Academy session restarts).

---

### 6. Lambda Trigger — `lambda/trigger_ocr.py`

An AWS Lambda function triggered by **S3 ObjectCreated** events on the `finsight-documents` bucket.

**What it does:**
1. Parses the S3 event (bucket name, object key, file size, event time)
2. Skips non-document files (only processes `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp`)
3. POSTs to the EC2 backend's `/process` endpoint with the S3 key
4. Logs success/failure to **CloudWatch** automatically

Uses only Python standard library (`urllib`) — no external dependencies, so no Lambda layer is needed.

Configurable via Lambda environment variables:
- `EC2_BACKEND_URL` — the public IP of the EC2 instance
- `REQUEST_TIMEOUT` — how long to wait for OCR to complete (default 60s)

Includes a local test harness at the bottom to simulate S3 events during development.

---

### 7. RDS PostgreSQL Schema — `infrastructure/rds_schema.sql`

Full database schema for the RDS instance.

**Tables:**
- `documents` — core document records with status check constraint (`processing | completed | failed`)
- `extracted_data` — flexible key-value OCR fields with confidence scores
- `categories` — one category per document (upserted on re-processing)
- `summary_cache` — pre-computed monthly rollups with a generated `net` column

**Indexes:**
- `idx_documents_status`, `idx_documents_upload_date` — for list/filter queries
- `idx_extracted_field_name`, partial index on `total_amount` — for analytics queries

**View: `dashboard_overview`**  
A convenience view that Metabase queries directly. For each completed document it returns:
- filename, s3_key, upload_date, status
- category and category_confidence
- amount, document_date, vendor (pulled from extracted_data)

**Auto-timestamp trigger:** `updated_at` column on `documents` automatically updates on every row change.

**Seed data** (commented out): 3 sample documents (bank statement, electric bill, grocery receipt) for local dev/demo.

---

### 8. EC2 Bootstrap Script — `infrastructure/setup_ec2.sh`

A one-shot bash script to provision a fresh Ubuntu 22.04 EC2 instance (t3.large). Run once after SSH.

**What it installs (8 steps):**
1. System update (`apt-get upgrade`)
2. Core tools (curl, wget, git, htop, etc.)
3. **Python 3.11** (via deadsnakes PPA)
4. **Tesseract OCR** + English language pack + **Poppler** (for PDF conversion) + `libpq-dev`
5. **AWS CLI v2**
6. **Docker** (CE, with buildx and compose plugin)
7. **Docker Compose** standalone v2.27.0
8. Clone/start the FinSight app via `docker-compose up -d --build`

Also installs the **CloudWatch Agent** and configures it to report `mem_used_percent` and `disk_used_percent` every 60 seconds.

---

### 9. Security Groups — `infrastructure/security_groups.md`

Defines two AWS Security Groups:

**`finsight-ec2-sg` (EC2):**
- Port 22 (SSH) → your IP only
- Port 5000 (Flask API) → 0.0.0.0/0 (public demo access)
- Port 3000 (Metabase) → 0.0.0.0/0

**`finsight-rds-sg` (RDS):**
- Port 5432 (PostgreSQL) → Source: `finsight-ec2-sg` only  
  (RDS is never publicly accessible — only the EC2 instance can reach it)

---

### 10. Metabase Dashboard — `metabase/setup_instructions.md`

Instructions to connect Metabase (running on EC2 port 3000) to RDS and build 4 dashboard charts:

| Chart | Type | SQL query |
|-------|------|-----------|
| Total Income vs Expenses | Bar chart | GROUP BY income/expense type |
| Spending by Category | Pie chart | GROUP BY category, excluding Income |
| Monthly Cash Flow | Line chart | Monthly income and expense series |
| Recent Documents | Table | Last 20 uploads from `dashboard_overview` view |

Dashboard auto-refreshes every 1 minute (for live demo effect).

---

### 11. Docker Setup — `docker-compose.yml` + `backend/Dockerfile`

**Dockerfile (backend):**
- Base: `python:3.11-slim`
- Installs: Tesseract, Poppler, libpq-dev, curl, gcc
- Runs: `gunicorn --bind 0.0.0.0:5000 --workers 4 --timeout 120`
- Healthcheck: `curl -f http://localhost:5000/health` every 30s

**docker-compose.yml:**
- `backend` service: Flask API on port 5000, mounts `/tmp/finsight` for temp OCR files
- `metabase` service: Metabase on port 3000, H2 file DB persisted via named volume
- Both on a shared `finsight-net` bridge network
- Log rotation: 50 MB max, 5 files (backend), 50 MB / 3 files (Metabase)

---

### 12. Environment Config — `.env.example`

Template for all required environment variables:

```
AWS_REGION          us-east-1
S3_BUCKET_NAME      finsight-documents-<your-name>
RDS_HOST            <your-rds-endpoint>.rds.amazonaws.com
RDS_PORT            5432
RDS_DB              finsight
RDS_USER            admin
RDS_PASSWORD        <strong password>
SES_SENDER_EMAIL    <verified sender>
NOTIFICATION_EMAIL  <your email>
FLASK_ENV           production
FLASK_PORT          5000
EC2_BACKEND_URL     http://<ec2-public-ip>:5000
```

AWS credentials are **not** stored here — the EC2 LabRole instance profile provides them automatically via boto3.

---

## Python Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| flask | 3.0.3 | Web framework |
| boto3 / botocore | 1.34.131 | AWS SDK (S3, SES) |
| psycopg2-binary | 2.9.9 | PostgreSQL driver |
| pytesseract | 0.3.10 | Tesseract OCR wrapper |
| Pillow | 10.3.0 | Image processing |
| pdf2image | 1.17.0 | PDF → image conversion |
| python-dotenv | 1.0.1 | Load `.env` files |
| requests | 2.32.3 | HTTP client |
| gunicorn | 22.0.0 | Production WSGI server |

---

## What Still Needs To Be Done (Not Yet Built)

- SES email notification code (referenced in README and `.env.example` but not implemented)
- Frontend / UI (currently API-only)
- Authentication / API keys
- Deploy to actual AWS (EC2, RDS, S3, Lambda all need to be provisioned)
- Fill in `.env` with real AWS values
- Run `rds_schema.sql` against the RDS instance
- Deploy Lambda function to AWS
