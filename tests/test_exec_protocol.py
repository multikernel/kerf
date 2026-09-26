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
Tests for the kerf exec wire protocol.
"""

import errno
import socket
import struct

import pytest

from kerf.exec import protocol


def test_frame_header():
    assert protocol.pack_frame(protocol.STDIN, b"ab") == b"\x02\x00\x00\x00\x02\x00\x00\x00ab"


def test_oversize_payload_is_refused():
    with pytest.raises(ValueError):
        protocol.pack_frame(protocol.STDIN, b"x" * (protocol.MAX_PAYLOAD + 1))


def test_read_frame_round_trip():
    a, b = socket.socketpair()
    a.sendall(protocol.pack_frame(protocol.STDOUT, b"hi") + protocol.pack_frame(protocol.STDIN_EOF))
    assert protocol.read_frame(b) == (protocol.STDOUT, b"hi")
    assert protocol.read_frame(b) == (protocol.STDIN_EOF, b"")
    a.close()
    assert protocol.read_frame(b) is None


def test_read_frame_rejects_oversize_header():
    a, b = socket.socketpair()
    a.sendall(struct.pack("<IHH", protocol.MAX_PAYLOAD + 1, protocol.STDOUT, 0))
    with pytest.raises(protocol.ProtocolError):
        protocol.read_frame(b)


def test_read_frame_rejects_truncated_frame():
    a, b = socket.socketpair()
    a.sendall(struct.pack("<IHH", 10, protocol.STDOUT, 0) + b"abc")
    a.close()
    with pytest.raises(protocol.ProtocolError):
        protocol.read_frame(b)


def test_open_layout_without_user():
    frame = protocol.pack_open(["ls", "-l"], ["A=1"], cwd="/tmp", tty=True, stdin=True, rows=24, cols=80)
    length, ftype, _ = struct.unpack_from("<IHH", frame)
    assert ftype == protocol.OPEN
    assert length == len(frame) - 8
    fields = struct.unpack_from("<HHIIHHHHHH", frame, 8)
    assert fields == (1, protocol.FLAG_TTY | protocol.FLAG_STDIN, 0, 0, 0, 24, 80, 2, 1, 0)
    assert frame[8 + 24:] == b"/tmp\0ls\0-l\0A=1\0"


def test_open_layout_with_user():
    frame = protocol.pack_open(["id"], [], user=(1000, 100, [10, 20]))
    fields = struct.unpack_from("<HHIIHHHHHH", frame, 8)
    assert fields[1] == protocol.FLAG_USER
    assert fields[2:5] == (1000, 100, 2)
    assert struct.unpack_from("<2I", frame, 8 + 24) == (10, 20)
    assert frame[8 + 24 + 8:] == b"/\0id\0"


def test_open_needs_argv():
    with pytest.raises(ValueError):
        protocol.pack_open([], [])


def test_resize_and_signal():
    assert protocol.pack_resize(40, 100)[8:] == struct.pack("<HH", 40, 100)
    assert protocol.pack_signal(15)[8:] == struct.pack("<I", 15)


def test_unpack_replies():
    assert protocol.unpack_started(struct.pack("<I", 42)) == 42
    assert protocol.unpack_error(struct.pack("<I", 2) + b"exec x: gone\0") == (2, "exec x: gone")
    assert protocol.unpack_exit(bytes([0, 3])) == 3
    assert protocol.unpack_exit(bytes([1, 15])) == 143


def test_error_exit_code():
    assert protocol.error_exit_code(errno.ENOENT) == 127
    assert protocol.error_exit_code(errno.EACCES) == 126


def test_stdin_ack():
    assert protocol.STDIN_ACK == 21
    assert protocol.STDIN_WINDOW == 65536
    assert protocol.unpack_ack(struct.pack("<I", 4096)) == 4096
