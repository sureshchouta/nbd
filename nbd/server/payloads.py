"""
Parsed NBD payloads (stdlib dataclasses). Parse once, pass typed data to handlers.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OptGoPayload:
    """NBD_OPT_GO payload: export name (length-prefixed in wire format)."""
    export_name: str


def parse_opt_go(payload: bytes) -> OptGoPayload | None:
    """Parse OPT_GO payload. Returns None if invalid."""
    if len(payload) < 4:
        return None
    from nbd.server import protocol as proto
    name_len = proto.unpack_be32(payload[:4])
    if name_len > len(payload) - 4:
        return None
    export_name = payload[4 : 4 + name_len].decode("utf-8", errors="replace")
    return OptGoPayload(export_name=export_name)
