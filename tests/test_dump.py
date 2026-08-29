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
DTS_PREFIX = "/dts-v1/;"


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


@pytest.fixture(name="real_root")
def _real_root(tmp_path, monkeypatch, pool_dtb):
    (tmp_path / "device_tree").write_bytes(pool_dtb(lambda sw: sw.property_string("tag", "pci")))
    monkeypatch.setattr(main, "KERNFS_ROOT", tmp_path)
    return tmp_path


@pytest.mark.usefixtures("real_root")
def test_dump_dts_is_readable_text():
    result = CliRunner().invoke(main.dump, ["--dts"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith(DTS_PREFIX)
    assert 'compatible = "linux,multikernel-host";' in result.output
    assert "resources {" in result.output
    assert 'tag = "pci";' in result.output


@pytest.mark.usefixtures("real_root")
def test_dump_dts_to_file(tmp_path):
    out = tmp_path / "host.dts"
    result = CliRunner().invoke(main.dump, ["--dts", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").startswith(DTS_PREFIX)


@pytest.mark.usefixtures("real_root")
def test_dump_dts_on_a_tty_is_fine(monkeypatch):
    monkeypatch.setattr(main, "_stdout_is_tty", lambda: True)
    result = CliRunner().invoke(main.dump, ["--dts"])
    assert result.exit_code == 0, result.output


def test_instance_dtb_with_host_bridge_nodes_parses_and_renders():
    """The kernel now emits /aliases and pci@N host-bridge nodes alongside
    /resources; dump parsing and DTS rendering must take them in stride."""
    import struct
    import libfdt
    from kerf.dtc.parser import DeviceTreeParser

    sw = libfdt.FdtSw()
    sw.finish_reservemap()
    sw.begin_node("")
    sw.property_string("compatible", "multikernel-v1")
    sw.property_string("model", "web-server")
    sw.property_u32("id", 1)
    sw.begin_node("resources")
    sw.property_u64("memory-base", 0xFFFA0D000)
    sw.property_u64("memory-bytes", 512 * 1024 * 1024)
    sw.property("cpus", struct.pack(">Q", 174))
    sw.begin_node("devices")
    sw.begin_node("pci_0000_50_00_0")
    sw.property_string("device-type", "pci")
    sw.property_string("pci-id", "0000:50:00.0")
    sw.property_u32("vendor-id", 0x144D)
    sw.property_u32("device-id", 0xA80A)
    sw.end_node()
    sw.end_node()
    sw.end_node()  # resources
    sw.begin_node("aliases")
    sw.property_string("nvme0", "/resources/devices/pci_0000_50_00_0")
    sw.end_node()
    sw.begin_node("pci@4f")
    sw.property_string("compatible", "multikernel,pci-host-bridge")
    sw.property_string("device_type", "pci")
    sw.property_u32("#address-cells", 3)
    sw.property_u32("#size-cells", 2)
    sw.property_u32("linux,pci-domain", 0)
    sw.property("bus-range", struct.pack(">II", 0x4F, 0x50))
    sw.property("reg", struct.pack(">IIII", 0, 0xE04F0000, 0, 0x200000))
    sw.property("ranges", struct.pack(">7I", 0x02000000, 0, 0xB0000000,
                                      0, 0xB0000000, 0, 0x10000000))
    sw.end_node()
    sw.end_node()  # root
    dtb = sw.as_fdt()
    dtb.pack()
    data = bytes(dtb.as_bytearray())

    instance = DeviceTreeParser().parse_instance_dtb_from_bytes(data)
    assert instance.name == "web-server"
    assert instance.id == 1
    assert instance.resources.devices == ["pci_0000_50_00_0"]

    text = DeviceTreeParser().dts_from_dtb(data)
    assert "pci@4f {" in text
    assert "multikernel,pci-host-bridge" in text
    assert "bus-range" in text
    assert "nvme0" in text
