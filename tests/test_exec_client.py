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
Tests for the kerf exec host relay.
"""

import io
import os
import socket
import subprocess
from pathlib import Path

import pytest

from kerf.exec import protocol
from kerf.exec.client import DEFAULT_PATH, LostConnection, RemoteError, Session, build_env

SESSION_TEST = Path(__file__).resolve().parents[1] / "src" / "init" / "session-test"
needs_session = pytest.mark.skipif(not SESSION_TEST.exists(), reason="session-test not built")


def test_build_env_defaults():
    assert build_env([], "/root", None) == [f"PATH={DEFAULT_PATH}", "HOME=/root"]


def test_build_env_overrides_and_term():
    env = build_env(["PATH=/bin", "A=1=2"], "/home/a", "xterm")
    assert env == ["PATH=/bin", "HOME=/home/a", "TERM=xterm", "A=1=2"]


def test_build_env_needs_equals():
    with pytest.raises(ValueError):
        build_env(["A"], "/", None)


@pytest.fixture(name="agent")
def fixture_agent():
    host, agent = socket.socketpair()
    proc = subprocess.Popen([str(SESSION_TEST)], stdin=agent)  # pylint: disable=consider-using-with
    agent.close()
    yield host
    host.close()
    proc.wait(timeout=5)


@needs_session
def test_run_returns_exit_code(agent):
    out, err = io.BytesIO(), io.BytesIO()
    frame = protocol.pack_open(["sh", "-c", "echo o; echo e >&2; exit 4"], build_env([], "/", None))
    assert Session(agent).run(frame, out, err) == 4
    assert out.getvalue() == b"o\n"
    assert err.getvalue() == b"e\n"


@needs_session
def test_run_forwards_stdin(agent):
    r, w = os.pipe()
    os.write(w, b"piped\n")
    os.close(w)
    out = io.BytesIO()
    frame = protocol.pack_open(["cat"], build_env([], "/", None), stdin=True)
    assert Session(agent).run(frame, out, io.BytesIO(), stdin_fd=r) == 0
    assert out.getvalue() == b"piped\n"
    os.close(r)


@needs_session
def test_run_raises_remote_error(agent):
    frame = protocol.pack_open(["no-such-binary"], build_env([], "/", None))
    with pytest.raises(RemoteError) as info:
        Session(agent).run(frame, io.BytesIO(), io.BytesIO())
    assert info.value.message.startswith("exec no-such-binary:")


@needs_session
def test_on_started_runs_after_started(agent):
    seen = []
    out = io.BytesIO()

    def on_started():
        seen.append(out.getvalue())

    frame = protocol.pack_open(["echo", "x"], build_env([], "/", None))
    Session(agent).run(frame, out, io.BytesIO(), on_started=on_started)
    assert seen == [b""]


def test_lost_connection():
    host, agent = socket.socketpair()
    agent.close()
    with pytest.raises(LostConnection):
        Session(host).run(protocol.pack_open(["true"], []), io.BytesIO(), io.BytesIO())
