# Copyright 2025 Multikernel Technologies, Inc.
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
Tests for kerf device tree parser.
"""

import struct

import libfdt
import pytest
from kerf.dtc.parser import DeviceTreeParser
from kerf.dtc.extractor import InstanceExtractor
from kerf.exceptions import ParseError


class TestDeviceTreeParser:
    """Test device tree parsing."""

    def test_parse_dtb_roundtrip(self, sample_tree):
        """Test DTB generation and parsing roundtrip."""
        # Generate DTB from tree
        extractor = InstanceExtractor()
        dtb_data = extractor.generate_global_dtb(sample_tree)

        # Parse it back
        parser = DeviceTreeParser()
        parsed_tree = parser.parse_dtb_from_bytes(dtb_data)

        # Verify resources match
        assert parsed_tree.hardware.cpus.available == sample_tree.hardware.cpus.available
        assert parsed_tree.hardware.memory.requested == {
            0: sample_tree.hardware.memory.memory_pool_bytes
        }
        assert (
            parsed_tree.hardware.memory.memory_pool_bytes
            == sample_tree.hardware.memory.memory_pool_bytes
        )

        # Verify instances match
        assert len(parsed_tree.instances) == len(sample_tree.instances)
        for name in sample_tree.instances:
            assert name in parsed_tree.instances
            orig = sample_tree.instances[name]
            parsed = parsed_tree.instances[name]
            assert parsed.id == orig.id
            assert parsed.resources.cpus == orig.resources.cpus
            assert parsed.resources.memory_base == orig.resources.memory_base
            assert parsed.resources.memory_bytes == orig.resources.memory_bytes

    def test_parse_dtb_empty_instances(self, sample_hardware):
        """Test parsing DTB with no instances."""
        from kerf.models import GlobalDeviceTree

        tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

        extractor = InstanceExtractor()
        dtb_data = extractor.generate_global_dtb(tree)

        parser = DeviceTreeParser()
        parsed_tree = parser.parse_dtb_from_bytes(dtb_data)

        assert len(parsed_tree.instances) == 0
        assert parsed_tree.hardware.cpus.available == sample_hardware.cpus.available

    def test_parse_invalid_dtb(self):
        """Test parsing invalid DTB data."""
        parser = DeviceTreeParser()

        # Invalid data
        invalid_data = b"not a valid dtb"

        with pytest.raises(ParseError, match="Failed to parse DTB"):
            parser.parse_dtb_from_bytes(invalid_data)

    def test_parse_empty_dtb(self):
        """Test parsing empty DTB data."""
        parser = DeviceTreeParser()

        # Empty data
        empty_data = b""

        with pytest.raises(ParseError):
            parser.parse_dtb_from_bytes(empty_data)

    def test_parse_dtb_with_devices(self, sample_tree):
        """Test parsing DTB with device information."""
        # Verify devices are preserved
        extractor = InstanceExtractor()
        dtb_data = extractor.generate_global_dtb(sample_tree)

        parser = DeviceTreeParser()
        parsed_tree = parser.parse_dtb_from_bytes(dtb_data)

        # Check devices are parsed
        assert "eth0" in parsed_tree.hardware.devices
        device = parsed_tree.hardware.devices["eth0"]
        assert device.name == "eth0"
        assert device.compatible == "intel,i40e"
        assert device.sriov_vfs == 8

    def test_parse_kernel_pci_hierarchy_by_bdf(self):
        """Parse PCI leaves from the live pool tree emitted by the kernel."""
        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()
        fdt_sw.begin_node("")
        fdt_sw.property_string("compatible", "multikernel-v1")

        fdt_sw.begin_node("resources")
        fdt_sw.property("cpus", struct.pack(">QQQQ", 0, 1, 2, 3))
        fdt_sw.property("cpus-available", struct.pack(">QQ", 2, 3))
        fdt_sw.begin_node("memory@0")
        fdt_sw.property("reg", struct.pack(">QQ", 0x200000000, 0x40000000))
        fdt_sw.property_u32("numa-node-id", 0)
        fdt_sw.end_node()
        fdt_sw.end_node()

        fdt_sw.begin_node("pci@0")
        fdt_sw.property_string("compatible", "multikernel,pci-host-bridge")
        fdt_sw.property_u32("linux,pci-domain", 0)
        fdt_sw.begin_node("pci@12,0")
        fdt_sw.property("reg", struct.pack(">IIIII", 0x9000, 0, 0, 0, 0))
        fdt_sw.property_u32("vendor-id", 0x8086)
        fdt_sw.property_u32("device-id", 0x10CA)
        fdt_sw.end_node()
        fdt_sw.begin_node("pci@3,0")
        fdt_sw.property("reg", struct.pack(">IIIII", 0x1800, 0, 0, 0, 0))
        fdt_sw.begin_node("pci@10,2")
        fdt_sw.property("reg", struct.pack(">IIIII", 0x18200, 0, 0, 0, 0))
        fdt_sw.property_u32("vendor-id", 0x8086)
        fdt_sw.property_u32("device-id", 0x10CA)
        fdt_sw.end_node()
        fdt_sw.end_node()
        fdt_sw.end_node()
        fdt_sw.end_node()

        dtb = fdt_sw.as_fdt()
        dtb.pack()
        devices = DeviceTreeParser().parse_dtb_from_bytes(
            dtb.as_bytearray()
        ).hardware.devices

        assert set(devices) == {"0000:00:12.0", "0000:01:10.2"}
        assert devices["0000:00:12.0"].pci_id == "0000:00:12.0"
        assert devices["0000:01:10.2"].vendor_id == 0x8086
        assert set(DeviceTreeParser().parse_devices_from_bytes(dtb.as_bytearray())) == set(
            devices
        )

    @pytest.mark.parametrize(
        ("declaration", "expected"),
        [
            ("<2 3>", [2, 3]),
            ("<0x2 3>", [2, 3]),
            ("/bits/ 32 <0x2 0x3>", [2, 3]),
            ("/bits/ 64 <0x2 0x3>", [2, 3]),
        ],
    )
    def test_parse_cpu_ids_from_dts(self, declaration, expected):
        """Test legacy and explicit-width CPU cells in DTS sources."""
        dts = f"/dts-v1/; / {{ resources {{ cpus = {declaration}; }}; }};"
        cpus = DeviceTreeParser()._parse_cpus_from_dts(dts)  # pylint: disable=protected-access

        assert cpus.available == expected

    @pytest.mark.parametrize(
        ("declaration", "expected"),
        [
            ("<4 5>", [4, 5]),
            ("/bits/ 32 <0x4 0x5>", [4, 5]),
            ("/bits/ 64 <0x4 0x5>", [4, 5]),
        ],
    )
    def test_parse_instance_resource_cpu_ids_from_dts(self, declaration, expected):
        """Test instance resource CPU cells in DTS sources."""
        dts = f"""
        resources {{
            cpus = {declaration};
            memory-base = <0x100000000>;
            memory-bytes = <0x40000000>;
        }};
        """
        resources = DeviceTreeParser()._parse_instance_resources_from_dts(dts)  # pylint: disable=protected-access

        assert resources.cpus == expected

    @pytest.mark.parametrize(
        ("declaration", "expected"),
        [
            ("<6 7>", [6, 7]),
            ("/bits/ 32 <0x6 0x7>", [6, 7]),
            ("/bits/ 64 <0x6 0x7>", [6, 7]),
        ],
    )
    def test_parse_numa_membership_cpu_ids_from_dts(self, declaration, expected):
        """Test NUMA node CPU membership cells in DTS sources."""
        topology = f"""
        topology {{
            numa-nodes {{
                node@0 {{
                    memory-base = <0x0>;
                    memory-size = <0x40000000>;
                    cpus = {declaration};
                }};
            }};
        }};
        """
        parsed = DeviceTreeParser()._parse_topology_from_dts(topology)  # pylint: disable=protected-access

        assert parsed is not None
        assert parsed.numa_nodes is not None
        assert parsed.numa_nodes[0].cpus == expected

    @pytest.mark.parametrize(
        ("declaration", "expected"),
        [
            ("<8 9>", [8, 9]),
            ("/bits/ 32 <0x8 0x9>", [8, 9]),
            ("/bits/ 64 <0x8 0x9>", [8, 9]),
        ],
    )
    def test_parse_core_topology_cpu_ids_from_dts(self, declaration, expected):
        """Test core topology CPU cells in DTS sources."""
        dts = f"""
        /dts-v1/;
        / {{
            cores {{
                core@4 {{ cpus = {declaration}; }};
            }};
        }};
        """
        topology = DeviceTreeParser()._parse_cpu_topology_from_dts(dts)  # pylint: disable=protected-access

        assert topology is not None
        assert sorted(topology) == expected
        assert [topology[cpu_id].core_id for cpu_id in expected] == [4, 4]  # pylint: disable=unsubscriptable-object


class TestInstanceExtractor:
    """Test instance extraction."""

    def test_generate_global_dtb(self, sample_tree):
        """Test generating global DTB."""
        extractor = InstanceExtractor()
        dtb_data = extractor.generate_global_dtb(sample_tree)

        # Should produce non-empty DTB
        assert len(dtb_data) > 0

        # Should be valid FDT with magic number
        magic = struct.unpack(">I", dtb_data[:4])[0]
        assert magic == 0xD00DFEED  # FDT magic number

    def test_generate_global_dtb_empty_instances(self, sample_hardware):
        """Test generating DTB with no instances."""
        from kerf.models import GlobalDeviceTree

        tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

        extractor = InstanceExtractor()
        dtb_data = extractor.generate_global_dtb(tree)

        # Should produce valid DTB
        assert len(dtb_data) > 0

    def test_generate_dtb_with_multiple_instances(self, sample_tree):
        """Test generating DTB with multiple instances."""
        from kerf.models import Instance, InstanceResources

        # Add another instance
        sample_tree.instances["test"] = Instance(
            name="test",
            id=3,
            resources=InstanceResources(
                cpus=[16, 17], memory_base=0x200000000, memory_bytes=1024**3, devices=[]
            ),
        )

        extractor = InstanceExtractor()
        dtb_data = extractor.generate_global_dtb(sample_tree)

        # Parse and verify
        parser = DeviceTreeParser()
        parsed_tree = parser.parse_dtb_from_bytes(dtb_data)

        assert len(parsed_tree.instances) == 3
        assert "test" in parsed_tree.instances
