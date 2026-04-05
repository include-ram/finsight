"""
FinSight - Document Categorizer
Keyword-based classification of financial documents into spending categories.
Returns category name and a confidence score (0.0–1.0).
"""

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Category keyword definitions ──────────────────────────────────────────────
# Each entry: category_name → (weight, [keyword list])
# Weight 2 = strong signal, Weight 1 = supporting evidence
CATEGORY_RULES: dict[str, list[tuple[int, list[str]]]] = {
    "Income": [
        (2, ["salary", "payroll", "direct deposit", "paycheck", "gross pay", "net pay",
             "wage", "earnings", "compensation", "income", "commission", "bonus",
             "dividend", "interest income", "freelance payment"]),
        (1, ["deposit", "credit", "transfer in", "refund"]),
    ],
    "Utilities": [
        (2, ["electric", "electricity", "water bill", "gas bill", "internet bill",
             "phone bill", "cable bill", "utility", "utilities", "sewage",
             "xfinity", "comcast", "at&t", "verizon", "spectrum", "con edison",
             "national grid", "pge", "duke energy"]),
        (1, ["monthly service", "account number", "meter reading", "kwh", "therms"]),
    ],
    "Food": [
        (2, ["restaurant", "grocery", "supermarket", "food", "dining", "meal",
             "doordash", "ubereats", "grubhub", "instacart", "whole foods",
             "trader joe", "walmart grocery", "kroger", "safeway", "aldi",
             "mcdonald", "starbucks", "chipotle", "pizza", "cafe", "bakery"]),
        (1, ["order", "subtotal", "tip", "delivery fee", "dine in", "take out"]),
    ],
    "Medical": [
        (2, ["hospital", "clinic", "pharmacy", "prescription", "medical", "dental",
             "vision", "doctor", "physician", "healthcare", "health care",
             "cvs", "walgreens", "rite aid", "lab results", "copay", "co-pay",
             "deductible", "urgent care", "emergency room", "radiology"]),
        (1, ["patient", "diagnosis", "treatment", "procedure", "insurance claim"]),
    ],
    "Insurance": [
        (2, ["insurance premium", "policy", "auto insurance", "car insurance",
             "home insurance", "renters insurance", "life insurance", "geico",
             "progressive", "state farm", "allstate", "liberty mutual", "usaa",
             "policy number", "coverage", "premium due"]),
        (1, ["renewal", "effective date", "expiration date", "underwriter"]),
    ],
    "Tax": [
        (2, ["irs", "tax return", "form w-2", "form 1099", "schedule", "taxable income",
             "federal tax", "state tax", "withholding", "tax refund", "tax payment",
             "estimated tax", "turbotax", "h&r block", "tax preparation"]),
        (1, ["adjusted gross income", "deduction", "exemption", "filing status"]),
    ],
    "Rent": [
        (2, ["rent", "lease", "landlord", "tenant", "property management",
             "monthly rent", "rent payment", "apartment", "housing payment",
             "mortgage", "hoa", "homeowners association", "rental agreement"]),
        (1, ["unit", "suite", "sq ft", "security deposit", "move in", "move out"]),
    ],
    "Travel": [
        (2, ["airline", "hotel", "airbnb", "vrbo", "flight", "uber", "lyft",
             "rental car", "hertz", "enterprise", "expedia", "booking.com",
             "delta", "american airlines", "united", "southwest", "marriott",
             "hilton", "hyatt", "toll", "parking"]),
        (1, ["itinerary", "reservation", "check-in", "check-out", "boarding pass"]),
    ],
    "Shopping": [
        (2, ["amazon", "walmart", "target", "best buy", "costco", "ebay", "etsy",
             "online order", "purchase", "merchandise", "retail", "clothing",
             "apparel", "electronics", "home depot", "lowe's", "ikea"]),
        (1, ["order number", "tracking", "shipped", "delivered", "return policy"]),
    ],
    "Subscription": [
        (2, ["netflix", "spotify", "apple music", "amazon prime", "hulu",
             "disney+", "youtube premium", "adobe", "microsoft 365", "dropbox",
             "subscription", "monthly membership", "annual renewal", "auto-renew"]),
        (1, ["billed monthly", "billed annually", "next billing date", "cancel anytime"]),
    ],
}

# Categories that indicate money coming IN vs going OUT
INCOME_CATEGORIES = {"Income"}
EXPENSE_CATEGORIES = set(CATEGORY_RULES.keys()) - INCOME_CATEGORIES


class Categorizer:
    """Keyword-based financial document categorizer."""

    def categorize(self, text: str) -> dict[str, Any]:
        """
        Analyse the OCR text and return the best-matching category.

        Returns:
            {
                "category": str,
                "confidence": float,        # 0.0–1.0
                "is_income": bool,
                "all_scores": {category: score, ...}   # for debugging
            }
        """
        if not text or not text.strip():
            return {
                "category": "Other",
                "confidence": 0.0,
                "is_income": False,
                "all_scores": {},
            }

        text_lower = text.lower()
        scores: dict[str, float] = {}

        for category, rule_groups in CATEGORY_RULES.items():
            raw_score = 0
            matched_count = 0
            for weight, keywords in rule_groups:
                for kw in keywords:
                    if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                        raw_score += weight
                        matched_count += 1
            scores[category] = raw_score

        if not any(scores.values()):
            return {
                "category": "Other",
                "confidence": 0.0,
                "is_income": False,
                "all_scores": scores,
            }

        best_category = max(scores, key=lambda k: scores[k])
        best_score = scores[best_category]
        total_score = sum(scores.values())

        # Normalise confidence to 0.0–1.0
        confidence = round(best_score / total_score, 4) if total_score > 0 else 0.0

        # Cap confidence at 0.95 — model is not perfect
        confidence = min(confidence, 0.95)

        logger.debug(
            "Category: %s (confidence=%.2f, score=%d/%d)",
            best_category, confidence, best_score, total_score,
        )

        return {
            "category": best_category,
            "confidence": confidence,
            "is_income": best_category in INCOME_CATEGORIES,
            "all_scores": scores,
        }

    def categorize_batch(self, texts: list[str]) -> list[dict[str, Any]]:
        """Categorise a list of OCR texts in one call."""
        return [self.categorize(t) for t in texts]
