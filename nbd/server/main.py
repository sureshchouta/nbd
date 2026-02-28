"""
NBD server entry point. Listens on TCP, accepts connections, hands each client to handle_connection.
Volumes are stored in MinIO only (chunked). Set MINIO_* env or use defaults (localhost:9000, nbd-storage).
"""
import argparse
import os
import socket
import sys
import threading


def _minio_config() -> dict:
    """MinIO config from env; defaults for local MinIO."""
    endpoint = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000").strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "http://" + endpoint
    return {
        "endpoint_url": endpoint,
        "access_key": os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        "secret_key": os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        "bucket": os.environ.get("MINIO_BUCKET", "nbd-storage"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="NBD server (fixed newstyle, MinIO backend)")
    parser.add_argument("-p", "--port", type=int, default=10809, help="Listen port")
    parser.add_argument("-s", "--size", type=int, default=16 * 1024 * 1024, help="Default export size (bytes)")
    args = parser.parse_args()

    minio_config = _minio_config()
    print(f"NBD server MinIO: {minio_config['endpoint_url']} bucket={minio_config['bucket']}")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", args.port))
    server.listen(16)
    print(f"NBD server listening on 0.0.0.0:{args.port}")

    while True:
        try:
            client_sock, _ = server.accept()
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            default_size = args.size
            mcfg = minio_config
            t = threading.Thread(
                target=lambda: _run_client(client_sock, default_size, mcfg),
                daemon=True,
            )
            t.start()
        except KeyboardInterrupt:
            break
        except OSError as e:
            print(f"accept error: {e}", file=sys.stderr)

    server.close()
    return 0


def _run_client(
    sock: socket.socket,
    default_export_size: int,
    minio_config: dict,
) -> None:
    try:
        from nbd.server.connection import handle_connection
        handle_connection(sock, default_export_size, minio_config)
    finally:
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
