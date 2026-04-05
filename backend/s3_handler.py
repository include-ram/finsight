"""
FinSight - S3 Handler
Wraps all S3 operations: upload, download, presigned URLs.
Uses boto3 without explicit credentials — relies on EC2 LabRole instance profile.
"""

import os
import logging
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "finsight-documents")


class S3Handler:
    """
    S3 operations helper.
    boto3 automatically picks up credentials from the EC2 instance's
    LabRole IAM profile — no keys are stored in code or environment.
    """

    def __init__(self):
        self._client = boto3.client("s3", region_name=AWS_REGION)
        self._bucket = S3_BUCKET
        logger.info("S3Handler initialised for bucket '%s' in %s", self._bucket, AWS_REGION)

    # ── Upload ────────────────────────────────────────────────────────────────
    def upload_file(self, local_path: str, s3_key: str, content_type: str = "application/octet-stream") -> str:
        """
        Upload a file from a local path to S3.
        Returns the S3 URI (s3://bucket/key).
        """
        try:
            self._client.upload_file(
                local_path,
                self._bucket,
                s3_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ServerSideEncryption": "AES256",   # encrypt at rest
                },
            )
            s3_uri = f"s3://{self._bucket}/{s3_key}"
            logger.info("Uploaded %s → %s", local_path, s3_uri)
            return s3_uri
        except NoCredentialsError:
            logger.error("No AWS credentials found. Is the LabRole attached to this EC2?")
            raise
        except ClientError as exc:
            logger.error("S3 upload failed for %s: %s", local_path, exc)
            raise

    def upload_bytes(
        self,
        data: bytes,
        s3_key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Upload raw bytes to S3 (used when we already have the file in memory).
        Returns the S3 URI.
        """
        import io
        try:
            self._client.upload_fileobj(
                io.BytesIO(data),
                self._bucket,
                s3_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ServerSideEncryption": "AES256",
                },
            )
            s3_uri = f"s3://{self._bucket}/{s3_key}"
            logger.info("Uploaded %d bytes → %s", len(data), s3_uri)
            return s3_uri
        except ClientError as exc:
            logger.error("S3 bytes upload failed for key %s: %s", s3_key, exc)
            raise

    def upload_fileobj(
        self,
        fileobj: BinaryIO,
        s3_key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file-like object to S3. Returns the S3 URI."""
        try:
            self._client.upload_fileobj(
                fileobj,
                self._bucket,
                s3_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ServerSideEncryption": "AES256",
                },
            )
            return f"s3://{self._bucket}/{s3_key}"
        except ClientError as exc:
            logger.error("S3 fileobj upload failed: %s", exc)
            raise

    # ── Download ──────────────────────────────────────────────────────────────
    def download_file(self, s3_key: str, local_path: str) -> None:
        """
        Download an S3 object to a local file path.
        Creates parent directories if they don't exist.
        """
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self._bucket, s3_key, local_path)
            logger.info("Downloaded s3://%s/%s → %s", self._bucket, s3_key, local_path)
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "404":
                raise FileNotFoundError(
                    f"S3 object not found: s3://{self._bucket}/{s3_key}"
                ) from exc
            logger.error("S3 download failed for key %s: %s", s3_key, exc)
            raise

    def download_bytes(self, s3_key: str) -> bytes:
        """Download an S3 object and return it as raw bytes."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=s3_key)
            data = response["Body"].read()
            logger.info("Downloaded %d bytes from s3://%s/%s", len(data), self._bucket, s3_key)
            return data
        except ClientError as exc:
            logger.error("S3 get_object failed for key %s: %s", s3_key, exc)
            raise

    # ── Presigned URLs ────────────────────────────────────────────────────────
    def get_presigned_url(self, s3_key: str, expiry_seconds: int = 3600) -> str:
        """
        Generate a presigned GET URL so the frontend can view the original file
        without making the S3 bucket public.
        Default expiry: 1 hour.
        """
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": s3_key},
                ExpiresIn=expiry_seconds,
            )
            return url
        except ClientError as exc:
            logger.error("Could not generate presigned URL for %s: %s", s3_key, exc)
            raise

    # ── Object existence check ────────────────────────────────────────────────
    def object_exists(self, s3_key: str) -> bool:
        """Return True if the key exists in the bucket."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=s3_key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise

    # ── List objects ──────────────────────────────────────────────────────────
    def list_objects(self, prefix: str = "uploads/") -> list[dict]:
        """
        List objects under a prefix.
        Returns a list of dicts with key, size, last_modified.
        """
        results = []
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    results.append({
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    })
        except ClientError as exc:
            logger.error("S3 list failed for prefix %s: %s", prefix, exc)
            raise
        return results

    # ── Delete ────────────────────────────────────────────────────────────────
    def delete_object(self, s3_key: str) -> None:
        """Delete a single object from S3."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=s3_key)
            logger.info("Deleted s3://%s/%s", self._bucket, s3_key)
        except ClientError as exc:
            logger.error("S3 delete failed for key %s: %s", s3_key, exc)
            raise
