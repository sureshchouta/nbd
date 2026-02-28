"""
Per-client NBD lifecycle: handshake -> option loop (NBD_OPT_GO) -> transmission loop (READ/WRITE/FLUSH).
Uses decorator-registered handlers: @option(OPT_*) and @command(CMD_*) in this module.
Volume in-use is enforced by a lock file in MinIO; second client (or second server instance) sees lock and gets error.
"""
import socket
from typing import Optional

from nbd.server import protocol as proto
from nbd.server.handlers import OptionContext, command, option
from nbd.server.payloads import parse_opt_go
from nbd.server.volume_minio import MinIOVolume


# --- Wire helpers (used by handlers) ---

def _send_handshake(sock: socket.socket) -> bool:
    buf = (
        proto.pack_be64(proto.MAGIC_NBD)
        + proto.pack_be64(proto.MAGIC_IHAVEOPT)
        + proto.pack_be16(proto.FLAG_FIXED_NEWSTYLE)
    )
    return proto.send_all(sock, buf)


def _recv_client_flags(sock: socket.socket) -> Optional[int]:
    buf = proto.recv_all(sock, 4)
    if buf is None:
        return None
    return proto.unpack_be32(buf)


def _recv_option(sock: socket.socket) -> Optional[tuple[int, bytes]]:
    hdr = proto.recv_all(sock, 16)
    if hdr is None or proto.unpack_be64(hdr[:8]) != proto.MAGIC_IHAVEOPT:
        return None
    opt = proto.unpack_be32(hdr[8:12])
    length = proto.unpack_be32(hdr[12:16])
    payload = proto.recv_all(sock, length) if length else b""
    if length and payload is None:
        return None
    return (opt, payload or b"")


def _send_reply_ack(sock: socket.socket, opt: int) -> bool:
    buf = (
        proto.pack_be64(proto.REPLY_MAGIC)
        + proto.pack_be32(opt)
        + proto.pack_be32(proto.REP_ACK)
        + proto.pack_be32(0)
    )
    return proto.send_all(sock, buf)


def _send_reply_err(sock: socket.socket, opt: int, err: int) -> bool:
    buf = (
        proto.pack_be64(proto.REPLY_MAGIC)
        + proto.pack_be32(opt)
        + proto.pack_be32(err)
        + proto.pack_be32(0)
    )
    return proto.send_all(sock, buf)


def _send_info_export(sock: socket.socket, opt: int, size: int, flags: int) -> bool:
    payload_len = 2 + 8 + 2
    buf = (
        proto.pack_be64(proto.REPLY_MAGIC)
        + proto.pack_be32(opt)
        + proto.pack_be32(proto.REP_INFO)
        + proto.pack_be32(payload_len)
        + proto.pack_be16(proto.INFO_EXPORT)
        + proto.pack_be64(size)
        + proto.pack_be16(flags)
    )
    return proto.send_all(sock, buf)


def _send_go_success(sock: socket.socket, opt: int, size: int, flags: int) -> bool:
    return _send_info_export(sock, opt, size, flags) and _send_reply_ack(sock, opt)


def _recv_request(sock: socket.socket) -> Optional[tuple[int, int, int, int, bytes]]:
    buf = proto.recv_all(sock, 28)
    if buf is None or proto.unpack_be32(buf[:4]) != proto.REQUEST_MAGIC:
        return None
    cmd = proto.unpack_be16(buf[6:8])
    cookie = proto.unpack_be64(buf[8:16])
    offset = proto.unpack_be64(buf[16:24])
    length = proto.unpack_be32(buf[24:28])
    write_data = proto.recv_all(sock, length) if cmd == proto.CMD_WRITE and length else b""
    if cmd == proto.CMD_WRITE and length and write_data is None:
        return None
    return (cmd, cookie, offset, length, write_data or b"")


def _send_simple_reply(
    sock: socket.socket, error: int, cookie: int, data: bytes = b""
) -> bool:
    buf = (
        proto.pack_be32(proto.SIMPLE_REPLY_MAGIC)
        + proto.pack_be32(error)
        + proto.pack_be64(cookie)
    )
    if not proto.send_all(sock, buf):
        return False
    if data and not proto.send_all(sock, data):
        return False
    return True


# --- Option handlers (annotations like @app.get in FastAPI) ---

@option(proto.OPT_ABORT)
def _handle_opt_abort(sock: socket.socket, payload: bytes, ctx: OptionContext) -> str:
    _send_reply_ack(sock, proto.OPT_ABORT)
    return "return"


@option(proto.OPT_GO)
def _handle_opt_go(sock: socket.socket, payload: bytes, ctx: OptionContext) -> str:
    parsed = parse_opt_go(payload)
    if parsed is None:
        _send_reply_err(sock, proto.OPT_GO, proto.REP_ERR_INVALID)
        return "return"
    volume = MinIOVolume.open(
        endpoint_url=ctx.minio_config["endpoint_url"],
        access_key=ctx.minio_config["access_key"],
        secret_key=ctx.minio_config["secret_key"],
        bucket=ctx.minio_config["bucket"],
        export_name=parsed.export_name,
        default_size=ctx.default_export_size,
    )
    if volume is None:
        _send_reply_err(sock, proto.OPT_GO, proto.REP_ERR_UNKNOWN)
        return "return"
    if not _send_go_success(sock, proto.OPT_GO, volume.size_bytes, volume.transmission_flags):
        volume.release()
        return "return"
    ctx.volume = volume
    return "break"


# --- Command handlers (transmission phase) ---

@command(proto.CMD_READ)
def _handle_cmd_read(volume: MinIOVolume, sock: socket.socket, cookie: int, offset: int, length: int, write_data: bytes) -> None:
    err, data = volume.read(offset, length)
    _send_simple_reply(sock, err, cookie, data if err == proto.ERR_NONE else b"")


@command(proto.CMD_WRITE)
def _handle_cmd_write(volume: MinIOVolume, sock: socket.socket, cookie: int, offset: int, length: int, write_data: bytes) -> None:
    err = volume.write(offset, write_data)
    _send_simple_reply(sock, err, cookie)


@command(proto.CMD_FLUSH)
def _handle_cmd_flush(volume: MinIOVolume, sock: socket.socket, cookie: int, offset: int, length: int, write_data: bytes) -> None:
    err = volume.flush()
    _send_simple_reply(sock, err, cookie)


# --- Connection loop (dispatches to registered handlers) ---

def handle_connection(
    sock: socket.socket,
    default_export_size: int,
    minio_config: dict,
) -> None:
    """Run full NBD lifecycle for one client on sock. Backend is MinIO only."""
    from nbd.server.handlers import COMMAND_HANDLERS, OPTION_HANDLERS

    if not _send_handshake(sock):
        return
    if _recv_client_flags(sock) is None:
        return

    ctx = OptionContext(default_export_size, minio_config)
    try:
        while True:
            out = _recv_option(sock)
            if out is None:
                return
            opt, payload = out
            handler = OPTION_HANDLERS.get(opt)
            if handler is not None:
                action = handler(sock, payload, ctx)
                if action == "return":
                    return
                if action == "break":
                    break
            else:
                _send_reply_err(sock, opt, proto.REP_ERR_UNSUP)

        volume = ctx.volume
        if volume is None:
            return
        while True:
            out = _recv_request(sock)
            if out is None:
                return
            cmd, cookie, offset, length, write_data = out
            handler = COMMAND_HANDLERS.get(cmd)
            if handler is not None:
                handler(volume, sock, cookie, offset, length, write_data)
            else:
                _send_simple_reply(sock, proto.ERR_PERM, cookie)
    finally:
        volume = getattr(ctx, "volume", None)
        if volume is not None and hasattr(volume, "release"):
            volume.release()
