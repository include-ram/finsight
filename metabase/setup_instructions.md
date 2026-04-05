# Metabase Setup — Connecting to RDS

Metabase runs on port 3000 of your EC2 instance.

---

## Step 1 — First-time Metabase setup

1. Open `http://<EC2_PUBLIC_IP>:3000` in your browser
2. Click **Let's get started**
3. Fill in your name, email, and a password for Metabase admin
4. On the **Add your data** screen → click **I'll add my data later** (we'll do it next)

---

## Step 2 — Connect Metabase to RDS

1. Go to **Settings (gear icon) → Admin → Databases → Add a database**
2. Fill in:

| Field             | Value                                            |
|-------------------|--------------------------------------------------|
| Database type     | PostgreSQL                                       |
| Display name      | FinSight RDS                                     |
| Host              | `<your-rds-endpoint>.rds.amazonaws.com`          |
| Port              | `5432`                                           |
| Database name     | `finsight`                                       |
| Username          | `admin`                                          |
| Password          | `<your RDS password>`                            |
| SSL               | **Enabled** (RDS requires SSL)                   |

3. Click **Save**
4. Metabase will test the connection — if it fails, check that:
   - Your RDS Security Group allows port 5432 from `finsight-ec2-sg`
   - The RDS instance is in the same VPC as EC2
   - You're using the correct endpoint (not `localhost`)

---

## Step 3 — Create the Dashboard

### Question 1: Total Income vs Expenses
- New Question → Custom query (SQL)
```sql
SELECT
    CASE WHEN c.category = 'Income' THEN 'Income' ELSE 'Expense' END AS type,
    SUM(e.field_value::NUMERIC) AS total
FROM extracted_data e
JOIN documents d ON e.document_id = d.id
JOIN categories c ON c.document_id = d.id
WHERE e.field_name = 'total_amount'
  AND e.field_value ~ '^[0-9]+(\.[0-9]+)?$'
  AND d.status = 'completed'
GROUP BY type;
```
- Visualize as: **Bar chart** or **Row chart**

### Question 2: Spending by Category
```sql
SELECT c.category, SUM(e.field_value::NUMERIC) AS total
FROM extracted_data e
JOIN documents d ON e.document_id = d.id
JOIN categories c ON c.document_id = d.id
WHERE e.field_name = 'total_amount'
  AND e.field_value ~ '^[0-9]+(\.[0-9]+)?$'
  AND d.status = 'completed'
  AND c.category != 'Income'
GROUP BY c.category
ORDER BY total DESC;
```
- Visualize as: **Pie chart** or **Bar chart**

### Question 3: Monthly Cash Flow
```sql
SELECT
    TO_CHAR(d.upload_date, 'YYYY-MM') AS month,
    SUM(CASE WHEN c.category = 'Income' THEN e.field_value::NUMERIC ELSE 0 END) AS income,
    SUM(CASE WHEN c.category != 'Income' THEN e.field_value::NUMERIC ELSE 0 END) AS expenses
FROM extracted_data e
JOIN documents d ON e.document_id = d.id
JOIN categories c ON c.document_id = d.id
WHERE e.field_name = 'total_amount'
  AND e.field_value ~ '^[0-9]+(\.[0-9]+)?$'
  AND d.status = 'completed'
GROUP BY month
ORDER BY month;
```
- Visualize as: **Line chart** with two series

### Question 4: Recent Documents
```sql
SELECT filename, category, upload_date, status
FROM dashboard_overview
ORDER BY upload_date DESC
LIMIT 20;
```
- Visualize as: **Table**

### Step 4 — Build the Dashboard
1. Go to **+ New → Dashboard** → name it "FinSight Overview"
2. Click **Add a question** and add all 4 questions above
3. Arrange the cards: bar chart (top-left), pie (top-right), line (full-width middle), table (bottom)
4. Click **Save**

---

## Auto-refresh
Click the **clock icon** on the dashboard → set to **1 minute** for live demo effect.
