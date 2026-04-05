# FinSight — Security Group Setup (AWS Console)

Create these two Security Groups in **VPC → Security Groups** in `us-east-1`.

---

## SG 1: `finsight-ec2-sg`

**Purpose:** Controls traffic to the EC2 instance.

### Inbound Rules

| Type        | Protocol | Port  | Source              | Description                     |
|-------------|----------|-------|---------------------|---------------------------------|
| SSH         | TCP      | 22    | Your IP/32          | SSH access (your machine only)  |
| Custom TCP  | TCP      | 5000  | 0.0.0.0/0           | Flask API (public demo access)  |
| Custom TCP  | TCP      | 3000  | 0.0.0.0/0           | Metabase dashboard              |
| HTTP        | TCP      | 80    | 0.0.0.0/0           | Optional: redirect to 5000      |

> **Security note:** For production, restrict port 22 to your IP only.  
> For the demo, you can open 5000 and 3000 to 0.0.0.0/0.

### Outbound Rules

| Type        | Protocol | Port  | Destination | Description        |
|-------------|----------|-------|-------------|--------------------|
| All traffic | All      | All   | 0.0.0.0/0   | Allow all outbound |

---

## SG 2: `finsight-rds-sg`

**Purpose:** Controls traffic to the RDS PostgreSQL instance.  
**Critical:** RDS must ONLY accept connections from the EC2 instance.

### Inbound Rules

| Type            | Protocol | Port | Source               | Description                        |
|-----------------|----------|------|----------------------|------------------------------------|
| PostgreSQL      | TCP      | 5432 | finsight-ec2-sg (SG) | Allow EC2 → RDS only               |

> Set the **Source** to the Security Group ID of `finsight-ec2-sg`, NOT an IP range.  
> This way, only instances in `finsight-ec2-sg` can reach the database.

### Outbound Rules

| Type        | Protocol | Port | Destination | Description        |
|-------------|----------|------|-------------|--------------------|
| All traffic | All      | All  | 0.0.0.0/0   | Allow all outbound |

---

## How to Apply in the AWS Console

### Step 1 — Create `finsight-ec2-sg`
1. Go to **EC2 → Security Groups → Create security group**
2. Name: `finsight-ec2-sg`
3. VPC: select your default VPC
4. Add inbound rules from table above
5. Click **Create**

### Step 2 — Create `finsight-rds-sg`
1. Go to **EC2 → Security Groups → Create security group**
2. Name: `finsight-rds-sg`
3. VPC: same default VPC
4. Inbound: PostgreSQL (5432) → Source = **Security group** → select `finsight-ec2-sg`
5. Click **Create**

### Step 3 — Attach SGs to resources
- **EC2 instance:** Actions → Security → Change security groups → add `finsight-ec2-sg`
- **RDS instance:** Modify → Connectivity → Security groups → set to `finsight-rds-sg`

---

## Lambda — No Security Group Needed
Lambda runs outside the VPC by default. It calls the EC2 public endpoint on port 5000.  
(Port 5000 is already open in `finsight-ec2-sg` above.)
