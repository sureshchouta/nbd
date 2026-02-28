#!/usr/bin/env python3
"""
Use the local MinIO instance (from setup-minio.sh) to create a bucket,
upload a few chunk objects, and list them.

Requires boto3. On Debian/Ubuntu the system Python is externally managed,
so install and run inside a virtual environment.

  Prerequisite (one-time; if venv creation fails with "ensurepip is not available"):

    sudo apt install python3.12-venv

  Installation (one-time, from repo root):

    cd /home/suresh/git/nbd/nbd
    python3 -m venv .venv
    .venv/bin/pip install boto3

  Run (with MinIO server already running, e.g. ./utilities/setup-minio.sh):

    .venv/bin/python utilities/minio-create-bucket-and-chunks.py

  Do not use system python3 (e.g. python3 utilities/...); use .venv/bin/python
  so the script sees the venv's installed boto3.

Default endpoint: http://localhost:9000, user minioadmin, password minioadmin.
Override with env: MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET.
"""
import os
import sys

try:
    import boto3
    from botocore.client import Config
    from botocore.exceptions import ClientError
except ImportError:
    print("boto3 not found. Run with the venv Python, not system python3:", file=sys.stderr)
    print("  .venv/bin/python utilities/minio-create-bucket-and-chunks.py", file=sys.stderr)
    print("If the venv is missing or boto3 not installed there:", file=sys.stderr)
    print("  .venv/bin/pip install boto3", file=sys.stderr)
    sys.exit(1)

ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
BUCKET = os.environ.get("MINIO_BUCKET", "nbd-storage")


def main() -> int:
    client = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

    # 1. Create bucket
    print(f"Creating bucket: {BUCKET}")
    try:
        client.create_bucket(Bucket=BUCKET)
        print("  done")
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
        print("  (already exists)")

    # 2. Upload a few "chunks" (small objects under a prefix, e.g. exports/vol1/chunks/)
    prefix = "exports/vol1/chunks/"
    chunks = [
        (f"{prefix}0-chunk1", b"fake chunk 0 data " * 64),   # 1 KB
        (f"{prefix}1-chunk2", b"fake chunk 1 data " * 64),
        (f"{prefix}2-chunk3", b"fake chunk 2 data " * 64),
    ]
    print("Uploading chunks:")
    for key, data in chunks:
        client.put_object(Bucket=BUCKET, Key=key, Body=data)
        print(f"  {key} ({len(data)} bytes)")

    # 3. List objects under the prefix
    print(f"Listing objects under {prefix}:")
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents") or []:
            print(f"  {obj['Key']}  size={obj['Size']}  etag={obj.get('ETag', '')}")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
