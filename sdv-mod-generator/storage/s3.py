"""Storage layer — S3 with local filesystem fallback.

Local mode is used when AWS_ACCESS_KEY_ID is not set.
Zip files are written to LOCAL_OUTPUT_DIR and served as file:// URLs.
"""
import os
import shutil
from pathlib import Path

import boto3
import structlog

logger = structlog.get_logger()

_BUCKET = os.getenv("S3_BUCKET", "sdv-mod-generator")
_REGION = os.getenv("S3_REGION", "us-east-1")
_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "")
_AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
_AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
_LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "/tmp/sdv-mod-generator/outputs")
_PUBLIC_URL_TEMPLATE = os.getenv("S3_PUBLIC_URL", "")

_use_local = False
_client = None


def _is_local_mode() -> bool:
    global _use_local
    if _use_local:
        return True
    if not _AWS_ACCESS_KEY_ID:
        _use_local = True
        logger.info("storage.s3.mode", mode="local", reason="no_aws_credentials")
        return True
    if _ENDPOINT_URL and "localhost" in _ENDPOINT_URL:
        _use_local = True
        logger.info("storage.s3.mode", mode="local", reason="localhost_endpoint")
        return True
    return False


def get_client():
    global _client
    if _client is not None:
        return _client
    if _is_local_mode():
        return None
    logger.info("storage.s3.connected", mode="aws", bucket=_BUCKET, region=_REGION)
    _client = boto3.client("s3", region_name=_REGION)
    return _client


def _local_path(zip_key: str) -> Path:
    return Path(_LOCAL_OUTPUT_DIR) / zip_key


def _make_url(zip_key: str) -> str:
    if _PUBLIC_URL_TEMPLATE:
        return _PUBLIC_URL_TEMPLATE.format(bucket=_BUCKET, key=zip_key)
    if _ENDPOINT_URL:
        return f"{_ENDPOINT_URL.rstrip('/')}/{_BUCKET}/{zip_key}"
    return f"https://{_BUCKET}.s3.{_REGION}.amazonaws.com/{zip_key}"


def upload_zip(zip_key: str, zip_path: str) -> str:
    """Upload zip to S3 or local filesystem (sync — call from thread pool if needed)."""
    if _is_local_mode():
        dest = _local_path(zip_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(zip_path, dest)
        url = f"file://{dest}"
        logger.info("storage.s3.upload_done", zip_key=zip_key, url=url, mode="local")
        return url

    client = get_client()
    client.upload_file(zip_path, _BUCKET, zip_key)
    url = _make_url(zip_key)
    logger.info("storage.s3.upload_done", zip_key=zip_key, url=url)
    return url


def download_zip(zip_key: str, dest_path: str) -> None:
    """Download zip from S3 or local filesystem."""
    if _is_local_mode():
        src = _local_path(zip_key)
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)
        logger.info("storage.s3.download_done", zip_key=zip_key, dest_path=dest_path, mode="local")
        return

    client = get_client()
    client.download_file(_BUCKET, zip_key, dest_path)
    logger.info("storage.s3.download_done", zip_key=zip_key, dest_path=dest_path)
