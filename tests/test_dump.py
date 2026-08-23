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

"""Tests for kerf dump."""

from click.testing import CliRunner
import pytest

from kerf.dump import main

BASELINE = b"\xd0\x0d\xfe\xed base"
INSTANCE = b"\xd0\x0d\xfe\xed inst"


@pytest.fixture(name="root")
def _root(tmp_path, monkeypatch):
    (tmp_path / "device_tree").write_bytes(BASELINE)
    inst = tmp_path / "instances" / "web"
    inst.mkdir(parents=True)
    (inst / "device_tree").write_bytes(INSTANCE)
    monkeypatch.setattr(main, "KERNFS_ROOT", tmp_path)
    return tmp_path


@pytest.mark.usefixtures("root")
def test_dump_baseline_to_file(tmp_path):
    out = tmp_path / "host.dtb"
    result = CliRunner().invoke(main.dump, ["-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == BASELINE


@pytest.mark.usefixtures("root")
def test_dump_instance_to_file(tmp_path):
    out = tmp_path / "web.dtb"
    result = CliRunner().invoke(main.dump, ["web", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == INSTANCE


@pytest.mark.usefixtures("root")
def test_dump_to_stdout():
    result = CliRunner().invoke(main.dump, [])
    assert result.exit_code == 0
    assert result.stdout_bytes == BASELINE


@pytest.mark.usefixtures("root")
def test_dump_refuses_binary_on_tty(monkeypatch):
    monkeypatch.setattr(main, "_stdout_is_tty", lambda: True)
    result = CliRunner().invoke(main.dump, [])
    assert result.exit_code != 0
    assert "-o" in result.output


@pytest.mark.usefixtures("root")
def test_dump_unknown_instance():
    result = CliRunner().invoke(main.dump, ["nope"])
    assert result.exit_code != 0
    assert "nope" in result.output
    assert "web" in result.output


def test_dump_without_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "KERNFS_ROOT", tmp_path / "missing")
    result = CliRunner().invoke(main.dump, [])
    assert result.exit_code != 0
    assert "multikernel" in result.output
