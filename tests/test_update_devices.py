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

"""Tests for moving devices with kerf update --devices."""

import contextlib

import libfdt
import pytest
from click.testing import CliRunner

from kerf.dtc.overlay import OverlayGenerator
from kerf.models import DeviceInfo
from kerf.update import main
from tests.test_parser_pci_tree import _instance_dtb

MISSING = -libfdt.FDT_ERR_NOTFOUND


class _FakeManager:
    """A kernel holding the sample pool and one instance built by _instance_dtb."""

    def __init__(self, tree):
        self.tree = tree
        self.overlay_gen = OverlayGenerator()
        self.applied = []

    def has_instance(self, name):
        return name == "web"

    def read_baseline(self):
        return self.tree

    def read_instance_dtb(self, name):
        assert name == "web"
        return _instance_dtb()

    @contextlib.contextmanager
    def lock(self):
        yield

    def apply_dtbo(self, dtbo):
        self.applied.append(dtbo)
        return "tx_9"


def _pci_ids(dtbo, op):
    fdt = libfdt.Fdt(dtbo)
    node = fdt.path_offset(f"/fragment@0/__overlay__/{op}", libfdt.QUIET_NOTFOUND)
    if node == MISSING:
        return []
    ids = []
    child = fdt.first_subnode(node, quiet=[libfdt.FDT_ERR_NOTFOUND])
    while child >= 0:
        ids.append(fdt.getprop(child, "pci-id").as_str())
        child = fdt.next_subnode(child, quiet=[libfdt.FDT_ERR_NOTFOUND])
    return ids


@pytest.fixture(name="manager")
def _manager(sample_tree, monkeypatch):
    sample_tree.instances.clear()
    sample_tree.hardware.devices["pci_0000_04_00_0"] = DeviceInfo(
        name="pci_0000_04_00_0", compatible="", device_type="pci",
        pci_id="0000:04:00.0", alias="nvme0")
    manager = _FakeManager(sample_tree)
    monkeypatch.setattr(main, "DeviceTreeManager", lambda: manager)
    return manager


def test_update_sees_the_devices_the_instance_holds(manager):
    """The instance built by _instance_dtb holds 09:00.0 (enp9s0) and 00:1f.2."""
    result = CliRunner().invoke(main.update, ["web", "--devices=enp9s0,0000:00:1f.2"])

    assert result.exit_code == 0, result.output
    assert not _pci_ids(manager.applied[0], "device-add")
    assert not _pci_ids(manager.applied[0], "device-remove")


@pytest.mark.parametrize("ref", ["nvme0", "0000:04:00.0", "pci_0000_04_00_0"])
def test_update_adds_a_pool_device_by_any_name(manager, ref):
    result = CliRunner().invoke(main.update, ["web", f"--devices=enp9s0,0000:00:1f.2,{ref}"])

    assert result.exit_code == 0, result.output
    assert _pci_ids(manager.applied[0], "device-add") == ["0000:04:00.0"]
    assert not _pci_ids(manager.applied[0], "device-remove")


def test_update_returns_devices_not_named(manager):
    result = CliRunner().invoke(main.update, ["web", "--devices=enp9s0"])

    assert result.exit_code == 0, result.output
    assert _pci_ids(manager.applied[0], "device-remove") == ["0000:00:1f.2"]
    assert not _pci_ids(manager.applied[0], "device-add")


def test_update_none_returns_every_device(manager):
    result = CliRunner().invoke(main.update, ["web", "--devices=none"])

    assert result.exit_code == 0, result.output
    assert sorted(_pci_ids(manager.applied[0], "device-remove")) == ["0000:00:1f.2", "0000:09:00.0"]


def test_update_rejects_a_device_nobody_has(manager):
    result = CliRunner().invoke(main.update, ["web", "--devices=0000:05:00.0"])

    assert result.exit_code != 0
    assert "0000:05:00.0" in result.output
    assert manager.applied == []
