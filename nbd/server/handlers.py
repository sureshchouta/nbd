"""
Decorator-based dispatch for NBD options and commands (same idea as Flask/FastAPI routes).
Register handlers with @option(OPT_GO) or @command(CMD_READ); connection loop dispatches to them.
"""
from typing import Any, Callable

from nbd.server import protocol as proto

# Option handlers: opt_id -> (sock, payload, ctx) -> "break" | "return" | "continue"
# ctx has default_export_size, minio_config; handler may set ctx.volume for "break".
OPTION_HANDLERS: dict[int, Callable[..., str]] = {}

# Command handlers: cmd_id -> (volume, sock, cookie, offset, length, write_data) -> None
# Handler must send the simple reply itself.
COMMAND_HANDLERS: dict[int, Callable[..., None]] = {}


def option(opt_id: int) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a handler for an NBD option (e.g. OPT_GO, OPT_ABORT)."""

    def register(f: Callable[..., str]) -> Callable[..., str]:
        OPTION_HANDLERS[opt_id] = f
        return f

    return register


def command(cmd_id: int) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Register a handler for a transmission command (e.g. CMD_READ, CMD_WRITE)."""

    def register(f: Callable[..., None]) -> Callable[..., None]:
        COMMAND_HANDLERS[cmd_id] = f
        return f

    return register


class OptionContext:
    """Context passed to option handlers; set .volume when handling OPT_GO."""

    def __init__(self, default_export_size: int, minio_config: dict[str, Any]):
        self.default_export_size = default_export_size
        self.minio_config = minio_config  # {endpoint_url, access_key, secret_key, bucket}
        self.volume: Any = None
