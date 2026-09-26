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
Drive the C exec session (src/init/session-test) over a socketpair.
"""

import errno
import os
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from kerf.exec import protocol

SESSION_TEST = Path(__file__).resolve().parents[1] / "src" / "init" / "session-test"
ENV = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]

pytestmark = pytest.mark.skipif(
    not SESSION_TEST.exists(), reason="session-test not built, run make"
)


@pytest.fixture(name="start")
def fixture_start():
    sessions = []

    def start():
        host, agent = socket.socketpair()
        proc = subprocess.Popen([str(SESSION_TEST)], stdin=agent)  # pylint: disable=consider-using-with
        agent.close()
        host.settimeout(10)
        sessions.append((host, proc))
        return host

    yield start
    for host, proc in sessions:
        host.close()
        proc.wait(timeout=5)


def collect(host):
    """Read frames until EXIT, ERROR or close; return what arrived."""
    result = {"stdout": b"", "stderr": b"", "exit": None, "error": (0, ""), "started": False}
    while True:
        frame = protocol.read_frame(host)
        if frame is None:
            return result
        ftype, payload = frame
        if ftype == protocol.STARTED:
            result["started"] = True
        elif ftype == protocol.STDOUT:
            result["stdout"] += payload
        elif ftype == protocol.STDERR:
            result["stderr"] += payload
        elif ftype == protocol.EXIT:
            result["exit"] = protocol.unpack_exit(payload)
            return result
        elif ftype == protocol.ERROR:
            result["error"] = protocol.unpack_error(payload)
            return result


def wait_started(host):
    ftype, _ = protocol.read_frame(host)
    assert ftype == protocol.STARTED


def test_echo(start):
    host = start()
    host.sendall(protocol.pack_open(["echo", "hi"], ENV))
    result = collect(host)
    assert result["started"]
    assert result["stdout"] == b"hi\n"
    assert result["exit"] == 0


def test_stdout_and_stderr_stay_separate(start):
    host = start()
    host.sendall(protocol.pack_open(["sh", "-c", "echo out; echo err >&2; exit 3"], ENV))
    result = collect(host)
    assert result["stdout"] == b"out\n"
    assert result["stderr"] == b"err\n"
    assert result["exit"] == 3


def test_stdin_round_trip(start):
    host = start()
    host.sendall(protocol.pack_open(["cat"], ENV, stdin=True))
    host.sendall(protocol.pack_frame(protocol.STDIN, b"hello\n"))
    host.sendall(protocol.pack_frame(protocol.STDIN_EOF))
    result = collect(host)
    assert result["stdout"] == b"hello\n"
    assert result["exit"] == 0


def test_without_stdin_flag_reads_eof(start):
    host = start()
    host.sendall(protocol.pack_open(["cat"], ENV))
    result = collect(host)
    assert result["stdout"] == b""
    assert result["exit"] == 0


def test_missing_binary(start):
    host = start()
    host.sendall(protocol.pack_open(["no-such-binary"], ENV))
    result = collect(host)
    assert not result["started"]
    err, msg = result["error"]
    assert err == errno.ENOENT
    assert msg.startswith("exec no-such-binary:")
    assert protocol.error_exit_code(err) == 127


def test_path_comes_from_sent_env(start):
    host = start()
    host.sendall(protocol.pack_open(["env"], ["PATH=/nonexistent"]))
    assert collect(host)["error"][0] == errno.ENOENT


def test_env_is_exactly_what_was_sent(start):
    host = start()
    host.sendall(protocol.pack_open(["env"], ENV + ["A=1"]))
    result = collect(host)
    assert result["stdout"].decode().splitlines() == ENV + ["A=1"]


def test_cwd(start, tmp_path):
    host = start()
    host.sendall(protocol.pack_open(["pwd"], ENV, cwd=str(tmp_path)))
    assert collect(host)["stdout"] == f"{tmp_path}\n".encode()


def test_missing_cwd(start):
    host = start()
    host.sendall(protocol.pack_open(["pwd"], ENV, cwd="/no/such/dir"))
    err, msg = collect(host)["error"]
    assert err == errno.ENOENT
    assert msg.startswith("chdir /no/such/dir:")


def test_killed_by_signal(start):
    host = start()
    host.sendall(protocol.pack_open(["sh", "-c", "kill -TERM $$"], ENV))
    assert collect(host)["exit"] == 128 + signal.SIGTERM


def test_signal_frame(start):
    host = start()
    host.sendall(protocol.pack_open(["sleep", "100"], ENV))
    wait_started(host)
    host.sendall(protocol.pack_signal(signal.SIGTERM))
    assert collect(host)["exit"] == 128 + signal.SIGTERM


def test_tty(start):
    host = start()
    host.sendall(protocol.pack_open(["sh", "-c", "tty; stty size"], ENV, tty=True, rows=24, cols=80))
    result = collect(host)
    assert result["stderr"] == b""
    lines = result["stdout"].decode().split("\r\n")
    assert lines[0].startswith("/dev/pts/")
    assert lines[1] == "24 80"
    assert result["exit"] == 0


def test_tty_resize(start):
    host = start()
    host.sendall(
        protocol.pack_open(["sh", "-c", "read x; stty size"], ENV, tty=True, stdin=True, rows=24, cols=80)
    )
    wait_started(host)
    host.sendall(protocol.pack_resize(40, 100))
    host.sendall(protocol.pack_frame(protocol.STDIN, b"\n"))
    result = collect(host)
    assert b"40 100" in result["stdout"]
    assert result["exit"] == 0


def test_tty_stdin_eof_sends_veof(start):
    host = start()
    host.sendall(protocol.pack_open(["cat"], ENV, tty=True, stdin=True))
    wait_started(host)
    host.sendall(protocol.pack_frame(protocol.STDIN_EOF))
    assert collect(host)["exit"] == 0


def test_disconnect_sends_sighup(start, tmp_path):
    marker = tmp_path / "hup"
    # stderr goes to /dev/null: sh reports the killed sleep there, and with the
    # session gone that write would SIGPIPE sh before its trap runs.
    script = (
        f"exec 2>/dev/null; trap 'echo hup > {marker}; exit 0' HUP; "
        "echo ready; while :; do sleep 0.05; done"
    )
    host = start()
    host.sendall(protocol.pack_open(["sh", "-c", script], ENV))
    wait_started(host)
    ftype, payload = protocol.read_frame(host)
    assert (ftype, payload) == (protocol.STDOUT, b"ready\n")
    host.shutdown(socket.SHUT_RDWR)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text() == "hup\n"


def test_concurrent_sessions(start):
    first, second = start(), start()
    for host in (first, second):
        host.sendall(protocol.pack_open(["sh", "-c", "read x; echo $x"], ENV, stdin=True))
        wait_started(host)
    second.sendall(protocol.pack_frame(protocol.STDIN, b"two\n"))
    assert collect(second)["stdout"] == b"two\n"
    first.sendall(protocol.pack_frame(protocol.STDIN, b"one\n"))
    assert collect(first)["stdout"] == b"one\n"


def test_large_stdin_to_slow_reader(start):
    size = 1 << 20
    host = start()
    host.sendall(protocol.pack_open(["sh", "-c", "sleep 0.3; wc -c"], ENV, stdin=True))

    def feed():
        chunk = b"x" * protocol.MAX_PAYLOAD
        for _ in range(size // len(chunk)):
            host.sendall(protocol.pack_frame(protocol.STDIN, chunk))
        host.sendall(protocol.pack_frame(protocol.STDIN_EOF))

    feeder = threading.Thread(target=feed)
    feeder.start()
    result = collect(host)
    feeder.join()
    assert result["stdout"].strip() == str(size).encode()


def test_large_stdout_to_slow_host(start):
    size = 1 << 20
    host = start()
    host.sendall(protocol.pack_open(["head", "-c", str(size), "/dev/zero"], ENV))
    time.sleep(0.3)
    result = collect(host)
    assert len(result["stdout"]) == size
    assert result["exit"] == 0


@pytest.mark.skipif(os.geteuid() != 0, reason="setgroups needs root")
def test_user(start):
    host = start()
    host.sendall(
        protocol.pack_open(
            ["sh", "-c", "id -u; id -g; id -G"], ENV, user=(65534, 65534, [65534])
        )
    )
    assert collect(host)["stdout"] == b"65534\n65534\n65534\n"


def test_unknown_version(start):
    host = start()
    frame = bytearray(protocol.pack_open(["true"], ENV))
    struct.pack_into("<H", frame, 8, 2)
    host.sendall(bytes(frame))
    assert collect(host)["error"][0] == errno.EPROTO


def test_malformed_open(start):
    host = start()
    frame = protocol.pack_open(["true"], ENV)
    host.sendall(protocol.pack_frame(protocol.OPEN, frame[8:-1]))
    assert collect(host)["error"][0] == errno.EINVAL


def test_first_frame_must_be_open(start):
    host = start()
    host.sendall(protocol.pack_frame(protocol.STDIN, b"x"))
    try:
        frame = protocol.read_frame(host)
    except ConnectionResetError:
        frame = None
    assert frame is None


def test_background_holder_does_not_block_exit(start):
    host = start()
    host.sendall(protocol.pack_open(["sh", "-c", "sleep 5 & echo hi"], ENV))
    began = time.monotonic()
    result = collect(host)
    assert result["stdout"] == b"hi\n"
    assert result["exit"] == 0
    assert time.monotonic() - began < 2


def test_command_closes_stdin_early(start):
    host = start()
    host.sendall(protocol.pack_open(["head", "-c", "1"], ENV, stdin=True))

    def feed():
        try:
            for _ in range(64):
                host.sendall(protocol.pack_frame(protocol.STDIN, b"y" * protocol.MAX_PAYLOAD))
            host.sendall(protocol.pack_frame(protocol.STDIN_EOF))
        except OSError:
            pass

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()
    result = collect(host)
    assert result["stdout"] == b"y"
    assert result["exit"] == 0


def test_binary_output(start):
    host = start()
    host.sendall(protocol.pack_open(["printf", "\\377\\000\\001"], ENV))
    assert collect(host)["stdout"] == b"\xff\x00\x01"
