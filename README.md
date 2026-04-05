# FinSight — AI-Powered Financial Document Intelligence Platform
### CSYE 6225 Cloud Computing | Northeastern University

---

## Architecture Overview

```
                        ┌─────────────────────────────────────────┐
                        │              AWS us-east-1              │
                        │                                         │
  Browser / User        │   ┌──────────────────────────────────┐  │
      │                 │   │         EC2 t3.large             │  │
      │  Upload PDF     │   │  ┌────────────┐  ┌────────────┐  │  │
      ├────────────────►│──►│  │ Flask API  │  │  Metabase  │  │  │
      │  :5000          │   │  │  :5000     │  │   :3000    │  │  │
      │  :3000          │   │  └─────┬──────┘  └─────┬──────┘  │  │
                        │   │        │  Tesseract OCR │         │  │
                        │   └────────┼───────────────┼──────────┘  │
                        │            │               │             │
                        │   ┌────────▼───┐   ┌───────▼───────┐    │
                        │   │     S3     │   │  RDS Postgres  │   │
                        │   │  (uploads) │   │   (finsight)   │   │
                        │   └────────────┘   └───────────────┘    │
                        │            │                             │
                        │   ┌────────▼───────────┐                │
                        │   │  Lambda            │                │
                        │   │  (S3 → EC2 trigger)│                │
                        │   └────────────────────┘                │
                        │                                         │
                        │   ┌───────────┐  ┌──────────────────┐   │
                        │   │    SES    │  │   CloudWatch     │   │
                        │   │  (email)  │  │  (logs/metrics)  │   │
                        │   └───────────┘  └──────────────────┘   │
                        └─────────────────────────────────────────┘
```

---

## AWS Services Used

| Service | Purpose | Tier |
|---------|---------|------|
| EC2 t3.large | Runs Flask API, Tesseract OCR, Metabase | ~$0.08/hr |
| S3 | Document storage | Free tier / pay per GB |
| RDS PostgreSQL db.t3.micro | Structured data storage | Free tier eligible |
| Lambda | S3-triggered OCR pipeline | Free tier |
| SES | Email notifications | $0.10/1000 emails |
| CloudWatch | Logs, metrics, alarms | Free tier |
| IAM LabRole | Pre-provided by AWS Academy | Free |

---

## Part 1: AWS Academy Setup

### 1.1 — Start your Lab Session
1. Log in to AWS Academy → click **Start Lab** → wait for the green dot
2. Click **AWS** to open the Console in `us-east-1`
3. Your session lasts ~4 hours — save your work before it expires

### 1.2 — Create S3 Bucket
1. Go to **S3 → Create bucket**
2. Name: `finsight-documents-<yourname>` (must be globally unique)
3. Region: `us-east-1`
4. **Block all public access: ON** (all 4 checkboxes checked)
5. **Versioning: Disabled** (saves credits)
6. Click **Create bucket**

### 1.3 — Create RDS PostgreSQL Instance
1. Go to **RDS → Create database**
2. Engine: **PostgreSQL 15**
3. Template: **Free tier**
4. DB instance identifier: `finsight-db`
5. Master username: `admin`
6. Master password: choose a strong password (save it!)
7. Instance: `db.t3.micro`
8. Storage: 20 GB gp2
9. **Publicly accessible: NO**
10. VPC security group: `finsight-rds-sg` (create first — see `infrastructure/security_groups.md`)
11. Initial database name: `finsight`
12. Click **Create database** → takes ~5 minutes

### 1.4 — Create Security Groups
Follow `infrastructure/security_groups.md` exactly.

### 1.5 — Create EC2 Instance
1. Go to **EC2 → Launch instance**
2. Name: `finsight-server`
3. AMI: **Ubuntu Server 22.04 LTS (64-bit x86)**
4. Instance type: `t3.large`
5. Key pair: create or reuse an existing key pair (download .pem file!)
6. Security group: `finsight-ec2-sg`
7. Storage: 30 GB gp3
8. **Advanced → IAM instance profile → LabRole**
9. Click **Launch instance**

### 1.6 — Create Lambda Function
1. Go to **Lambda → Create function**
2. Name: `finsight-ocr-trigger`
3. Runtime: **Python 3.12**
4. Execution role: **Use existing role → LabRole**
5. Click **Create function**
6. In the code editor, paste the contents of `lambda/trigger_ocr.py`
7. Click **Deploy**

**Add environment variable:**
- Key: `EC2_BACKEND_URL`
- Value: `http://<your-ec2-public-ip>:5000`

**Add S3 trigger:**
1. Click **Add trigger → S3**
2. Bucket: `finsight-documents-<yourname>`
3. Event type: **All object create events**
4. Click **Add**

### 1.7 — Configure SES (Email Alerts)
1. Go to **SES → Verified identities → Create identity**
2. Identity type: **Email address**
3. Enter your email → click **Create identity**
4. Check your inbox and click the verification link
5. Repeat for a second email (sender address)

> AWS Academy SES is in **Sandbox mode** — both sender AND recipient must be verified.

---

## Part 2: SSH into EC2 and Bootstrap

### 2.1 — Connect via SSH

**Windows (PowerShell or Git Bash):**
```bash
# Change permissions on your key file (Git Bash)
chmod 400 ~/Downloads/your-key.pem

# SSH into EC2
ssh -i ~/Downloads/your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

> Find your EC2 Public IP: EC2 → Instances → select your instance → Public IPv4 address

### 2.2 — Upload project files to EC2

**From your local machine (not EC2):**
```bash
# Upload the entire finsight project
scp -i ~/Downloads/your-key.pem -r C:/Cloudcomputing/Project/finsight ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/
```

### 2.3 — Run the bootstrap script

**On EC2:**
```bash
cd /home/ubuntu/finsight
chmod +x infrastructure/setup_ec2.sh
sudo ./infrastructure/setup_ec2.sh
```

This installs Docker, Tesseract, AWS CLI, and Python. Takes ~5–7 minutes.

### 2.4 — Configure environment variables

```bash
cd /home/ubuntu/finsight
cp .env.example .env
nano .env
```

Fill in:
- `S3_BUCKET_NAME` — your bucket name
- `RDS_HOST` — your RDS endpoint (from RDS console)
- `RDS_PASSWORD` — the password you set
- `SES_SENDER_EMAIL` / `NOTIFICATION_EMAIL` — your verified emails

### 2.5 — Load the database schema

```bash
# Install psql client if not present
sudo apt-get install -y postgresql-client

# Run schema (replace with your RDS endpoint)
psql -h <RDS_ENDPOINT> -U admin -d finsight -f infrastructure/rds_schema.sql
```

When prompted, enter your RDS password.

### 2.6 — Start the application

```bash
cd /home/ubuntu/finsight
docker-compose up -d --build
```

Verify everything is running:
```bash
docker-compose ps              # both services should show "Up"
curl http://localhost:5000/health  # should return {"status": "ok"}
```

---

## Part 3: Connect Metabase to RDS

See `metabase/setup_instructions.md` for the full walkthrough.

**Quick summary:**
1. Open `http://<EC2_PUBLIC_IP>:3000`
2. Complete first-time setup
3. Admin → Databases → Add PostgreSQL → use your RDS credentials
4. Create questions and dashboard (SQL queries in setup_instructions.md)

---

## Part 4: Demo Flow (April 16th)

Follow this exact sequence for the demo:

### Step 1 — Show the running application
```
Browser → http://<EC2_IP>:5000/health
→ Shows: {"status": "ok", "timestamp": "..."}

Browser → http://<EC2_IP>:5000/documents
→ Shows: {"total": 0, "documents": []}
```

### Step 2 — Upload a document
```bash
# From your laptop (using curl or Postman):
curl -X POST http://<EC2_IP>:5000/upload \
  -F "file=@sample_bank_statement.pdf"

# Response shows document_id and status: "completed"
```

Show the file appearing in S3: **S3 → finsight-documents bucket → uploads/**

### Step 3 — Show OCR results
```
Browser → http://<EC2_IP>:5000/documents/<document_id>
→ Shows extracted: amounts, dates, vendor_name, category
```

### Step 4 — Show Metabase dashboard
```
Browser → http://<EC2_IP>:3000
→ Show the FinSight Overview dashboard updating live
→ Demonstrate: pie chart (by category), bar chart (income vs expenses), table
```

### Step 5 — Show CloudWatch logs (Lambda triggered)
1. AWS Console → **CloudWatch → Log groups**
2. Open `/aws/lambda/finsight-ocr-trigger`
3. Show the log entry from when you uploaded the file

### Step 6 — Show RDS with populated data
```bash
# SSH into EC2, then:
psql -h <RDS_ENDPOINT> -U admin -d finsight

SELECT * FROM documents;
SELECT * FROM extracted_data LIMIT 10;
SELECT * FROM categories;
```

### Step 7 — Email alert
- Upload another document
- Show the email received in your inbox (SES sent via the Lambda → EC2 → SES flow)

---

## Troubleshooting

### Docker containers not starting
```bash
docker-compose logs backend    # check Flask errors
docker-compose logs metabase   # check Metabase errors
docker-compose down && docker-compose up -d --build   # full restart
```

### Cannot connect to RDS
```bash
# Test from EC2:
nc -zv <RDS_ENDPOINT> 5432
# If it hangs: check finsight-rds-sg allows port 5432 from finsight-ec2-sg
```

### OCR returns empty text
```bash
# Test Tesseract directly on EC2:
tesseract /path/to/test.pdf output_text
cat output_text.txt
# If empty: the PDF may be image-only — pdf2image handles this
```

### S3 upload fails (NoCredentialsError)
```bash
# Verify LabRole is attached:
aws sts get-caller-identity
# Should show LabRole ARN — if it fails, re-attach LabRole in EC2 console
```

### Lambda not triggering
1. Check Lambda → Configuration → Triggers → S3 trigger is enabled
2. Check Lambda → Monitor → View CloudWatch logs for invocation errors
3. Verify `EC2_BACKEND_URL` env var points to the correct EC2 IP
4. Check that port 5000 is open in `finsight-ec2-sg`

### AWS Academy session expired
After restarting the lab session:
- EC2, RDS, S3 all persist — just restart if stopped
- Re-check your EC2's **Public IP** (it changes on restart!)
- Update Lambda's `EC2_BACKEND_URL` environment variable with the new IP
- Restart EC2 if stopped: EC2 → Instances → Start

---

## Project Files Reference

```
finsight/
├── backend/
│   ├── app.py              Flask REST API (upload, list, detail, dashboard)
│   ├── ocr_processor.py    Tesseract OCR + regex field extraction
│   ├── categorizer.py      Keyword-based ML classification
│   ├── db_handler.py       RDS PostgreSQL connection pool + queries
│   ├── s3_handler.py       S3 upload/download/presigned URL helpers
│   ├── requirements.txt    Python dependencies
│   └── Dockerfile          Container definition
├── lambda/
│   └── trigger_ocr.py      S3 event → EC2 OCR trigger
├── infrastructure/
│   ├── setup_ec2.sh        One-time EC2 bootstrap script
│   ├── security_groups.md  SG setup instructions
│   └── rds_schema.sql      PostgreSQL schema + views
├── metabase/
│   └── setup_instructions.md  Metabase → RDS connection guide
├── docker-compose.yml      Runs backend + Metabase on EC2
├── .env.example            Environment variable template
└── README.md               This file
```
