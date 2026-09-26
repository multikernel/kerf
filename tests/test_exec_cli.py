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
Tests for the kerf exec command.
"""

import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kerf.cli import main

SESSION_TEST = Path(__file__).resolve().parents[1] / "src" / "init" / "session-test"


@pytest.fixture(name="instance")
def fixture_instance():
    with patch("kerf.exec.main.get_instance_id_from_name", return_value=3), \
         patch("kerf.exec.main.get_instance_status", return_value="active"):
        yield


@pytest.fixture(name="agent")
def fixture_agent():
    host, agent = socket.socketpair()
    proc = subprocess.Popen([str(SESSION_TEST)], stdin=agent)  # pylint: disable=consider-using-with
    agent.close()
    with patch("kerf.exec.main.connect_agent", return_value=host) as connect:
        yield connect
    proc.wait(timeout=5)


@pytest.mark.skipif(not SESSION_TEST.exists(), reason="session-test not built")
def test_runs_command(instance, agent):  # pylint: disable=unused-argument
    result = CliRunner().invoke(main, ["exec", "web", "sh", "-c", "echo hi; exit 5"])
    assert result.output == "hi\n"
    assert result.exit_code == 5
    agent.assert_called_once_with(3, "web")


@pytest.mark.skipif(not SESSION_TEST.exists(), reason="session-test not built")
def test_missing_binary_exits_127(instance, agent):  # pylint: disable=unused-argument
    result = CliRunner().invoke(main, ["exec", "web", "--", "no-such-binary"])
    assert result.exit_code == 127
    assert "exec no-such-binary:" in result.output


def test_instance_not_active():
    with patch("kerf.exec.main.get_instance_id_from_name", return_value=3), \
         patch("kerf.exec.main.get_instance_status", return_value="loaded"):
        result = CliRunner().invoke(main, ["exec", "web", "true"])
    assert result.exit_code == 1
    assert "kerf start web" in result.output


def test_unknown_instance():
    with patch("kerf.exec.main.get_instance_id_from_name", return_value=None):
        result = CliRunner().invoke(main, ["exec", "nope", "true"])
    assert result.exit_code == 1
    assert "Instance 'nope' not found" in result.output


def test_env_without_equals_is_rejected(instance):  # pylint: disable=unused-argument
    with patch("kerf.exec.main.connect_agent") as connect:
        result = CliRunner().invoke(main, ["exec", "-e", "A", "web", "true"])
    assert result.exit_code == 1
    assert "expected KEY=VALUE" in result.output
    connect.assert_not_called()


def test_user_resolution_error(instance, tmp_path):  # pylint: disable=unused-argument
    with patch("kerf.exec.main.KERF_DAXFS_MNT_DIR", str(tmp_path)), \
         patch("kerf.exec.main.connect_agent") as connect:
        result = CliRunner().invoke(main, ["exec", "-u", "alice", "web", "id"])
    assert result.exit_code == 1
    assert "rootfs is not mounted on the host" in result.output
    connect.assert_not_called()


def test_needs_a_command(instance):  # pylint: disable=unused-argument
    result = CliRunner().invoke(main, ["exec", "web"])
    assert result.exit_code == 2


@pytest.mark.skipif(not SESSION_TEST.exists(), reason="session-test not built")
def test_by_id(agent):
    with patch("kerf.exec.main.get_instance_name_from_id", return_value="web"), \
         patch("kerf.exec.main.get_instance_status", return_value="active"):
        result = CliRunner().invoke(main, ["exec", "--id", "3", "--", "echo", "x"])
    assert result.output == "x\n"
    assert result.exit_code == 0
    agent.assert_called_once_with(3, "web")
