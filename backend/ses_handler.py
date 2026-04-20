"""
FinSight - SES Email Handler
Sends notification emails via AWS SES (us-east-1).
Both sender and recipient must be verified in SES Sandbox mode.
Set SES_SENDER_EMAIL env var to the verified sender address.
"""

import os
import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class SESHandler:
    def __init__(self):
        self.client = boto3.client("ses", region_name=os.getenv("AWS_REGION", "us-east-1"))
        self.sender = os.getenv("SES_SENDER_EMAIL", "")

    def _send(self, recipient: str, subject: str, body_html: str, body_text: str) -> bool:
        if not self.sender:
            logger.warning("SES_SENDER_EMAIL not set — skipping email")
            return False
        if not recipient:
            logger.warning("No recipient email — skipping SES send")
            return False
        try:
            self.client.send_email(
                Source=self.sender,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": body_html, "Charset": "UTF-8"},
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                    },
                },
            )
            logger.info("SES email sent to %s: %s", recipient, subject)
            return True
        except ClientError as exc:
            logger.warning("SES send failed: %s", exc.response["Error"]["Message"])
            return False

    def send_upload_notification(
        self,
        recipient: str,
        username: str,
        doc_info: dict,
    ) -> bool:
        filename = doc_info.get("filename", "–")
        category = doc_info.get("category", "–")
        amount   = doc_info.get("amount", "–")
        date     = doc_info.get("date", "–")

        subject = f"FinSight: Document processed — {filename}"

        body_html = f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#333;">
          <h2 style="color:#6c63ff;margin-bottom:4px;">FinSight</h2>
          <p style="color:#888;margin-top:0;">Financial Document Intelligence</p>
          <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
          <p>Hi <strong>{username}</strong>, your document has been processed successfully.</p>
          <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px;">
            <tr style="background:#f9f9f9;">
              <td style="padding:10px 14px;font-weight:600;width:140px;">File</td>
              <td style="padding:10px 14px;">{filename}</td>
            </tr>
            <tr>
              <td style="padding:10px 14px;font-weight:600;">Category</td>
              <td style="padding:10px 14px;">{category}</td>
            </tr>
            <tr style="background:#f9f9f9;">
              <td style="padding:10px 14px;font-weight:600;">Amount</td>
              <td style="padding:10px 14px;">{amount}</td>
            </tr>
            <tr>
              <td style="padding:10px 14px;font-weight:600;">Document Date</td>
              <td style="padding:10px 14px;">{date}</td>
            </tr>
          </table>
          <p style="font-size:12px;color:#aaa;margin-top:24px;">
            You are receiving this because email notifications are enabled on your FinSight account.
          </p>
        </body></html>
        """

        body_text = (
            f"FinSight — Document Processed\n\n"
            f"Hi {username},\n\n"
            f"File: {filename}\nCategory: {category}\nAmount: {amount}\nDate: {date}\n"
        )

        return self._send(recipient, subject, body_html, body_text)

    def send_weekly_digest(
        self,
        recipient: str,
        username: str,
        summary: dict,
    ) -> bool:
        income   = summary.get("total_income", 0)
        expenses = summary.get("total_expenses", 0)
        net      = income - expenses
        docs     = summary.get("total_docs", 0)

        subject = "FinSight Weekly Digest"

        rows = ""
        for cat, amt in summary.get("by_category", {}).items():
            rows += f"<tr><td style='padding:8px 14px;'>{cat}</td><td style='padding:8px 14px;text-align:right;'>${amt:,.2f}</td></tr>"

        body_html = f"""
        <html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#333;">
          <h2 style="color:#6c63ff;">FinSight Weekly Digest</h2>
          <p>Hi <strong>{username}</strong>, here's your financial summary for this week.</p>
          <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:14px;">
            <tr style="background:#f0f0ff;">
              <td style="padding:10px 14px;font-weight:600;">Total Income</td>
              <td style="padding:10px 14px;text-align:right;color:#4ecca3;">${income:,.2f}</td>
            </tr>
            <tr>
              <td style="padding:10px 14px;font-weight:600;">Total Expenses</td>
              <td style="padding:10px 14px;text-align:right;color:#ff6b6b;">${expenses:,.2f}</td>
            </tr>
            <tr style="background:#f0f0ff;">
              <td style="padding:10px 14px;font-weight:600;">Net Cash Flow</td>
              <td style="padding:10px 14px;text-align:right;font-weight:700;color:{'#4ecca3' if net >= 0 else '#ff6b6b'};">${net:,.2f}</td>
            </tr>
            <tr>
              <td style="padding:10px 14px;font-weight:600;">Documents Processed</td>
              <td style="padding:10px 14px;text-align:right;">{docs}</td>
            </tr>
          </table>
          {"<h3 style='margin-top:24px;'>Category Breakdown</h3><table style='width:100%;border-collapse:collapse;font-size:13px;'>" + rows + "</table>" if rows else ""}
          <p style="font-size:12px;color:#aaa;margin-top:24px;">FinSight — Financial Document Intelligence</p>
        </body></html>
        """

        body_text = (
            f"FinSight Weekly Digest\n\nHi {username},\n\n"
            f"Income: ${income:,.2f}\nExpenses: ${expenses:,.2f}\nNet: ${net:,.2f}\nDocs: {docs}\n"
        )

        return self._send(recipient, subject, body_html, body_text)
