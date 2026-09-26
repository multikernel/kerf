# Copyright 2026 Multikernel Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Wire protocol between kerf exec and kerf-init, mirrored in src/init/proto.h.
"""

import errno
import socket
import struct
from typing import List, Optional, Sequence, Tuple

AGENT_PORT = 1023
MAX_PAYLOAD = 65536
PROTOCOL_VERSION = 1

OPEN = 1
STDIN = 2
STDIN_EOF = 3
RESIZE = 4
SIGNAL = 5
STARTED = 16
ERROR = 17
STDOUT = 18
STDERR = 19
EXIT = 20

FLAG_TTY = 0x1
FLAG_STDIN = 0x2
FLAG_USER = 0x4

_HDR = struct.Struct("<IHH")
_OPEN = struct.Struct("<HHIIHHHHHH")


class ProtocolError(Exception):
    """The peer sent something that is not a valid frame."""


def pack_frame(ftype: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload of {len(payload)} bytes exceeds {MAX_PAYLOAD}")
    return _HDR.pack(len(payload), ftype, 0) + payload


def _recv_exact(sock: socket.socket, size: int) -> Optional[bytes]:
    chunks = []
    while size > 0:
        chunk = sock.recv(size)
        if not chunk:
            return None
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> Optional[Tuple[int, bytes]]:
    """Read one frame; None when the peer closed between frames."""
    hdr = _recv_exact(sock, _HDR.size)
    if hdr is None:
        return None
    length, ftype, _ = _HDR.unpack(hdr)
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"frame of {length} bytes exceeds {MAX_PAYLOAD}")
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        raise ProtocolError("connection closed inside a frame")
    return ftype, payload


def pack_open(
    argv: Sequence[str],
    env: Sequence[str],
    cwd: str = "/",
    tty: bool = False,
    stdin: bool = False,
    rows: int = 0,
    cols: int = 0,
    user: Optional[Tuple[int, int, List[int]]] = None,
) -> bytes:
    """Build an OPEN frame; user is (uid, gid, supplementary gids)."""
    if not argv:
        raise ValueError("argv must not be empty")
    flags = (FLAG_TTY if tty else 0) | (FLAG_STDIN if stdin else 0)
    uid, gid, groups = 0, 0, []
    if user is not None:
        flags |= FLAG_USER
        uid, gid, groups = user
    fixed = _OPEN.pack(
        PROTOCOL_VERSION, flags, uid, gid, len(groups), rows, cols, len(argv), len(env), 0
    )
    gids = struct.pack(f"<{len(groups)}I", *groups)
    strings = b"".join(s.encode() + b"\0" for s in [cwd, *argv, *env])
    return pack_frame(OPEN, fixed + gids + strings)


def pack_resize(rows: int, cols: int) -> bytes:
    return pack_frame(RESIZE, struct.pack("<HH", rows, cols))


def pack_signal(signo: int) -> bytes:
    return pack_frame(SIGNAL, struct.pack("<I", signo))


def unpack_started(payload: bytes) -> int:
    return struct.unpack("<I", payload)[0]


def unpack_error(payload: bytes) -> Tuple[int, str]:
    (err,) = struct.unpack_from("<I", payload)
    return err, payload[4:].split(b"\0", 1)[0].decode(errors="replace")


def unpack_exit(payload: bytes) -> int:
    """Shell-style status: the exit code, or 128 + signal."""
    signaled, code = struct.unpack("<BB", payload)
    return 128 + code if signaled else code


def error_exit_code(err: int) -> int:
    return 127 if err == errno.ENOENT else 126
