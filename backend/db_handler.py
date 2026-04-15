"""
FinSight - Database Handler
Manages all PostgreSQL (RDS) interactions.
Connection details are read from environment variables — never hardcoded.
Uses connection pooling with automatic reconnect to survive AWS Academy restarts.
"""

import os
import logging
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2 import pool, extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Connection parameters (from environment) ──────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("RDS_HOST", "localhost"),
    "port":     int(os.getenv("RDS_PORT", 5432)),
    "dbname":   os.getenv("RDS_DB", "finsight"),
    "user":     os.getenv("RDS_USER", "admin"),
    "password": os.getenv("RDS_PASSWORD", ""),
    "connect_timeout": 10,
    "sslmode": "require",   # RDS enforces SSL
}


class DBHandler:
    """Thread-safe PostgreSQL handler backed by a connection pool."""

    _pool: pool.ThreadedConnectionPool | None = None

    def __init__(self, min_conn: int = 1, max_conn: int = 10):
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._init_pool()

    # ── Pool management ───────────────────────────────────────────────────────
    def _init_pool(self) -> None:
        """Create the connection pool. Called at startup and on reconnect."""
        try:
            self._pool = pool.ThreadedConnectionPool(
                self._min_conn,
                self._max_conn,
                **DB_CONFIG,
            )
            logger.info(
                "Connected to RDS at %s:%s/%s",
                DB_CONFIG["host"], DB_CONFIG["port"], DB_CONFIG["dbname"],
            )
        except psycopg2.OperationalError as exc:
            logger.error("Could not connect to RDS: %s", exc)
            self._pool = None

    def _get_conn(self):
        """Get a connection from the pool, recreating the pool if needed."""
        if self._pool is None:
            logger.info("Pool not initialised — retrying connection to RDS...")
            self._init_pool()

        if self._pool is None:
            raise RuntimeError("Cannot connect to RDS — check RDS_HOST and credentials")

        try:
            return self._pool.getconn()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning("Lost connection to RDS — recreating pool...")
            self._pool = None
            self._init_pool()
            return self._pool.getconn()

    def _release(self, conn, error: bool = False) -> None:
        """Return a connection to the pool."""
        if self._pool and conn:
            self._pool.putconn(conn, close=error)

    def _execute(
        self,
        query: str,
        params: tuple = (),
        fetch: str = "none",   # "one" | "all" | "none"
    ) -> Any:
        """
        Execute a single query, handling connection errors with one retry.
        fetch: "one" → fetchone(), "all" → fetchall(), "none" → no fetch.
        """
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, params)
                if fetch == "one":
                    result = cur.fetchone()
                elif fetch == "all":
                    result = cur.fetchall()
                else:
                    result = None
                conn.commit()
                return result
        except psycopg2.OperationalError as exc:
            conn.rollback()
            self._release(conn, error=True)
            logger.error("DB operational error: %s — retrying once", exc)
            # One retry after reconnect
            conn = self._get_conn()
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, params)
                if fetch == "one":
                    result = cur.fetchone()
                elif fetch == "all":
                    result = cur.fetchall()
                else:
                    result = None
                conn.commit()
                return result
        except Exception:
            conn.rollback()
            self._release(conn, error=True)
            raise
        finally:
            self._release(conn)

    # ── Document CRUD ─────────────────────────────────────────────────────────
    def insert_document(
        self,
        doc_id: str,
        filename: str,
        s3_key: str,
        status: str = "processing",
        user_id: str | None = None,
    ) -> None:
        """Insert a new document record."""
        self._execute(
            """
            INSERT INTO documents (id, filename, s3_key, upload_date, status, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (doc_id, filename, s3_key, datetime.utcnow(), status, user_id),
        )
        logger.debug("Inserted document %s", doc_id)

    def update_document_status(self, doc_id: str, status: str) -> None:
        """Update the processing status of a document."""
        self._execute(
            "UPDATE documents SET status = %s WHERE id = %s",
            (status, doc_id),
        )

    def get_document_by_id(self, doc_id: str) -> dict | None:
        """Fetch one document by its UUID."""
        row = self._execute(
            "SELECT * FROM documents WHERE id = %s",
            (doc_id,),
            fetch="one",
        )
        return dict(row) if row else None

    def get_document_by_s3_key(self, s3_key: str) -> dict | None:
        """Fetch one document by its S3 key."""
        row = self._execute(
            "SELECT * FROM documents WHERE s3_key = %s",
            (s3_key,),
            fetch="one",
        )
        return dict(row) if row else None

    def get_all_documents(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[dict]:
        """Fetch a paginated list of documents scoped to a user."""
        p: list = []
        if user_id: p.append(user_id)
        if status:  p.append(status)
        p += [limit, offset]
        rows = self._execute(
            f"""
            SELECT d.*, c.category, c.confidence AS category_confidence
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT category, confidence FROM categories
                WHERE document_id = d.id ORDER BY confidence DESC LIMIT 1
            ) c ON TRUE
            WHERE 1=1
            {"AND d.user_id = %s" if user_id else ""}
            {"AND d.status = %s" if status else ""}
            ORDER BY d.upload_date DESC
            LIMIT %s OFFSET %s
            """,
            tuple(p),
            fetch="all",
        )
        return [dict(r) for r in rows] if rows else []

    def count_documents(self, status: str | None = None, user_id: str | None = None) -> int:
        """Return the total count of documents for a user."""
        p = [v for v in [user_id, status] if v is not None]
        row = self._execute(
            f"""SELECT COUNT(*) AS cnt FROM documents WHERE 1=1
            {"AND user_id = %s" if user_id else ""}
            {"AND status = %s" if status else ""}""",
            tuple(p),
            fetch="one",
        )
        return row["cnt"] if row else 0

    # ── Extracted data CRUD ───────────────────────────────────────────────────
    def insert_extracted_data(
        self,
        document_id: str,
        field_name: str,
        field_value: str,
        confidence: float = 0.0,
    ) -> None:
        """Store one extracted field for a document."""
        self._execute(
            """
            INSERT INTO extracted_data (document_id, field_name, field_value, confidence)
            VALUES (%s, %s, %s, %s)
            """,
            (document_id, field_name, field_value, confidence),
        )

    def get_extracted_data(self, document_id: str) -> list[dict]:
        """Fetch all extracted fields for a document."""
        rows = self._execute(
            "SELECT * FROM extracted_data WHERE document_id = %s ORDER BY field_name",
            (document_id,),
            fetch="all",
        )
        return [dict(r) for r in rows] if rows else []

    # ── Category CRUD ─────────────────────────────────────────────────────────
    def insert_category(
        self,
        document_id: str,
        category: str,
        confidence: float,
    ) -> None:
        """Store the category classification result for a document."""
        self._execute(
            """
            INSERT INTO categories (document_id, category, confidence)
            VALUES (%s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE
                SET category = EXCLUDED.category,
                    confidence = EXCLUDED.confidence
            """,
            (document_id, category, confidence),
        )

    def get_categories(self, document_id: str) -> list[dict]:
        """Fetch all category classifications for a document."""
        rows = self._execute(
            "SELECT * FROM categories WHERE document_id = %s ORDER BY confidence DESC",
            (document_id,),
            fetch="all",
        )
        return [dict(r) for r in rows] if rows else []

    # ── Summary / analytics ───────────────────────────────────────────────────
    def get_summary_stats(self, month: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """
        Return aggregated financial stats for the dashboard.
        month: optional "YYYY-MM" filter.
        """
        month_filter_sql = ""
        user_filter_sql = "AND d.user_id = %s" if user_id else ""
        params: tuple = tuple(v for v in [user_id, month] if v is not None) if (user_id or month) else ()
        # Rebuild params in correct order for each query
        def build_params(*extras):
            p = []
            if user_id: p.append(user_id)
            p.extend(extras)
            return tuple(p)

        if month:
            month_filter_sql = "AND TO_CHAR(d.upload_date, 'YYYY-MM') = %s"

        # ── Total income vs expenses ──────────────────────────────────────────
        totals_query = f"""
            SELECT
                c.category,
                SUM(CAST(e.field_value AS NUMERIC)) AS total
            FROM extracted_data e
            JOIN documents d ON e.document_id = d.id
            JOIN categories c ON c.document_id = d.id
            WHERE e.field_name = 'total_amount'
              AND e.field_value ~ '^[0-9]+(\\.[0-9]+)?$'
              AND d.status = 'completed'
              {user_filter_sql}
              {month_filter_sql}
            GROUP BY c.category
            ORDER BY total DESC
        """
        category_rows = self._execute(totals_query, build_params(*([month] if month else [])), fetch="all") or []

        by_category: dict[str, float] = {}
        total_income = 0.0
        total_expenses = 0.0
        for row in category_rows:
            cat_name = row["category"]
            total = float(row["total"] or 0)
            by_category[cat_name] = total
            if cat_name == "Income":
                total_income += total
            else:
                total_expenses += total

        # ── Monthly cash flow (last 12 months) ────────────────────────────────
        monthly_query = f"""
            SELECT
                TO_CHAR(d.upload_date, 'YYYY-MM') AS month,
                SUM(CASE WHEN c.category = 'Income' THEN CAST(e.field_value AS NUMERIC) ELSE 0 END) AS income,
                SUM(CASE WHEN c.category != 'Income' THEN CAST(e.field_value AS NUMERIC) ELSE 0 END) AS expenses
            FROM extracted_data e
            JOIN documents d ON e.document_id = d.id
            JOIN categories c ON c.document_id = d.id
            WHERE e.field_name = 'total_amount'
              AND e.field_value ~ '^[0-9]+(\\.[0-9]+)?$'
              AND d.status = 'completed'
              {user_filter_sql}
              AND d.upload_date >= NOW() - INTERVAL '12 months'
            GROUP BY TO_CHAR(d.upload_date, 'YYYY-MM')
            ORDER BY month ASC
        """
        monthly_rows = self._execute(monthly_query, build_params(), fetch="all") or []
        monthly_cashflow = [
            {
                "month": r["month"],
                "income": float(r["income"] or 0),
                "expenses": float(r["expenses"] or 0),
                "net": float(r["income"] or 0) - float(r["expenses"] or 0),
            }
            for r in monthly_rows
        ]

        # ── Top vendors ───────────────────────────────────────────────────────
        vendor_query = f"""
            SELECT
                e.field_value AS vendor,
                COUNT(*) AS doc_count
            FROM extracted_data e
            JOIN documents d ON e.document_id = d.id
            WHERE e.field_name = 'vendor_name'
              AND d.status = 'completed'
              {user_filter_sql}
              {month_filter_sql}
            GROUP BY e.field_value
            ORDER BY doc_count DESC
            LIMIT 10
        """
        vendor_rows = self._execute(vendor_query, build_params(*([month] if month else [])), fetch="all") or []
        top_vendors = [
            {"vendor": r["vendor"], "count": r["doc_count"]} for r in vendor_rows
        ]

        # ── Document counts by status ─────────────────────────────────────────
        status_query = f"""
            SELECT status, COUNT(*) AS cnt FROM documents
            WHERE 1=1 {user_filter_sql} GROUP BY status
        """
        status_rows = self._execute(status_query, build_params(), fetch="all") or []
        doc_counts = {r["status"]: r["cnt"] for r in status_rows}

        return {
            "total_income": round(total_income, 2),
            "total_expenses": round(total_expenses, 2),
            "net": round(total_income - total_expenses, 2),
            "by_category": by_category,
            "monthly_cashflow": monthly_cashflow,
            "top_vendors": top_vendors,
            "document_counts": doc_counts,
            "month_filter": month,
        }
