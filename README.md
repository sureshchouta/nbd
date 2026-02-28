# NBD — Cloud Block Device (Python)

Network block device server: fixed newstyle NBD over TCP, MinIO-backed volumes. Interview-friendly: small codebase, single README, easy to change live.

**Assignment alignment ([Replit take-home: Cloud Block Device](https://www.notion.so/replit/External-Replit-Take-Home-Cloud-Block-Device-277f7a820bb180e39c70e9391a7b053a)):** All base requirements are met (NBD handshake, OPT_GO/OPT_ABORT, read/write/flush, arbitrary export names, durable S3 persist, flush semantics). Optional item implemented: *Prevent multiple servers from writing to the same disk in S3 at the same time* (lock object in MinIO; second open fails). Concurrent clients are not required by the assignment; the “concurrent read/write” idea is not implemented (one client per export).

---

## Design

The design below describes exactly what is implemented (no speculative features).

### Goals

- NBD server with handshake, READ, WRITE, FLUSH.
- Arbitrary export names; each export is one volume.
- Backend: MinIO only (S3-compatible), chunked storage, one client per export enforced by a lock object in MinIO.

### NBD protocol

- **Fixed newstyle handshake:** server sends magic (NBDMAGIC, IHAVEOPT) + flags (FIXED_NEWSTYLE); client sends flags (4 bytes).
- **Options:**  
  - `NBD_OPT_GO(export_name)` — select export and enter transmission. Server opens volume (see Storage); if open fails (e.g. volume in use), reply REP_ERR_UNKNOWN; else send export info (size, flags) + REP_ACK and enter transmission.  
  - `NBD_OPT_ABORT` — reply REP_ACK and close connection.  
  - Any other option — reply REP_ERR_UNSUP.
- **Transmission:** one TCP connection per client; many commands on the same connection.  
  - READ(offset, length) → simple reply (error, cookie, data).  
  - WRITE(offset, data) → simple reply (error, cookie).  
  - FLUSH() → simple reply (error, cookie).  
  - Unknown command → simple reply ERR_PERM.

### Storage (MinIO)

- **Bucket:** one bucket (env `MINIO_BUCKET`, default `nbd-storage`). All exports live under key prefix `exports/<export_name>/`.
- **Object keys:**
  - `exports/<export_name>/meta.json` — JSON: `size_bytes`, `chunk_size` (default 4 MiB). Created on first open if missing.
  - `exports/<export_name>/lock` — single object, empty body. Created when a client opens the export; deleted when the connection closes (`volume.release()`). If this object exists at open time, open fails (volume in use). Same lock is visible to all server instances using the same bucket.
  - `exports/<export_name>/chunks/<index>` — chunk data; `<index>` is the chunk index (non-negative integer). Missing key reads as zeros. On flush, dirty chunks are uploaded with `put_object` (overwrites same key).
- **Chunking:** Logical offset → chunk index = `offset // chunk_size`; key = `exports/<export_name>/chunks/<index>`. No UUIDs; no manifest; direct index-based keys.
- **Single writer:** No in-memory registry. Open: `head_object(lock)` → if exists return None; else `put_object(lock)`, then load or create meta, return `MinIOVolume`. Disconnect: `volume.release()` → `delete_object(lock)`.

---

## Implementation

**Language:** Python 3. **Build:** Bazel (`rules_python`). **Backend:** MinIO only (no local file backend). The code matches the Design above.

### Layout

```
nbd/
  server/
    main.py         # Entry: argparse, listen, one thread per client; MinIO config from env
    connection.py   # Per-client: handshake → option loop (GO) → transmission loop (READ/WRITE/FLUSH)
    protocol.py     # NBD constants, send_all/recv_all, big-endian pack/unpack
    handlers.py     # Decorator dispatch: @option(OPT_GO), @command(CMD_READ/WRITE/FLUSH)
    payloads.py     # Parse OPT_GO payload (export name)
    volume_minio.py # MinIO-backed volume: chunked read/write/flush
```

### Build and run

```bash
bazel build //nbd/server:server
# MinIO must be running; use .venv with boto3 for MinIO
MINIO_ENDPOINT=http://localhost:9000 bazel run //nbd/server:server -- -p 10809 -s 16777216
```

**Server flags:** `-p` port (default 10809), `-s` default export size in bytes (default 16 MiB). MinIO env: `MINIO_ENDPOINT` (default http://localhost:9000), `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (default nbd-storage).

### What each file does

| File | Role |
|------|------|
| **main.py** | Parse args, build MinIO config from env (defaults for local MinIO), create listen socket, accept loop; each client runs in a daemon thread that calls `handle_connection(sock, default_size, minio_config)`. |
| **connection.py** | `handle_connection`: send handshake, recv client flags; option loop until NBD_OPT_GO (parse export name, open MinIO volume via lock in MinIO—fails if lock exists—send export info + ack); transmission loop: recv request → dispatch READ/WRITE/FLUSH → send reply. On disconnect, calls `volume.release()` to delete the lock. |
| **protocol.py** | NBD constants (magics, option/reply/command codes, errors); `send_all`/`recv_all`; `pack_be*`/`unpack_be*` for wire format. |
| **handlers.py** | `OptionContext` (default_export_size, minio_config, volume); `@option`/`@command` decorators; OPT_GO and READ/WRITE/FLUSH handlers. |
| **payloads.py** | `parse_opt_go(payload)` → export name (or None). |
| **volume_minio.py** | `MinIOVolume`: open (lock in MinIO, meta.json, chunks by index), read/write/flush, release (delete lock). Chunk keys `exports/<name>/chunks/<index>`. Dirty cache; flush overwrites chunk objects. Requires boto3. |

### MinIO backend

As in Design (Storage): one bucket; keys `exports/<name>/meta.json`, `exports/<name>/lock`, `exports/<name>/chunks/<index>`. Lock created on open, deleted on disconnect. Env: `MINIO_ENDPOINT` (default `http://localhost:9000`), `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` (default `nbd-storage`). Run MinIO with `./utilities/setup-minio.sh`, create the bucket (e.g. `utilities/minio-create-bucket-and-chunks.py`), then start the server with boto3 available (e.g. `.venv/bin/python` or install boto3 for Bazel).

### Data flow

1. **main.py** — Listen, accept; per client: thread → `handle_connection(sock, default_export_size, minio_config)`.
2. **connection.py** — Handshake → option loop. On NBD_OPT_GO: `MinIOVolume.open(...)` (lock in MinIO; if lock exists, open returns None → send REP_ERR_UNKNOWN); else send export info + ack → transmission loop: recv request → dispatch READ/WRITE/FLUSH to volume → send simple reply. On disconnect (finally): `volume.release()` → delete lock in MinIO.
3. **volume_minio.py** — Open: check lock, create lock, load/create meta. Read: from dirty cache or MinIO (key `chunks/<index>`); missing key → zeros. Write: to dirty cache. Flush: put_object for each dirty chunk (key `chunks/<index>`). Release: delete_object(lock).

### Making changes in an interview

- **Add a new NBD option:** Register a handler with `@option(opt_id)` in `connection.py` and add the reply logic.
- **Change backend (e.g. different S3 layout):** Adjust `MinIOVolume` in `volume_minio.py`; keep the same `read`/`write`/`flush` interface so `connection.py` stays unchanged.
- **Add a command (e.g. TRIM):** In `protocol.py` add the command constant; register `@command(cmd_id)` in `connection.py` and send the appropriate reply.
- **Tighten error handling:** Return proper NBD errors from volume read/write/flush and ensure `connection.py` sends them in the simple reply.

### Testing the server (no custom client required)

You **do not** need to implement an NBD client to create or use the filesystem. The Linux kernel has a built-in NBD client. Use the `nbd-client` CLI (or `nbdctl` where available) to connect to this server; the kernel exposes a block device (e.g. `/dev/nbd3`) that you can format with `mkfs` and mount.

1. Start MinIO and the NBD server (see Build and run).
2. Connect using the kernel NBD client, e.g.  
   `sudo nbd-client localhost 10809 -N <export_name>`  
   (or `nbdctl -C -a localhost -p 10809 -n <export_name>`). The tool prints the device path (e.g. `/dev/nbd3`).
3. Format and mount:  
   `sudo mkfs.ext4 /dev/nbd3`  
   `sudo mount /dev/nbd3 /mnt`  
   Then read/write files under `/mnt`.
4. To test persistence: unmount (`sudo umount /mnt`), disconnect NBD (`sudo nbd-client -d /dev/nbd3`), restart the server, connect again with the same export name, mount again—the filesystem contents should still be there.
5. Disconnect when done: `sudo nbd-client -d /dev/nbdX` (or `nbdctl -d /dev/nbdX`).

---

## Interview talking points

1. **One connection, many commands** — One TCP connection per client; handshake once, then NBD_OPT_GO once per export, then a loop of READ/WRITE/FLUSH on the same socket.
2. **Wire format** — NBD is big-endian; `protocol.py` uses `struct.pack(">Q", ...)` etc.; fixed-size headers and length-prefixed payloads.
3. **Concurrency** — One thread per client. At most one client per export: lock object in MinIO (`exports/<name>/lock`); created on open, deleted on disconnect; visible to all server instances.
4. **Design = implementation** — The Design section describes only what is implemented (NBD protocol, MinIO layout with meta.json + lock + chunks by index, no manifests or UUIDs in keys).
