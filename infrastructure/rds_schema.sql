-- FinSight RDS PostgreSQL Schema
-- Run this against your RDS instance after creation:
--   psql -h <RDS_ENDPOINT> -U admin -d finsight -f rds_schema.sql

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- UUID generation
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- trigram index for text search

-- ── Documents table ───────────────────────────────────────────────────────────
-- One row per uploaded file.
CREATE TABLE IF NOT EXISTS documents (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename     VARCHAR(512)  NOT NULL,
    s3_key       VARCHAR(1024) NOT NULL UNIQUE,
    upload_date  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    status       VARCHAR(20)   NOT NULL DEFAULT 'processing'
                     CHECK (status IN ('processing', 'completed', 'failed')),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Auto-update updated_at on every row modification
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_documents_status      ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_upload_date ON documents (upload_date DESC);

-- ── Extracted data table ──────────────────────────────────────────────────────
-- Stores each OCR-extracted field as a key-value pair.
-- Flexible schema: supports any field_name the OCR engine finds.
CREATE TABLE IF NOT EXISTS extracted_data (
    id           BIGSERIAL     PRIMARY KEY,
    document_id  UUID          NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    field_name   VARCHAR(100)  NOT NULL,   -- e.g. 'total_amount', 'vendor_name', 'primary_date'
    field_value  TEXT          NOT NULL,
    confidence   NUMERIC(5,4)  NOT NULL DEFAULT 0.0
                     CHECK (confidence >= 0 AND confidence <= 1),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extracted_document_id  ON extracted_data (document_id);
CREATE INDEX IF NOT EXISTS idx_extracted_field_name   ON extracted_data (field_name);
-- Partial index for quick amount lookups
CREATE INDEX IF NOT EXISTS idx_extracted_amounts
    ON extracted_data (document_id, field_value)
    WHERE field_name = 'total_amount';

-- ── Categories table ──────────────────────────────────────────────────────────
-- Stores the ML classification result per document.
-- One active category per document (upserted on re-processing).
CREATE TABLE IF NOT EXISTS categories (
    id           BIGSERIAL     PRIMARY KEY,
    document_id  UUID          NOT NULL UNIQUE REFERENCES documents (id) ON DELETE CASCADE,
    category     VARCHAR(50)   NOT NULL,   -- Income | Utilities | Food | Medical | …
    confidence   NUMERIC(5,4)  NOT NULL DEFAULT 0.0
                     CHECK (confidence >= 0 AND confidence <= 1),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_categories_document_id ON categories (document_id);
CREATE INDEX IF NOT EXISTS idx_categories_category    ON categories (category);

-- ── Summary cache table ───────────────────────────────────────────────────────
-- Pre-computed monthly summaries — refreshed by a nightly job or on demand.
-- Metabase can query this directly for fast dashboard rendering.
CREATE TABLE IF NOT EXISTS summary_cache (
    id               BIGSERIAL     PRIMARY KEY,
    month            CHAR(7)       NOT NULL UNIQUE,  -- 'YYYY-MM'
    total_income     NUMERIC(15,2) NOT NULL DEFAULT 0,
    total_expenses   NUMERIC(15,2) NOT NULL DEFAULT 0,
    net              NUMERIC(15,2) GENERATED ALWAYS AS (total_income - total_expenses) STORED,
    by_category      JSONB         NOT NULL DEFAULT '{}',  -- {"Food": 234.50, "Rent": 1200.00, …}
    top_vendors      JSONB         NOT NULL DEFAULT '[]',  -- [{"vendor": "Whole Foods", "count": 5}]
    document_count   INT           NOT NULL DEFAULT 0,
    refreshed_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_summary_cache_month ON summary_cache (month DESC);

-- ── View: dashboard_overview ──────────────────────────────────────────────────
-- Convenience view used by Metabase for the main dashboard.
CREATE OR REPLACE VIEW dashboard_overview AS
SELECT
    d.id,
    d.filename,
    d.s3_key,
    d.upload_date,
    d.status,
    c.category,
    c.confidence AS category_confidence,
    -- Pull total_amount from extracted_data (first match)
    (
        SELECT e.field_value::NUMERIC
        FROM extracted_data e
        WHERE e.document_id = d.id
          AND e.field_name = 'total_amount'
          AND e.field_value ~ '^[0-9]+(\.[0-9]+)?$'
        LIMIT 1
    ) AS amount,
    -- Pull primary date
    (
        SELECT e.field_value
        FROM extracted_data e
        WHERE e.document_id = d.id
          AND e.field_name = 'primary_date'
        LIMIT 1
    ) AS document_date,
    -- Pull vendor name
    (
        SELECT e.field_value
        FROM extracted_data e
        WHERE e.document_id = d.id
          AND e.field_name = 'vendor_name'
        LIMIT 1
    ) AS vendor
FROM documents d
LEFT JOIN categories c ON c.document_id = d.id
WHERE d.status = 'completed';

-- ── Seed data (for local dev / demo) ─────────────────────────────────────────
-- Uncomment to load sample data for testing without real uploads.
/*
INSERT INTO documents (id, filename, s3_key, status) VALUES
    ('11111111-1111-1111-1111-111111111111', 'bank_statement_jan.pdf',  'uploads/11111111-1111-1111-1111-111111111111/bank_statement_jan.pdf',  'completed'),
    ('22222222-2222-2222-2222-222222222222', 'electric_bill_feb.pdf',   'uploads/22222222-2222-2222-2222-222222222222/electric_bill_feb.pdf',   'completed'),
    ('33333333-3333-3333-3333-333333333333', 'whole_foods_receipt.jpg', 'uploads/33333333-3333-3333-3333-333333333333/whole_foods_receipt.jpg', 'completed');

INSERT INTO extracted_data (document_id, field_name, field_value, confidence) VALUES
    ('11111111-1111-1111-1111-111111111111', 'total_amount', '3500.00', 0.92),
    ('11111111-1111-1111-1111-111111111111', 'vendor_name',  'Bank of America', 0.88),
    ('11111111-1111-1111-1111-111111111111', 'primary_date', '01/31/2024', 0.95),
    ('22222222-2222-2222-2222-222222222222', 'total_amount', '124.56', 0.91),
    ('22222222-2222-2222-2222-222222222222', 'vendor_name',  'Con Edison', 0.89),
    ('22222222-2222-2222-2222-222222222222', 'primary_date', '02/15/2024', 0.94),
    ('33333333-3333-3333-3333-333333333333', 'total_amount', '87.32', 0.96),
    ('33333333-3333-3333-3333-333333333333', 'vendor_name',  'Whole Foods Market', 0.97),
    ('33333333-3333-3333-3333-333333333333', 'primary_date', '02/20/2024', 0.98);

INSERT INTO categories (document_id, category, confidence) VALUES
    ('11111111-1111-1111-1111-111111111111', 'Income',    0.85),
    ('22222222-2222-2222-2222-222222222222', 'Utilities', 0.90),
    ('33333333-3333-3333-3333-333333333333', 'Food',      0.92);
*/
