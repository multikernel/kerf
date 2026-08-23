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

"""Tests for replaying a dumped instance with kerf create --input."""

import struct

import libfdt
import pytest
from click.testing import CliRunner

from kerf.create import main
from kerf.dtc.parser import DeviceTreeParser

GB = 1 << 30


def _instance_dtb(name="web", instance_id=7, cpus=(20, 21), memory=2 * GB, devices=("nvme0",)):
    """Build what the kernel serves at instances/NAME/device_tree."""
    sw = libfdt.FdtSw()
    sw.finish_reservemap()
    sw.begin_node(name)
    sw.property_string("compatible", "multikernel-v1")
    sw.property_u32("id", instance_id)
    sw.begin_node("resources")
    sw.property_u64("memory-base", 0x100000000)
    sw.property_u64("memory-bytes", memory)
    sw.property("cpus", struct.pack(f">{len(cpus)}Q", *cpus))
    sw.begin_node("devices")
    for dev in devices:
        sw.begin_node(dev)
        sw.property_string("device-type", "pci")
        sw.end_node()
    sw.end_node()
    sw.end_node()
    sw.end_node()
    fdt = sw.as_fdt()
    fdt.pack()
    return bytes(fdt.as_bytearray())


def test_parse_instance_dump():
    inst = DeviceTreeParser().parse_instance_dtb_from_bytes(_instance_dtb())
    assert inst.name == "web"
    assert inst.id == 7
    assert inst.resources.cpus == [20, 21]
    assert inst.resources.memory_bytes == 2 * GB
    assert inst.resources.devices == ["nvme0"]


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
    sample_tree.hardware.devices["nvme0"] = sample_tree.hardware.devices.pop("eth0")
    monkeypatch.setattr(main, "DeviceTreeManager", lambda: _FakeManager(sample_tree))
    return sample_tree


@pytest.mark.usefixtures("baseline")
def test_create_replays_dump(tmp_path):
    dump = tmp_path / "web.dtb"
    dump.write_bytes(_instance_dtb())

    result = CliRunner().invoke(main.create, [f"--input={dump}", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "instance 'web'" in result.output
    assert "CPUs: 20, 21" in result.output
    assert "Devices: nvme0" in result.output
    assert "Instance ID: 7" in result.output


@pytest.mark.usefixtures("baseline")
def test_create_dump_name_and_id_can_be_overridden(tmp_path):
    dump = tmp_path / "web.dtb"
    dump.write_bytes(_instance_dtb())

    result = CliRunner().invoke(main.create, ["web2", f"--input={dump}", "--id=9", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "instance 'web2'" in result.output
    assert "Instance ID: 9" in result.output


def test_create_input_excludes_resource_flags(tmp_path):
    dump = tmp_path / "web.dtb"
    dump.write_bytes(_instance_dtb())

    result = CliRunner().invoke(main.create, [f"--input={dump}", "--memory=1GB"])

    assert result.exit_code == 2
    assert "--input" in result.output


def test_create_rejects_baseline_dump_as_instance(tmp_path, pool_dtb):
    dump = tmp_path / "host.dtb"
    dump.write_bytes(pool_dtb(lambda sw: sw.property_u32("placeholder", 0)))

    result = CliRunner().invoke(main.create, [f"--input={dump}"])

    assert result.exit_code == 2
    assert "instance" in result.output.lower()


def test_create_rejects_a_dts_dump(tmp_path):
    dts = tmp_path / "web.dts"
    dts.write_text("/dts-v1/;\n/ { };\n", encoding="utf-8")

    result = CliRunner().invoke(main.create, [f"--input={dts}"])

    assert result.exit_code == 2
    assert "--dts" in result.output
