# FinSight

FinSight is an AI-powered financial document intelligence platform. Upload bank statements or invoices as PDFs, and FinSight automatically extracts key fields (amounts, dates, vendors), categorizes transactions, stores structured data, and surfaces insights through a live dashboard.

---

## What it does

- **OCR Processing** — Extracts text from PDF documents using Tesseract OCR with pdf2image fallback for scanned/image-only files
- **Field Extraction** — Parses amounts, dates, and vendor names from raw OCR output using regex pipelines
- **Auto-categorization** — Classifies transactions into categories (Food, Travel, Utilities, etc.) using keyword-based ML
- **REST API** — Flask backend exposes endpoints to upload documents, list processed records, and fetch extracted data
- **Dashboard** — Metabase connects to the database and provides live charts (spend by category, income vs. expenses over time)
- **Email Alerts** — Sends email notifications on document processing completion via SES

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | Python / Flask |
| OCR | Tesseract, pdf2image, Pillow |
| Database | PostgreSQL (RDS) |
| File Storage | S3 |
| Notifications | AWS SES |
| Event Trigger | AWS Lambda (S3 → EC2) |
| Dashboard | Metabase |
| Containerization | Docker / Docker Compose |

---

## Architecture

```
  User
   │
   │  Upload PDF
   ▼
Flask API (:5000)
   │
   ├──► S3 (stores original PDF)
   │         │
   │         └──► Lambda trigger
   │                   │
   │◄──────────────────┘
   │  /process-document
   │
   ├──► Tesseract OCR
   ├──► Field extraction (amounts, dates, vendors)
   ├──► Categorizer
   ├──► PostgreSQL (RDS) ◄──── Metabase (:3000)
   └──► SES (email notification)
```

---

## Project Structure

```
finsight/
├── backend/
│   ├── app.py              REST API (upload, list, detail, dashboard endpoints)
│   ├── ocr_processor.py    Tesseract OCR + regex field extraction
│   ├── categorizer.py      Keyword-based transaction classification
│   ├── db_handler.py       PostgreSQL connection pool + queries
│   ├── s3_handler.py       S3 upload/download helpers
│   ├── requirements.txt    Python dependencies
│   └── Dockerfile
├── lambda/
│   └── trigger_ocr.py      S3 event trigger → EC2 backend
├── infrastructure/
│   ├── setup_ec2.sh        EC2 bootstrap script
│   ├── security_groups.md  Network security group setup
│   └── rds_schema.sql      PostgreSQL schema + views
├── metabase/
│   └── setup_instructions.md
├── docker-compose.yml
└── .env.example
```

---

## Running Locally

```bash
# 1. Clone the repo
git clone https://github.com/include-ram/finsight.git
cd finsight

# 2. Set up environment variables
cp .env.example .env
# Edit .env with your values

# 3. Start the app
docker-compose up --build
```

- API: `http://localhost:5000`
- Dashboard: `http://localhost:3000`

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/upload` | Upload a PDF document |
| `GET` | `/documents` | List all processed documents |
| `GET` | `/documents/<id>` | Get extracted data for a document |
| `GET` | `/health` | Health check |

---

## Environment Variables

See `.env.example` for all required variables. Key ones:

```
S3_BUCKET_NAME      # S3 bucket for document storage
RDS_HOST            # PostgreSQL host
RDS_PASSWORD        # PostgreSQL password
SES_SENDER_EMAIL    # Verified sender email for notifications
EC2_BACKEND_URL     # Backend URL (used by Lambda trigger)
```
