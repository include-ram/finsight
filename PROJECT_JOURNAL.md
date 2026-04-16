# FinSight — Full Project Journal

This document records everything built, every decision made, every bug encountered, and every fix applied across the entire FinSight project — from the initial commit through live deployment on AWS Academy.

---

## What FinSight Is

**FinSight** is an AI-powered financial document intelligence platform.  
Users upload financial PDFs (salary slips, expense reports, utility bills, receipts) and the system automatically:

- Extracts text using Tesseract OCR
- Pulls out structured fields (total amount, dates, vendor name, document type)
- Classifies the document into a spending category (Income, Food, Utilities, Travel, etc.)
- Stores all structured data in PostgreSQL
- Displays financial insights through a live dashboard with charts

Live at: `http://<ec2-public-ip>` (see AWS Academy session for current IP)  
GitHub: `https://github.com/include-ram/finsight`

---

## Architecture

```
Browser (port 80)
      │
      ▼
  nginx (frontend container)
      │
      ├── /             → serves index.html (Single-Page App)
      └── /upload, /documents, /dashboard, /auth, ...
                │
                ▼
        Flask API (backend container, port 5000)
                │
                ├── S3 Bucket  (stores original uploaded files)
                ├── Tesseract OCR  (extracts text from PDFs/images)
                ├── Categorizer  (keyword classifier)
                └── PostgreSQL (db container, port 5432)
```

All three services run as Docker containers on a single EC2 instance, connected via a Docker bridge network (`finsight-net`).

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| API | Python / Flask + Gunicorn | 4 workers, 120s timeout |
| OCR | Tesseract + pdf2image + Pillow | PDFs converted to 300 DPI images first |
| Database | PostgreSQL 15 (Docker container) | Named volume for persistence |
| File Storage | AWS S3 | AES-256 server-side encryption |
| Frontend | Vanilla HTML/CSS/JS SPA | Served by nginx |
| Web Server | nginx:alpine | Reverse proxy + static file server |
| Containerisation | Docker + Docker Compose | |
| Cloud | AWS Academy (EC2 t3.micro, S3) | |

---

## Project Structure

```
finsight/
├── backend/
│   ├── app.py              Flask REST API — all endpoints
│   ├── ocr_processor.py    Tesseract OCR + field extraction + total detection
│   ├── categorizer.py      Keyword-based transaction classifier
│   ├── db_handler.py       PostgreSQL connection pool + all queries
│   ├── s3_handler.py       S3 upload / download / presigned URLs
│   ├── requirements.txt    Python dependencies
│   └── Dockerfile
├── frontend/
│   ├── index.html          Single-Page App (upload, documents, dashboard)
│   └── nginx.conf          nginx reverse proxy config
├── infrastructure/
│   ├── init.sql            PostgreSQL schema (auto-applied on first container start)
│   ├── rds_schema.sql      Original RDS schema (kept for reference)
│   └── setup_ec2.sh        EC2 bootstrap script
├── lambda/
│   └── trigger_ocr.py      S3 event → EC2 backend bridge (not actively used)
├── docker-compose.yml      Defines db + backend + frontend services
├── .env.example            Template for environment variables
└── PROJECT_JOURNAL.md      This file
```

---

## Part 1 — Initial Backend Build

### What was built first

The project started as an API-only backend with no frontend. The core components:

**`backend/app.py`** — Flask API with these endpoints:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Liveness probe |
| POST | `/upload` | Accept file, run OCR, store in S3 + DB |
| POST | `/process` | Called by Lambda — download from S3, run OCR |
| GET | `/documents` | Paginated list of documents |
| GET | `/documents/<id>` | Full document detail + presigned S3 URL |
| GET | `/dashboard/summary` | Aggregated financial stats |

**`backend/ocr_processor.py`** — Tesseract OCR wrapper:
- PDFs are converted page-by-page to 300 DPI PNG images using `pdf2image` + Poppler, then OCR'd
- Images are OCR'd directly via Pillow
- Tesseract settings: `--psm 6 --oem 3` (uniform text block, best engine)
- Regex patterns extract: amounts, dates, vendor names, invoice numbers, document type
- Confidence score = ratio of alphanumeric chars to total chars in OCR output

**`backend/categorizer.py`** — keyword-based classifier:
- Two-tier keyword matching (weight-2 strong signals + weight-1 supporting evidence)
- Categories: Income, Utilities, Food, Medical, Insurance, Tax, Rent, Travel, Shopping, Subscription
- Confidence = top score / total score, capped at 0.95

**`backend/db_handler.py`** — PostgreSQL via psycopg2:
- ThreadedConnectionPool (1–10 connections)
- Automatic reconnect with one retry on OperationalError
- All queries use parameterised inputs (no SQL injection)

**`backend/s3_handler.py`** — S3 operations via boto3:
- `upload_bytes()`, `download_file()`, `get_presigned_url()`, `delete_object()`
- All uploads use AES-256 server-side encryption

### Database schema (`infrastructure/init.sql`)

Four tables, auto-applied when the PostgreSQL container first starts:

```sql
users          — id (UUID), username, password_hash, created_at
documents      — id (UUID), filename, s3_key, upload_date, status, user_id (FK)
extracted_data — document_id (FK), field_name, field_value, confidence
categories     — document_id (FK, UNIQUE), category, confidence
```

---

## Part 2 — Frontend UI

### The SPA (`frontend/index.html`)

A single HTML file serving as a complete single-page application with three tabs:

**Upload tab:**
- Drag-and-drop upload zone
- File input accepts: PDF, PNG, JPG, JPEG, TIFF, BMP up to 50 MB
- Initially single-file; later upgraded to **multi-file** (uploads sequentially, one row per file with individual pass/fail status)

**Documents tab:**
- Paginated table of all uploaded documents
- Sortable columns (click header to sort)
- Status filter dropdown (All / Completed / Processing / Failed)
- Checkbox-based bulk delete
- CSV export button
- **↻ Reprocess All** button to re-run OCR on existing documents
- Click any row to open a detail modal with extracted fields + presigned S3 link

**Dashboard tab:**
- Six stat cards: Total Income, Total Expenses, Net Cash Flow, Savings Rate, Avg Monthly Spend, Documents
- Income vs Expenses ratio bar (proportional colour split)
- Quick Insights (auto-generated bullets: top category, biggest vendor, MoM change, savings health, best month)
- Recent Activity feed (last 6 documents with icon + amount)
- Four Chart.js charts: Spending by Category (doughnut), Monthly Cash Flow (bar), Net Cash Flow Trend (line), Top Vendors by Spend (horizontal bar)
- Category Breakdown table with mini bars and % of total

### nginx reverse proxy (`frontend/nginx.conf`)

```nginx
location / {
    try_files $uri $uri/ /index.html;   # SPA routing
}

location ~ ^/(upload|documents|process|dashboard|health|auth|export) {
    proxy_pass http://backend:5000;      # forward API calls to Flask
    client_max_body_size 50m;
    proxy_read_timeout 120s;
}
```

---

## Part 3 — Multi-User Authentication

### Why it was added

Multiple people need to share the URL but each see only their own documents and financial data.

### What was built

**`backend/app.py` additions:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/auth/register` | Create account (username min 3 chars, password min 6) |
| POST | `/auth/login` | Authenticate, set server-side session cookie |
| POST | `/auth/logout` | Clear session |
| GET | `/auth/me` | Return current user info |

- Passwords hashed with `werkzeug.security.generate_password_hash` (pbkdf2:sha256)
- `login_required` decorator wraps all data endpoints
- All document queries scoped by `user_id` from session

**Frontend additions:**
- Auth overlay (modal blocking the entire UI) shown on page load if not logged in
- Login / Register tab switcher
- Username badge + logout button in header
- All API calls pass `credentials: 'include'` for cookie-based sessions

---

## Part 4 — AWS Deployment

### EC2 Setup (each new AWS Academy session)

AWS Academy resets everything when a session ends. The deployment process:

1. **New EC2 instance** — Amazon Linux 2023, t3.micro (1 GB RAM)
2. **Install Docker:**
   ```bash
   sudo dnf install -y docker git
   sudo systemctl start docker && sudo systemctl enable docker
   sudo usermod -aG docker ec2-user
   ```
3. **Install Docker Compose v5 + buildx plugin:**
   ```bash
   sudo curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
     -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose
   sudo mkdir -p /usr/local/lib/docker/cli-plugins
   sudo curl -sSL https://github.com/docker/buildx/releases/download/v0.17.0/buildx-v0.17.0.linux-amd64 \
     -o /usr/local/lib/docker/cli-plugins/docker-buildx && sudo chmod +x ...
   ```
4. **Clone the repo:**
   ```bash
   git clone https://github.com/include-ram/finsight.git ~/finsight
   ```
5. **Create `.env`** with AWS session credentials, S3 bucket name, Flask secret
6. **Open port 80** in the EC2 Security Group (only port 22 is open by default)
7. **Recreate S3 bucket** (AWS Academy deletes it on session end):
   ```bash
   aws s3api create-bucket --bucket finsight-docs-648524219490 --region us-east-1
   ```
8. **Start containers:**
   ```bash
   cd ~/finsight && docker-compose up -d --build
   ```

### `.env` file on EC2 (update each session)

```
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=<from AWS Academy Details panel>
AWS_SECRET_ACCESS_KEY=<from AWS Academy Details panel>
AWS_SESSION_TOKEN=<from AWS Academy Details panel>
S3_BUCKET_NAME=finsight-docs-648524219490
RDS_HOST=db
RDS_PORT=5432
RDS_DB=finsight
RDS_USER=finsight_admin
RDS_PASSWORD=FinsightDB2024!
DB_SSLMODE=disable
FLASK_ENV=production
FLASK_PORT=5000
FLASK_SECRET_KEY=finsight-prod-secret-2024
EC2_BACKEND_URL=http://<current-ec2-ip>:5000
```

### Why local PostgreSQL instead of RDS

AWS Academy **terminates the RDS instance** when the session ends. Recreating it requires provisioning a subnet group (needs 2+ AZs), takes ~5 minutes, and the schema must be reapplied every time.

The solution: run **PostgreSQL 15 as a Docker container** on the same EC2. The database lives in a named Docker volume (`postgres-data`) which survives container restarts. The `infrastructure/init.sql` schema is mounted into `/docker-entrypoint-initdb.d/` and runs automatically on first container start.

Trade-off: if the EC2 instance itself is terminated, the volume is lost. But AWS Academy preserves the EC2 instance between sessions — only the public IP and session credentials change.

---

## Part 5 — Bugs Found and Fixed

### Bug 1: S3 Access Denied on upload

**Symptom:** `S3 upload failed: AccessDenied`  
**Cause:** AWS Academy's EC2InstanceRole (LabRole) doesn't grant S3 permissions by default.  
**Fix:** Added `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` to `.env`. boto3 picks these up automatically over the instance profile.

### Bug 2: nginx returning HTML instead of JSON (502 / "Unexpected token '<'")

**Symptom:** API calls returned HTML error pages instead of JSON.  
**Cause 1:** `/auth` prefix not included in the nginx `location` regex — register/login returned 404 with nginx's HTML error page.  
**Fix 1:** Added `auth` to the nginx proxy regex.  
**Cause 2:** Backend container restarted, nginx cached the stale backend container IP.  
**Fix 2:** Restart the frontend container: `docker restart finsight-frontend`.

### Bug 3: Dashboard charts showing nothing

**Symptom:** Spending by Category, Monthly Cash Flow, Top Vendors all blank.  
**Cause:** Frontend JS was reading `data.category_breakdown` and `data.monthly_cash_flow`, but the backend returns `data.by_category` and `data.monthly_cashflow`.  
**Fix:** Updated frontend variable names to match backend response keys.

### Bug 4: Charts not rendering when switching to Dashboard tab

**Symptom:** Charts blank unless you resize the window.  
**Cause:** Chart.js measures the canvas element's dimensions on creation. When the Dashboard tab is hidden (`display:none`), canvas has 0×0 dimensions.  
**Fix:** Added `setTimeout(loadDashboard, 50)` when switching to the dashboard tab, and replaced canvas elements before redrawing (`destroyChart` now creates a fresh `<canvas>` element).

### Bug 5: Dashboard SQL error — "missing FROM-clause entry for table d"

**Symptom:** `/dashboard/summary` returned 500.  
**Cause:** The status count query used `d.user_id` but the query had no table alias `d` — it was a plain `SELECT ... FROM documents`.  
**Fix:** Changed `d.user_id` to `user_id` in the unaliased query.

### Bug 6: Net Cash Flow Trend showed only one data point

**Symptom:** The trend chart showed only one month even when documents covered multiple months.  
**Cause:** The `monthly_cashflow` SQL grouped by `d.upload_date`. All documents uploaded in the same session get the same upload month, even if they cover different periods (e.g., March and April expense reports uploaded in April).  
**Fix:** Changed grouping to use `primary_date` (the OCR-extracted document period date) with `TO_DATE(field_value, 'Month DD, YYYY')`. Falls back to `upload_date` when `primary_date` is absent or unparseable. Same fix applied to the month filter.

### Bug 7: Top Vendors showing document count instead of spend amount

**Symptom:** Top Vendors chart had `$` labels on the X axis but showed values like 1, 2, 3 (counts).  
**Cause:** Backend query returned `COUNT(*) AS doc_count`. Frontend used `v.count` but axis said `$`.  
**Fix:** Backend now JOINs to `extracted_data` for `total_amount` and returns `SUM(total_amount) AS total_spend` per vendor. Frontend uses `v.total_spend`.

### Bug 8: Month filter showing nothing for January

**Symptom:** Filtering dashboard by `2026-01` showed no data even though January documents existed.  
**Cause:** The `month_filter_sql` was `AND TO_CHAR(d.upload_date, 'YYYY-MM') = %s`. Since all files are uploaded in the current session (April 2026), no document had `upload_date` in January.  
**Fix:** Month filter now uses the same `primary_date` correlated subquery as the monthly cashflow grouping, falling back to `upload_date`.

### Bug 9: `total_amount` picking the wrong number

**Symptom:** Dashboard totals were wrong — too low (picking a line item instead of the total).  
**Cause:** OCR processor used `max(parsed_amounts)` as `total_amount`. For an expense report listing 6 line items (e.g. $23.98, $112.00, $22.00, $15.99, $45.00, $68.50), it picked $112.00 (the largest line) instead of $287.47 (the sum).  
**Root cause details:** The documents had `CATEGORY TOTAL 100%` — a total label with a percentage, not a dollar amount. No explicit numeric total existed in the document.  
**Fix:** Three-stage logic:
  1. Scan for explicit labels with a following dollar value (Total Due, Net Pay, Amount Payable, etc.)
  2. If `TOTAL` appears with only a `%` → itemised list, sum the unique line-item amounts
  3. If every unique amount appears the same number of times → OCR read the same section twice (layout duplication), sum unique amounts
  4. Fallback: largest number found

### Bug 10: `primary_date` picking "February 1, 2026" instead of "January 1, 2026"

**Symptom:** Documents labelled "January 2026" were grouped under February in the dashboard trend.  
**Cause:** OCR read "January" as "J anuary" (inserted a space after the first letter — common Tesseract artefact with certain PDF fonts). The date regex `(?:January|...)` didn't match "J anuary".  
**Fix:** Added `_normalize_text()` to preprocess OCR output before field extraction. Replaces `"J anuary"`, `"F ebruary"`, etc. with the correct month names.

---

## Part 6 — Features Added Over Time

### Multi-file upload

The file input was changed to `multiple`. Drop zone and `<input>` both call `uploadFiles(fileList)`.  
Files upload **sequentially** (not in parallel — avoids overloading the 1 GB EC2).  
Each file gets its own status row showing pass/fail. Progress bar advances per-file across the full batch.

### Sortable documents table

Column headers in the Documents table are clickable. Client-side sort toggles asc/desc per column. Sorted against the current page cache (`docsCache`).

### Bulk delete

Checkbox column added. "Select all" checkbox in header. A bulk action bar slides in when rows are selected showing the count and a Delete button. Uses sequential `DELETE /documents/<id>` calls.

### CSV export

`GET /documents/export` streams a CSV with columns: filename, upload_date, status, category, vendor, amount, document_date.  
Frontend triggers download via a hidden `<a>` element with a blob URL.

### Reprocess All

`POST /documents/<id>/reprocess` re-runs OCR + categorisation on an already-uploaded document:
1. Downloads the original file from S3
2. Runs OCR with the latest `ocr_processor.py` logic
3. Deletes old `extracted_data` rows for that document
4. Inserts fresh extracted fields
5. Upserts the category

The "↻ Reprocess All" button in the Documents tab iterates all documents, calling this endpoint for each, showing a live counter `(3/12…)`.

### Enhanced dashboard

Additional stat cards added beyond the original four:
- **Savings Rate** — (income − expenses) / income × 100
- **Avg Monthly Spend** — total expenses ÷ number of months with data
- **Month-over-month arrows** — ▲/▼ % change vs previous month on Income and Expenses cards

Additional dashboard elements:
- **Income vs Expenses ratio bar** — horizontal coloured bar showing the income/expense split
- **Quick Insights** — auto-generated text bullets (top category, biggest vendor, MoM change, savings health, best month)
- **Recent Activity feed** — last 6 documents with category emoji icon, amount, date, clickable to open detail modal
- **Category Breakdown table** — each category with a mini colour bar and % of total spend

---

## Part 7 — Key Decisions and Trade-offs

| Decision | Reason |
|----------|--------|
| Local PostgreSQL in Docker instead of RDS | RDS gets deleted every AWS Academy session; local volume survives instance restarts |
| Metabase disabled | Caused OOM crashes on the 1 GB t3.micro (Java heap alone was 512 MB); replaced with Chart.js dashboard |
| Sequential multi-file upload (not parallel) | Avoids overloading the OCR pipeline on a 1 GB machine |
| OCR runs synchronously on `/upload` | Simpler for demo — no queue needed; Lambda trigger path still exists for async use |
| Single HTML file SPA | No build step, no npm, deploys via nginx volume mount — works well for a focused demo |
| Group dashboard by `primary_date` not `upload_date` | All files are uploaded in the same session; `primary_date` gives accurate monthly trends |

---

## Part 8 — AWS Resources Used

| Resource | Name / ID |
|----------|-----------|
| EC2 instance | `i-019124f982033bb32` (IP changes each session) |
| S3 bucket | `finsight-docs-648524219490` |
| AWS Account | `648524219490` |
| Region | `us-east-1` |

> **Note:** AWS Academy resets S3 and session credentials every ~3 hours. The EC2 instance persists between sessions but gets a new public IP. After each new session:
> 1. Get new credentials from the AWS Academy "AWS Details" panel
> 2. Update `~/finsight/.env` on EC2 with new `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`
> 3. Recreate the S3 bucket: `aws s3api create-bucket --bucket finsight-docs-648524219490 --region us-east-1`
> 4. Restart backend: `cd ~/finsight && docker-compose up -d backend`

---

## Part 9 — API Reference (Final State)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | No | Liveness check |
| POST | `/auth/register` | No | Create account |
| POST | `/auth/login` | No | Login |
| POST | `/auth/logout` | No | Logout |
| GET | `/auth/me` | Yes | Current user |
| POST | `/upload` | Yes | Upload one or more files (call once per file) |
| GET | `/documents` | Yes | List documents (supports `?status=&limit=&offset=`) |
| GET | `/documents/<id>` | Yes | Document detail + extracted fields + presigned S3 URL |
| DELETE | `/documents/<id>` | Yes | Delete document from S3 and DB |
| POST | `/documents/<id>/reprocess` | Yes | Re-run OCR on an existing document |
| GET | `/documents/export` | Yes | Download all documents as CSV |
| GET | `/dashboard/summary` | Yes | Aggregated stats (supports `?month=YYYY-MM`) |

---

## Python Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| flask | 3.0.3 | Web framework |
| flask-cors | 4.0.1 | CORS headers (needed for dev; nginx handles prod) |
| boto3 / botocore | 1.34.131 | AWS SDK (S3) |
| psycopg2-binary | 2.9.9 | PostgreSQL driver |
| pytesseract | 0.3.10 | Tesseract OCR wrapper |
| Pillow | 10.3.0 | Image processing |
| pdf2image | 1.17.0 | PDF → image conversion |
| python-dotenv | 1.0.1 | Load `.env` files |
| gunicorn | 22.0.0 | Production WSGI server |
| werkzeug | (flask dep) | Password hashing |
