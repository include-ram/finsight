# FinSight — AWS Academy Deployment Guide

This guide walks you through deploying FinSight end-to-end on AWS Academy.  
All steps use the AWS Console and SSH — no local AWS CLI credentials needed.

---

## AWS Academy Constraints (Read First)

| Constraint | Impact |
|------------|--------|
| **LabRole only** — cannot create IAM users or custom roles | EC2 and Lambda must use the pre-existing `LabRole`. No hardcoded credentials. |
| **Sessions expire** (~4 hours) | When you restart the lab, the EC2 public IP changes. Update `EC2_BACKEND_URL` in Lambda's env var. |
| **SES is in Sandbox** | Both sender AND recipient emails must be verified in SES before emails work. |
| **Region: us-east-1** | All resources must be in `us-east-1`. |
| **Budget: ~$100** | t3.large + db.t3.micro + standard S3 storage stays well within limits for a demo. |

---

## Architecture Overview

```
  Browser / curl
       │
       │ POST /upload (PDF)
       ▼
  EC2 t3.large  (:5000 Flask API, :3000 Metabase)
       │
       ├──► S3 Bucket  ──► Lambda trigger_ocr
       │                        │
       │◄───────────────────────┘
       │  POST /process
       │
       ├──► Tesseract OCR
       ├──► Categorizer
       ├──► RDS PostgreSQL  ◄──── Metabase
       └──► SES email
```

---

## Step 1 — Start the AWS Academy Lab

1. Log into **AWS Academy Learner Lab**
2. Click **Start Lab** — wait until the circle turns green
3. Click **AWS** to open the AWS Console
4. Confirm you are in **us-east-1** (top-right region selector)

> The lab session lasts ~4 hours. Save your work often. When the session restarts, your EC2 instance will restart automatically — you just need to update the Lambda env var with the new EC2 IP.

---

## Step 2 — Create the S3 Bucket

1. Go to **S3 → Create bucket**
2. Settings:
   - **Bucket name:** `finsight-documents-<yourname>` (must be globally unique)
   - **Region:** us-east-1
   - **Block all public access:** enabled (checked — default)
   - **Versioning:** disabled
   - **Default encryption:** SSE-S3 (AES-256) — enabled by default
3. Click **Create bucket**

> Write down your bucket name — you'll need it in `.env`.

---

## Step 3 — Create Security Groups

### 3a — EC2 Security Group (`finsight-ec2-sg`)

1. Go to **EC2 → Security Groups → Create security group**
2. Fill in:
   - **Name:** `finsight-ec2-sg`
   - **VPC:** default VPC
3. **Inbound rules** — click "Add rule" for each:

   | Type       | Port  | Source    | Why                        |
   |------------|-------|-----------|----------------------------|
   | SSH        | 22    | My IP     | SSH access to the instance |
   | Custom TCP | 5000  | 0.0.0.0/0 | Flask API (public)         |
   | Custom TCP | 3000  | 0.0.0.0/0 | Metabase dashboard         |

4. **Outbound:** leave default (all traffic allowed)
5. Click **Create security group**

### 3b — RDS Security Group (`finsight-rds-sg`)

1. **EC2 → Security Groups → Create security group**
2. Fill in:
   - **Name:** `finsight-rds-sg`
   - **VPC:** same default VPC
3. **Inbound rule:**

   | Type       | Port | Source              | Why                        |
   |------------|------|---------------------|----------------------------|
   | PostgreSQL | 5432 | **finsight-ec2-sg** | EC2 → RDS only (no public) |

   > For Source, choose **Custom** and type the name `finsight-ec2-sg` to reference it as a security group (not an IP).

4. Click **Create security group**

---

## Step 4 — Create the RDS PostgreSQL Instance

1. Go to **RDS → Create database**
2. Settings:
   - **Engine:** PostgreSQL
   - **Version:** PostgreSQL 16.x (latest stable)
   - **Template:** Free tier
   - **DB instance identifier:** `finsight-db`
   - **Master username:** `admin`
   - **Master password:** choose a strong password (e.g. `Finsight2024!`) — **write it down**
   - **Instance class:** db.t3.micro (Free tier)
   - **Storage:** 20 GB gp2
   - **Multi-AZ:** No (single AZ for Academy)
3. **Connectivity:**
   - **VPC:** default
   - **Public access:** **No** (not publicly accessible — EC2 reaches it via SG)
   - **Security group:** remove the default, add `finsight-rds-sg`
4. **Additional configuration:**
   - **Initial database name:** `finsight`
   - **Automated backups:** disable (saves storage in Academy)
5. Click **Create database** — takes ~5 minutes

Once available, go to **RDS → Databases → finsight-db → Connectivity** and copy the **Endpoint** (looks like `finsight-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com`).

---

## Step 5 — Launch the EC2 Instance

1. Go to **EC2 → Launch instance**
2. Settings:
   - **Name:** `finsight-server`
   - **AMI:** Ubuntu Server 22.04 LTS (64-bit x86)
   - **Instance type:** t3.large
   - **Key pair:** create a new key pair named `finsight-key`, download the `.pem` file and keep it safe
3. **Network settings:**
   - **VPC:** default
   - **Auto-assign public IP:** Enable
   - **Security group:** select existing → `finsight-ec2-sg`
4. **Advanced details:**
   - **IAM instance profile:** `LabRole`
     > This gives the EC2 instance automatic access to S3, RDS, SES, and CloudWatch — no keys needed.
5. **Storage:** 30 GB gp3 (Tesseract + Docker images need space)
6. Click **Launch instance**

Wait for the instance to show **running** status. Copy the **Public IPv4 address**.

---

## Step 6 — SSH Into EC2 and Run Setup

### 6a — Connect via SSH

On your local machine (Git Bash / WSL / Terminal):

```bash
# Fix key permissions (required on Linux/Mac — skip on Windows Git Bash)
chmod 400 ~/Downloads/finsight-key.pem

ssh -i ~/Downloads/finsight-key.pem ubuntu@<EC2_PUBLIC_IP>
```

On **Windows** with Git Bash, the path might look like:
```bash
ssh -i /c/Users/<yourname>/Downloads/finsight-key.pem ubuntu@<EC2_PUBLIC_IP>
```

### 6b — Upload the project files to EC2

From your **local machine** (in the `finsight` project directory), run:

```bash
# Copy the whole project to the EC2 instance
scp -i ~/Downloads/finsight-key.pem -r /c/Cloudcomputing/Project/finsight ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/finsight
```

Alternatively, if you have the repo on GitHub:
```bash
# On the EC2 instance (inside SSH session):
git clone https://github.com/include-ram/finsight.git /home/ubuntu/finsight
```

### 6c — Run the bootstrap script

```bash
# On the EC2 instance:
cd /home/ubuntu/finsight
chmod +x infrastructure/setup_ec2.sh
sudo ./infrastructure/setup_ec2.sh
```

This installs Python 3.11, Tesseract OCR, Poppler, AWS CLI, Docker, and Docker Compose.  
Takes ~5–8 minutes. Watch for any errors.

---

## Step 7 — Configure Environment Variables

On the EC2 instance:

```bash
cd /home/ubuntu/finsight
cp .env.example .env
nano .env
```

Fill in each value:

```env
AWS_REGION=us-east-1

# S3 — the bucket name you created in Step 2
S3_BUCKET_NAME=finsight-documents-<yourname>

# RDS — endpoint from Step 4
RDS_HOST=finsight-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com
RDS_PORT=5432
RDS_DB=finsight
RDS_USER=admin
RDS_PASSWORD=Finsight2024!

# SES — must be verified (Step 10)
SES_SENDER_EMAIL=youremail@example.com
NOTIFICATION_EMAIL=youremail@example.com

# Flask
FLASK_ENV=production
FLASK_PORT=5000

# Lambda → EC2 (your EC2 public IP)
EC2_BACKEND_URL=http://<EC2_PUBLIC_IP>:5000
```

Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

> **AWS credentials are NOT set here.** The EC2 LabRole instance profile provides them automatically. boto3 will find them via the instance metadata service.

---

## Step 8 — Initialize the Database Schema

Install the PostgreSQL client on EC2 (if not already present):

```bash
sudo apt-get install -y postgresql-client
```

Run the schema against RDS:

```bash
psql \
  -h <RDS_ENDPOINT> \
  -U admin \
  -d finsight \
  -f /home/ubuntu/finsight/infrastructure/rds_schema.sql
```

When prompted, enter your RDS password (`Finsight2024!`).

Verify the tables were created:

```bash
psql -h <RDS_ENDPOINT> -U admin -d finsight -c "\dt"
```

Expected output:
```
 Schema |    Name       | Type  | Owner
--------+---------------+-------+-------
 public | categories    | table | admin
 public | documents     | table | admin
 public | extracted_data| table | admin
 public | summary_cache | table | admin
```

---

## Step 9 — Start the Application

```bash
cd /home/ubuntu/finsight
docker compose up -d --build
```

> The first build takes ~5 minutes (downloading base images, installing Tesseract inside Docker). Subsequent starts are fast.

Check that both containers are running:

```bash
docker compose ps
```

Expected output:
```
NAME                 STATUS          PORTS
finsight-backend     Up (healthy)    0.0.0.0:5000->5000/tcp
finsight-metabase    Up (healthy)    0.0.0.0:3000->3000/tcp
```

Test the API:

```bash
curl http://localhost:5000/health
# Expected: {"status": "ok", "timestamp": "..."}
```

Test from your browser: `http://<EC2_PUBLIC_IP>:5000/health`

---

## Step 10 — Verify SES Email Addresses (Optional)

AWS Academy runs SES in **Sandbox mode** — both sender and recipient must be verified.

1. Go to **SES → Verified identities → Create identity**
2. **Identity type:** Email address
3. Enter `youremail@example.com` → click **Create identity**
4. Check your inbox for a verification link and click it
5. Repeat for both `SES_SENDER_EMAIL` and `NOTIFICATION_EMAIL` if they're different addresses

> If you skip this step, the app still works — SES email alerts will fail silently (logged as warnings, not errors).

---

## Step 11 — Deploy the Lambda Function

### 11a — Create the Lambda function

1. Go to **Lambda → Create function**
2. Settings:
   - **Author from scratch**
   - **Function name:** `finsight-ocr-trigger`
   - **Runtime:** Python 3.11
   - **Architecture:** x86_64
   - **Permissions:** Use an existing role → **LabRole**
3. Click **Create function**

### 11b — Upload the Lambda code

1. In the Lambda console, scroll to **Code source**
2. Click **Upload from → .zip file**
3. Create a zip file first (on your local machine or EC2):

   ```bash
   cd /c/Cloudcomputing/Project/finsight/lambda
   zip trigger_ocr.zip trigger_ocr.py
   ```

4. Upload `trigger_ocr.zip`
5. Click **Deploy**

### 11c — Configure Lambda environment variables

1. Go to **Configuration → Environment variables → Edit**
2. Add:
   - `EC2_BACKEND_URL` = `http://<EC2_PUBLIC_IP>:5000`
   - `REQUEST_TIMEOUT` = `60`
3. Click **Save**

### 11d — Set Lambda timeout

1. **Configuration → General configuration → Edit**
2. **Timeout:** 1 min 30 sec (OCR on large PDFs can take ~60s)
3. Click **Save**

### 11e — Add S3 trigger

1. Go to **Configuration → Triggers → Add trigger**
2. **Source:** S3
3. **Bucket:** `finsight-documents-<yourname>`
4. **Event types:** `s3:ObjectCreated:*`
5. **Prefix:** `uploads/` (optional — prevents triggering on non-document keys)
6. Check the acknowledgement checkbox
7. Click **Add**

---

## Step 12 — Configure Metabase

1. Open `http://<EC2_PUBLIC_IP>:3000` in your browser
2. Complete the Metabase setup wizard:
   - Create an admin account (email + password)
   - **Skip** the "Add your data" step for now
3. After setup, go to **Settings (gear icon) → Admin → Databases → Add a database**
4. Settings:
   - **Database type:** PostgreSQL
   - **Display name:** FinSight RDS
   - **Host:** `<RDS_ENDPOINT>`
   - **Port:** 5432
   - **Database name:** finsight
   - **Username:** admin
   - **Password:** `Finsight2024!`
   - **SSL:** Required
5. Click **Save** — Metabase will test the connection

For detailed chart setup (4 dashboard charts), see `metabase/setup_instructions.md`.

---

## Step 13 — Test the Full Flow

### Upload a document via curl

```bash
# From your local machine (replace with your EC2 IP)
curl -X POST http://<EC2_PUBLIC_IP>:5000/upload \
  -F "file=@/path/to/sample_invoice.pdf"
```

Expected response:
```json
{
  "id": "abc-123...",
  "filename": "sample_invoice.pdf",
  "status": "completed",
  "upload_date": "2024-03-01T12:00:00Z"
}
```

### Check the documents list

```bash
curl http://<EC2_PUBLIC_IP>:5000/documents
```

### Check the dashboard summary

```bash
curl http://<EC2_PUBLIC_IP>:5000/dashboard/summary
```

### Check S3 → Lambda flow

1. Upload a PDF directly to S3 via the console (to `finsight-documents-<yourname>/uploads/test/test.pdf`)
2. Go to **Lambda → finsight-ocr-trigger → Monitor → View CloudWatch logs**
3. You should see the Lambda invocation and a successful POST to EC2

---

## Step 14 — View Logs

### Application logs

```bash
# On EC2:
docker compose logs -f backend     # Flask API logs
docker compose logs -f metabase    # Metabase logs
```

### Lambda logs

**CloudWatch → Log groups → /aws/lambda/finsight-ocr-trigger**

### CloudWatch metrics (memory/disk)

**CloudWatch → Metrics → CWAgent** — look for `mem_used_percent` and `disk_used_percent` under your instance ID.

---

## Handling AWS Academy Session Restarts

When your Academy session expires and restarts:

1. **EC2 instance** restarts automatically — LabRole credentials auto-refresh (boto3 picks them up)
2. **EC2 public IP changes** — get the new IP from EC2 console
3. **Update Lambda env var:** Lambda → finsight-ocr-trigger → Configuration → Environment variables → Edit `EC2_BACKEND_URL` = `http://<NEW_EC2_IP>:5000`
4. **Restart Docker if needed:**

   ```bash
   ssh -i finsight-key.pem ubuntu@<NEW_EC2_IP>
   cd /home/ubuntu/finsight
   docker compose up -d
   ```

> The `.env` file on EC2 also has `EC2_BACKEND_URL` — update it too if Lambda calls fail. (It's used only for local reference; Lambda reads its own env var.)

---

## Quick Reference — URLs After Deployment

| Service | URL |
|---------|-----|
| Flask API health | `http://<EC2_IP>:5000/health` |
| Upload endpoint | `POST http://<EC2_IP>:5000/upload` |
| Documents list | `GET http://<EC2_IP>:5000/documents` |
| Dashboard summary | `GET http://<EC2_IP>:5000/dashboard/summary` |
| Metabase | `http://<EC2_IP>:3000` |

---

## Troubleshooting

### Docker containers won't start

```bash
docker compose logs backend
# Common cause: .env missing or RDS not reachable
# Fix: verify RDS endpoint, SG rules, and .env values
```

### RDS connection refused

- Check `finsight-rds-sg` inbound rule — source must be `finsight-ec2-sg` (not an IP)
- Verify RDS is in **Available** state (not starting/stopping)
- Confirm `RDS_HOST` in `.env` matches the RDS endpoint exactly

### Lambda → EC2 timeout

- EC2 must be running and Docker containers healthy
- Port 5000 must be open in `finsight-ec2-sg`
- Lambda timeout must be ≥ 90 seconds (OCR is slow for large PDFs)
- Check `EC2_BACKEND_URL` env var in Lambda — IP changes after session restart

### S3 upload fails (403 / NoCredentialsError)

- The EC2 instance must have `LabRole` attached (check IAM instance profile in EC2 → Instance details)
- Do NOT set `AWS_ACCESS_KEY_ID` in `.env` — it overrides the instance profile and causes failures in Academy

### OCR returns empty fields

- The PDF may be image-only (scanned) — Tesseract still extracts text but confidence is low
- Check Docker logs: `docker compose logs -f backend`
- Verify Tesseract is installed: `docker exec finsight-backend tesseract --version`

---

## Cost Estimate (AWS Academy)

| Resource | Type | Est. Cost/hr |
|----------|------|-------------|
| EC2 | t3.large | ~$0.083/hr |
| RDS | db.t3.micro | ~$0.017/hr |
| S3 | Standard storage | ~$0.023/GB/mo |
| Lambda | First 1M req free | ~$0 |
| SES | First 62k emails/mo free | ~$0 |

Running continuously for 8 hours ≈ **~$0.80/session** — well within the $100 budget.

> Stop the EC2 and RDS instances when not in use to preserve your lab budget.
