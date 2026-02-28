"""
MinIO-backed volume: read/write/flush via S3-compatible API (e.g. local MinIO).
Chunked layout: exports/<export_name>/meta.json, exports/<export_name>/lock, exports/<export_name>/chunks/<index>.
Requires boto3 (use .venv with boto3 when running the server with MinIO).

Lock: one lock object per export in MinIO. If lock exists, open fails (volume in use). Same lock visible to all server instances.
"""
import json
from typing import Optional

try:
    import boto3
    from botocore.client import Config
    from botocore.exceptions import ClientError
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB


def _meta_key(export_name: str) -> str:
    return f"exports/{export_name}/meta.json"


def _chunk_key(export_name: str, chunk_index: int) -> str:
    return f"exports/{export_name}/chunks/{chunk_index}"


def _lock_key(export_name: str) -> str:
    return f"exports/{export_name}/lock"


class MinIOVolume:
    """
    Volume backed by MinIO (S3-compatible). Chunked storage; read from MinIO,
    write goes to dirty cache, flush uploads dirty chunks to MinIO.
    Same interface as Volume: size_bytes, transmission_flags, read, write, flush.
    """

    def __init__(
        self,
        client: object,
        bucket: str,
        export_name: str,
        size: int,
        chunk_size: int,
        flags: int,
    ):
        self._client = client
        self._bucket = bucket
        self._export_name = export_name
        self._lock_key = _lock_key(export_name)
        self._size = size
        self._chunk_size = chunk_size
        self._flags = flags
        self._dirty: dict[int, bytes] = {}  # chunk_index -> full chunk bytes

    @property
    def size_bytes(self) -> int:
        return self._size

    @property
    def transmission_flags(self) -> int:
        return self._flags

    def _get_chunk(self, chunk_index: int) -> bytes:
        """Load one chunk from dirty cache or MinIO; missing = zeros."""
        if chunk_index in self._dirty:
            return self._dirty[chunk_index]
        key = _chunk_key(self._export_name, chunk_index)
        try:
            r = self._client.get_object(Bucket=self._bucket, Key=key)
            data = r["Body"].read()
            if len(data) < self._chunk_size:
                data = data + b"\x00" * (self._chunk_size - len(data))
            elif len(data) > self._chunk_size:
                data = data[: self._chunk_size]
            return data
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return b"\x00" * self._chunk_size
            raise

    def read(self, offset: int, length: int) -> tuple[int, bytes]:
        """Return (error_code, data). 0 = success."""
        if offset + length > self._size:
            return (5, b"")
        result = bytearray(length)
        pos = 0
        while pos < length:
            abs_offset = offset + pos
            chunk_index = abs_offset // self._chunk_size
            off_in_chunk = abs_offset % self._chunk_size
            to_read = min(length - pos, self._chunk_size - off_in_chunk)
            chunk = self._get_chunk(chunk_index)
            result[pos : pos + to_read] = chunk[off_in_chunk : off_in_chunk + to_read]
            pos += to_read
        return (0, bytes(result))

    def write(self, offset: int, data: bytes) -> int:
        """Return NBD error code. 0 = success."""
        if offset + len(data) > self._size:
            return 5
        pos = 0
        while pos < len(data):
            abs_offset = offset + pos
            chunk_index = abs_offset // self._chunk_size
            off_in_chunk = abs_offset % self._chunk_size
            to_write = min(len(data) - pos, self._chunk_size - off_in_chunk)
            chunk = self._get_chunk(chunk_index)
            chunk_arr = bytearray(chunk)
            chunk_arr[off_in_chunk : off_in_chunk + to_write] = data[pos : pos + to_write]
            self._dirty[chunk_index] = bytes(chunk_arr)
            pos += to_write
        return 0

    def flush(self) -> int:
        """Upload dirty chunks to MinIO. Return 0 on success."""
        try:
            for chunk_index, chunk_data in self._dirty.items():
                key = _chunk_key(self._export_name, chunk_index)
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=chunk_data,
                )
            self._dirty.clear()
            return 0
        except ClientError:
            return 5  # ERR_IO

    def release(self) -> None:
        """Release the volume lock so another client can open it. Call when connection closes."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._lock_key)
        except ClientError:
            pass

    @staticmethod
    def open(
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        export_name: str,
        default_size: int,
        flags: int = 2,
    ) -> Optional["MinIOVolume"]:
        """
        Open or create volume in MinIO. If lock object already exists (any server instance), returns None (volume in use).
        Otherwise creates lock, then loads or creates meta. Returns None if boto3 missing, MinIO unreachable, or volume in use.
        Caller must call volume.release() when the connection closes to delete the lock.
        """
        if not _BOTO3_AVAILABLE:
            return None
        try:
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(signature_version="s3v4"),
                region_name="us-east-1",
            )
            try:
                client.head_bucket(Bucket=bucket)
            except ClientError:
                client.create_bucket(Bucket=bucket)
            lock_key = _lock_key(export_name)
            try:
                client.head_object(Bucket=bucket, Key=lock_key)
            except ClientError as e:
                if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                    return None
            else:
                return None
            client.put_object(Bucket=bucket, Key=lock_key, Body=b"")
            try:
                meta_key = _meta_key(export_name)
                try:
                    r = client.get_object(Bucket=bucket, Key=meta_key)
                    meta = json.loads(r["Body"].read().decode())
                    size = int(meta.get("size_bytes", 0))
                    chunk_size = int(meta.get("chunk_size", DEFAULT_CHUNK_SIZE))
                except ClientError as e:
                    if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                        raise
                    size = default_size
                    chunk_size = DEFAULT_CHUNK_SIZE
                    client.put_object(
                        Bucket=bucket,
                        Key=meta_key,
                        Body=json.dumps({
                            "size_bytes": size,
                            "chunk_size": chunk_size,
                        }).encode(),
                    )
                return MinIOVolume(
                    client=client,
                    bucket=bucket,
                    export_name=export_name,
                    size=size,
                    chunk_size=chunk_size,
                    flags=flags,
                )
            except Exception:
                try:
                    client.delete_object(Bucket=bucket, Key=lock_key)
                except ClientError:
                    pass
                return None
        except Exception:
            return None
