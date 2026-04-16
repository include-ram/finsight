-- FinSight PostgreSQL Schema
-- Auto-applied by Docker postgres container on first start.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ── Users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    username      VARCHAR(64)   NOT NULL UNIQUE,
    password_hash VARCHAR(256)  NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ── Documents ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id           UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename     VARCHAR(512)  NOT NULL,
    s3_key       VARCHAR(1024) NOT NULL UNIQUE,
    upload_date  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    status       VARCHAR(20)   NOT NULL DEFAULT 'processing'
                     CHECK (status IN ('processing', 'completed', 'failed')),
    user_id      UUID          REFERENCES users (id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

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

CREATE INDEX IF NOT EXISTS idx_documents_status      ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_upload_date ON documents (upload_date DESC);
CREATE INDEX IF NOT EXISTS idx_documents_user_id     ON documents (user_id);

-- ── Extracted data ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS extracted_data (
    id           BIGSERIAL     PRIMARY KEY,
    document_id  UUID          NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    field_name   VARCHAR(100)  NOT NULL,
    field_value  TEXT          NOT NULL,
    confidence   NUMERIC(5,4)  NOT NULL DEFAULT 0.0
                     CHECK (confidence >= 0 AND confidence <= 1),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extracted_document_id ON extracted_data (document_id);
CREATE INDEX IF NOT EXISTS idx_extracted_field_name  ON extracted_data (field_name);
CREATE INDEX IF NOT EXISTS idx_extracted_amounts
    ON extracted_data (document_id, field_value)
    WHERE field_name = 'total_amount';

-- ── Categories ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS categories (
    id           BIGSERIAL     PRIMARY KEY,
    document_id  UUID          NOT NULL UNIQUE REFERENCES documents (id) ON DELETE CASCADE,
    category     VARCHAR(50)   NOT NULL,
    confidence   NUMERIC(5,4)  NOT NULL DEFAULT 0.0
                     CHECK (confidence >= 0 AND confidence <= 1),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_categories_document_id ON categories (document_id);
CREATE INDEX IF NOT EXISTS idx_categories_category    ON categories (category);
