"""
NBD wire format: constants and blocking send/recv. All multi-byte values are big-endian.
"""
import struct
from typing import Tuple

# Handshake magics and flags
MAGIC_NBD = 0x4E42444D41474943  # "NBDMAGIC"
MAGIC_IHAVEOPT = 0x49484156454F5054  # "IHAVEOPT"
FLAG_FIXED_NEWSTYLE = 1

# Option types
OPT_EXPORT_NAME = 1
OPT_ABORT = 2
OPT_GO = 7

# Reply magic and types
REPLY_MAGIC = 0x00000003E889045565A9
REP_ACK = 1
REP_INFO = 3
REP_ERR_UNSUP = 2
REP_ERR_INVALID = 3
REP_ERR_UNKNOWN = 6
INFO_EXPORT = 0

# Transmission flags
TRANSMISSION_FLAG_READ_ONLY = 1
TRANSMISSION_FLAG_SEND_FLUSH = 2

# Request magic and command types
REQUEST_MAGIC = 0x25609513
CMD_READ = 0
CMD_WRITE = 1
CMD_FLUSH = 3
SIMPLE_REPLY_MAGIC = 0x67446698

# NBD error codes
ERR_NONE = 0
ERR_PERM = 1
ERR_IO = 5
ERR_BUSY = 16


def send_all(sock: object, data: bytes) -> bool:
    """Send exactly len(data) bytes. Return True on success."""
    try:
        view = memoryview(data)
        while view:
            n = sock.send(view)
            if n <= 0:
                return False
            view = view[n:]
        return True
    except (OSError, BrokenPipeError):
        return False


def recv_all(sock: object, n: int) -> bytes | None:
    """Receive exactly n bytes. Return None on failure."""
    try:
        buf = bytearray(n)
        view = memoryview(buf)
        while view:
            got = sock.recv_into(view)
            if got <= 0:
                return None
            view = view[got:]
        return bytes(buf)
    except (OSError, ConnectionResetError):
        return None


# Big-endian pack/unpack (NBD = network byte order)
def pack_be64(v: int) -> bytes:
    return struct.pack(">Q", v)


def pack_be32(v: int) -> bytes:
    return struct.pack(">I", v)


def pack_be16(v: int) -> bytes:
    return struct.pack(">H", v)


def unpack_be64(b: bytes) -> int:
    return struct.unpack(">Q", b)[0]


def unpack_be32(b: bytes) -> int:
    return struct.unpack(">I", b)[0]


def unpack_be16(b: bytes) -> int:
    return struct.unpack(">H", b)[0]
