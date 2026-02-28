#!/usr/bin/env bash
# Setup and run MinIO server: install if missing, ensure >= 1G storage, then start.
# Usage: ./utilities/setup-minio.sh [data-dir]
#   data-dir defaults to repo-root/minio-data (at least 1G free required on that filesystem).
# Env: MINIO_ROOT_USER, MINIO_ROOT_PASSWORD (defaults: minioadmin/minioadmin).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: $0 [data-dir]"
  echo "  data-dir  optional; default: $REPO_ROOT/minio-data"
  echo "  Requires >= 1G free on the filesystem containing data-dir."
  exit 0
fi

DATA_DIR="${1:-$REPO_ROOT/minio-data}"
MINIO_BIN=""
MINIO_VERSION_URL="https://dl.min.io/server/minio/release"

# --- Check or install MinIO ---
find_minio() {
  if command -v minio &>/dev/null; then
    MINIO_BIN="minio"
    return
  fi
  local local_bin="$SCRIPT_DIR/minio"
  if [[ -x "$local_bin" ]]; then
    MINIO_BIN="$local_bin"
    return
  fi
  return 1
}

install_minio() {
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) echo "Unsupported arch: $arch" >&2; exit 1 ;;
  esac
  local url="$MINIO_VERSION_URL/linux-${arch}/minio"
  echo "Installing MinIO from $url into $SCRIPT_DIR"
  if command -v curl &>/dev/null; then
    curl -sfSL -o "$SCRIPT_DIR/minio" "$url"
  elif command -v wget &>/dev/null; then
    wget -q -O "$SCRIPT_DIR/minio" "$url"
  else
    echo "Need curl or wget to download MinIO" >&2
    exit 1
  fi
  chmod +x "$SCRIPT_DIR/minio"
  MINIO_BIN="$SCRIPT_DIR/minio"
}

if ! find_minio; then
  install_minio
fi
echo "Using MinIO: $MINIO_BIN"
"$MINIO_BIN" --version

# --- Ensure data dir and >= 1G free ---
mkdir -p "$DATA_DIR"
DATA_DIR="$(cd "$DATA_DIR" && pwd)"
FREE_KB="$(df -k "$DATA_DIR" | awk 'NR==2 {print $4}')"
REQUIRED_KB=$((1024 * 1024))  # 1G
if [[ -z "$FREE_KB" ]] || [[ "$FREE_KB" -lt "$REQUIRED_KB" ]]; then
  echo "Not enough free space in $DATA_DIR (have ${FREE_KB:-0} KB, need $REQUIRED_KB KB for 1G)" >&2
  exit 1
fi
echo "Data directory: $DATA_DIR (>= 1G free)"

# --- Credentials (required by MinIO) ---
export MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
export MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"

echo "Starting MinIO server (root user: $MINIO_ROOT_USER)"
exec "$MINIO_BIN" server "$DATA_DIR"
