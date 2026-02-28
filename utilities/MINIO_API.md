# Using the MinIO instance (after setup-minio.sh)

MinIO runs at **http://localhost:9000** with user **minioadmin** / password **minioadmin**.

---

## 1. Python (boto3) — create bucket, upload chunks, list

On Debian/Ubuntu the system Python is externally managed, so use a venv:

```bash
cd /path/to/repo
python3 -m venv .venv
.venv/bin/pip install boto3
.venv/bin/python utilities/minio-create-bucket-and-chunks.py
```

Optional env: `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (default bucket: `nbd-storage`).

The script creates bucket `nbd-storage`, uploads three objects under `exports/vol1/chunks/`, then lists them.

---

## 2. AWS CLI (s3api)

Configure endpoint and credentials, then use standard S3 commands:

```bash
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin

# Create bucket
aws --endpoint-url http://localhost:9000 s3 mb s3://nbd-storage

# Upload chunks (create a small file first)
echo "chunk 0 data" > /tmp/chunk0
echo "chunk 1 data" > /tmp/chunk1
aws --endpoint-url http://localhost:9000 s3 cp /tmp/chunk0 s3://nbd-storage/exports/vol1/chunks/0-chunk1
aws --endpoint-url http://localhost:9000 s3 cp /tmp/chunk1 s3://nbd-storage/exports/vol1/chunks/1-chunk2

# List chunks under prefix
aws --endpoint-url http://localhost:9000 s3 ls s3://nbd-storage/exports/vol1/chunks/
```

---

## 3. MinIO client (mc)

```bash
# One-time: add alias (install mc from https://min.io/docs/minio/linux/reference/minio-mc.html)
mc alias set local http://localhost:9000 minioadmin minioadmin

# Create bucket
mc mb local/nbd-storage

# Upload chunks
echo "chunk 0" | mc pipe local/nbd-storage/exports/vol1/chunks/0-chunk1
echo "chunk 1" | mc pipe local/nbd-storage/exports/vol1/chunks/1-chunk2

# List
mc ls local/nbd-storage/exports/vol1/chunks/
```

---

## 4. curl (create bucket + upload via REST)

MinIO uses the S3 REST API. Creating a bucket and putting objects with raw curl is verbose (signing). Prefer boto3, AWS CLI, or mc for normal use.
