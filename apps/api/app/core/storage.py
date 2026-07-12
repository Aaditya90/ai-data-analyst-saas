"""
Thin wrapper around boto3 for the one thing this phase needs: storing
uploaded files and reading them back for parsing. Talks to MinIO locally
and to real S3 in production — same code path, since MinIO is
S3-API-compatible (that's the whole reason it's in docker-compose.yml).
"""

import io
import uuid
from functools import lru_cache

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import get_settings


@lru_cache
def get_s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


def ensure_bucket_exists() -> None:
    """Idempotent — safe to call on every startup/upload. MinIO doesn't
    auto-create buckets, and neither does real S3."""
    settings = get_settings()
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket_name)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket_name)


def build_storage_key(workspace_id: uuid.UUID, dataset_id: uuid.UUID, filename: str) -> str:
    """Keys are namespaced by workspace then dataset — mirrors the DB
    isolation model so a storage-layer bug can't leak another workspace's
    files as easily (defense in depth, not a substitute for the DB check)."""
    safe_name = filename.replace("/", "_").replace("\\", "_")
    return f"workspaces/{workspace_id}/datasets/{dataset_id}/{uuid.uuid4()}_{safe_name}"


def upload_bytes(key: str, data: bytes) -> None:
    settings = get_settings()
    ensure_bucket_exists()
    get_s3_client().put_object(Bucket=settings.s3_bucket_name, Key=key, Body=data)


def download_bytes(key: str) -> bytes:
    settings = get_settings()
    obj = get_s3_client().get_object(Bucket=settings.s3_bucket_name, Key=key)
    return obj["Body"].read()


def download_to_buffer(key: str) -> io.BytesIO:
    return io.BytesIO(download_bytes(key))
