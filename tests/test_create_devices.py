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

"""Tests for naming pool devices on kerf create --devices."""

import pytest
from click.testing import CliRunner

from kerf.create import main


class _FakeManager:
    def __init__(self, tree):
        self.tree = tree

    def has_instance(self, _name):
        return False

    def read_baseline(self):
        return self.tree


@pytest.fixture(name="baseline")
def _baseline(sample_tree, monkeypatch):
    sample_tree.instances.clear()
    monkeypatch.setattr(main, "DeviceTreeManager", lambda: _FakeManager(sample_tree))
    return sample_tree


@pytest.mark.usefixtures("baseline")
@pytest.mark.parametrize("ref", ["eth0", "0000:01:00.0"])
def test_create_resolves_device_to_its_node(ref):
    result = CliRunner().invoke(
        main.create, ["web", "--cpus=4-5", "--memory=1GB", f"--devices={ref}", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Devices: eth0" in result.output


@pytest.mark.usefixtures("baseline")
def test_create_rejects_a_device_not_in_the_pool():
    result = CliRunner().invoke(
        main.create, ["web", "--cpus=4-5", "--memory=1GB", "--devices=0000:09:00.0", "--dry-run"])

    assert result.exit_code == 1
    assert "0000:09:00.0" in result.output


class _AppliedManager(_FakeManager):
    """Stands in for the kernel after an overlay has been applied."""

    def apply_operation(self, operation):
        self.created = operation(self.tree).instances["web"]
        return "tx_1"

    def read_instance(self, name):
        assert name == "web"
        return self.created


def test_create_verbose_reports_the_kernel_view(sample_tree, monkeypatch):
    sample_tree.instances.clear()
    monkeypatch.setattr(main, "DeviceTreeManager", lambda: _AppliedManager(sample_tree))

    result = CliRunner().invoke(
        main.create, ["web", "--cpus=4-5", "--memory=1GB", "--devices=eth0", "--verbose"])

    assert result.exit_code == 0, result.output
    assert "Created instance 'web' (transaction tx_1)" in result.output
    assert "CPUs: 4, 5" in result.output
    assert "Devices: eth0" in result.output
