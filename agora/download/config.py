"""Download configuration and S3 client setup."""

import os
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# S3 flat files configuration.
#
# The access key ID is per-account (visible in your Massive dashboard
# under Flat Files credentials); the secret access key is the same
# value as your REST API key. Both are loaded from env so the codebase
# can be checked into version control without leaking credentials.
S3_ACCESS_KEY_ID = os.getenv("MASSIVE_S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.getenv("MASSIVE_API_KEY")
S3_ENDPOINT = os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com")
S3_BUCKET = os.getenv("MASSIVE_S3_BUCKET", "flatfiles")

# Default data output directory
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Rate limit for REST API (calls per minute)
REST_RATE_LIMIT = 5


def get_s3_client():
    """Create a boto3 S3 client configured for Massive flat files."""
    session = boto3.Session(
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
    )
    return session.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        config=Config(signature_version="s3v4"),
    )