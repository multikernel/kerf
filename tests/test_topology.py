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
Tests for topology support: DTB round-trip, DTS parsing, and host discovery.
"""
# pylint: disable=redefined-outer-name  # pytest fixtures

import pytest

from kerf.dtc.extractor import InstanceExtractor
from kerf.dtc.parser import DeviceTreeParser
from kerf.models import (
    CPUAllocation,
    DeviceInfo,
    GlobalDeviceTree,
    HardwareInventory,
    MemoryAllocation,
    NUMANode,
    TopologySection,
)


def make_tree_with_topology():
    """Build a tree with a two-node NUMA topology using physical APIC IDs."""
    cpus = CPUAllocation(
        total=16,
        host_reserved=[0],
        available=[128, 130, 132, 134, 136, 138, 140, 142],
    )
    memory = MemoryAllocation(
        total_bytes=64 * 1024**3,
        host_reserved_bytes=16 * 1024**3,
        memory_pool_base=0x4_0000_0000,
        memory_pool_bytes=48 * 1024**3,
    )
    topology = TopologySection(
        numa_nodes={
            0: NUMANode(
                node_id=0,
                memory_base=0x0,
                memory_size=32 * 1024**3,
                cpus=[128, 130, 132, 134],
                distance_matrix={0: 10, 1: 21},
                memory_type="dram",
            ),
            1: NUMANode(
                node_id=1,
                memory_base=32 * 1024**3,
                memory_size=32 * 1024**3,
                cpus=[136, 138, 140, 142],
                distance_matrix={0: 21, 1: 10},
                memory_type="hbm",
            ),
        }
    )
    devices = {
        "enp9s0_dev": DeviceInfo(
            name="enp9s0_dev",
            compatible="pci-network",
            device_type="pci",
            pci_id="0000:09:00.0",
            vendor_id=0x8086,
            device_id=0x1572,
            numa_node=1,
        )
    }
    hardware = HardwareInventory(cpus=cpus, memory=memory, topology=topology, devices=devices)
    return GlobalDeviceTree(hardware=hardware, instances={}, device_references={})


class TestTopologyDtbRoundtrip:
    """Topology must survive tree -> DTB -> tree, the path used for kernel state."""

    def roundtrip(self, tree):
        dtb = InstanceExtractor().generate_global_dtb(tree)
        return DeviceTreeParser().parse_dtb_from_bytes(dtb)

    def test_numa_nodes_survive_roundtrip(self):
        tree = make_tree_with_topology()
        parsed = self.roundtrip(tree)

        assert parsed.hardware.topology is not None
        nodes = parsed.hardware.topology.numa_nodes
        assert set(nodes.keys()) == {0, 1}

        node0 = nodes[0]
        assert node0.node_id == 0
        assert node0.memory_base == 0x0
        assert node0.memory_size == 32 * 1024**3
        assert node0.cpus == [128, 130, 132, 134]

        node1 = nodes[1]
        assert node1.memory_base == 32 * 1024**3
        assert node1.cpus == [136, 138, 140, 142]

    def test_memory_type_survives_roundtrip(self):
        parsed = self.roundtrip(make_tree_with_topology())
        assert parsed.hardware.topology.numa_nodes[0].memory_type == "dram"
        assert parsed.hardware.topology.numa_nodes[1].memory_type == "hbm"

    def test_distance_matrix_survives_roundtrip(self):
        parsed = self.roundtrip(make_tree_with_topology())
        assert parsed.hardware.topology.numa_nodes[0].distance_matrix == {0: 10, 1: 21}
        assert parsed.hardware.topology.numa_nodes[1].distance_matrix == {0: 21, 1: 10}

    def test_device_numa_node_survives_roundtrip(self):
        parsed = self.roundtrip(make_tree_with_topology())
        assert parsed.hardware.devices["enp9s0_dev"].numa_node == 1

    def test_absent_topology_stays_absent(self):
        tree = make_tree_with_topology()
        tree.hardware.topology = None
        tree.hardware.devices["enp9s0_dev"].numa_node = None
        parsed = self.roundtrip(tree)
        assert parsed.hardware.topology is None
        assert parsed.hardware.devices["enp9s0_dev"].numa_node is None


NUMA_BASELINE_DTS = """
/multikernel-v1/;

/ {
    compatible = "linux,multikernel-host";

    resources {
        cpus = <128 130 132 134 136 138 140 142>;

        topology {
            numa-nodes {
                node@0 {
                    node-id = <0>;
                    memory-base = <0x0 0x0>;
                    memory-size = <0x8 0x00000000>;
                    cpus = <128 130 132 134>;
                    distance-matrix = <0 10 1 21>;
                };

                node@1 {
                    node-id = <1>;
                    memory-base = <0x8 0x00000000>;
                    memory-size = <0x8 0x00000000>;
                    cpus = <136 138 140 142>;
                    distance-matrix = <0 21 1 10>;
                    memory-type = "hbm";
                };
            };
        };

        memory-base = <0x4 0x00000000>;
        memory-bytes = <0xC 0x00000000>;

        devices {
            eth0: ethernet@0 {
                compatible = "pci-network";
                device-type = "pci";
                pci-id = "0000:09:00.0";
                numa-node = <1>;
            };
        };
    };
};
"""


class TestTopologyDtsParsing:
    """The DTS parser must handle a real nested topology section."""

    def parse(self):
        return DeviceTreeParser().parse_dts(NUMA_BASELINE_DTS)

    def test_all_numa_nodes_parsed(self):
        tree = self.parse()
        topology = tree.hardware.topology
        assert topology is not None
        assert set(topology.numa_nodes.keys()) == {0, 1}
        assert topology.numa_nodes[0].cpus == [128, 130, 132, 134]
        assert topology.numa_nodes[1].cpus == [136, 138, 140, 142]
        assert topology.numa_nodes[1].memory_base == 0x8_0000_0000
        assert topology.numa_nodes[1].memory_type == "hbm"

    def test_distance_matrix_parsed(self):
        tree = self.parse()
        assert tree.hardware.topology.numa_nodes[0].distance_matrix == {0: 10, 1: 21}
        assert tree.hardware.topology.numa_nodes[1].distance_matrix == {0: 21, 1: 10}

    def test_pool_memory_not_confused_with_node_memory(self):
        """Pool memory-base follows the topology section in real baselines;
        the parser must not pick up node@0's memory-base instead."""
        tree = self.parse()
        assert tree.hardware.memory.memory_pool_base == 0x4_0000_0000
        assert tree.hardware.memory.memory_pool_bytes == 0xC_0000_0000

    def test_available_cpus_not_confused_with_node_cpus(self):
        tree = self.parse()
        assert tree.hardware.cpus.available == [128, 130, 132, 134, 136, 138, 140, 142]

    def test_device_numa_node_parsed(self):
        tree = self.parse()
        eth0 = next(iter(tree.hardware.devices.values()))
        assert eth0.numa_node == 1

    def test_all_cores_parsed_from_cores_section(self):
        dts = NUMA_BASELINE_DTS.replace(
            "cpus = <128 130 132 134 136 138 140 142>;",
            "cpus = <128 130 132 134 136 138 140 142>;\n"
            "        cores {\n"
            "            core@0 { cpus = <128 130>; };\n"
            "            core@1 { cpus = <132 134>; };\n"
            "        };",
        )
        tree = DeviceTreeParser().parse_dts(dts)
        cpu_topology = tree.hardware.cpus.topology
        assert cpu_topology is not None
        assert set(cpu_topology.keys()) == {128, 130, 132, 134}
        assert cpu_topology[132].core_id == 1

    def test_comments_inside_property_values(self):
        """DTS comments may appear inside multi-line property values."""
        dts = NUMA_BASELINE_DTS.replace(
            "distance-matrix = <0 10 1 21>;",
            "distance-matrix = <\n"
            "    0 10    /* local */\n"
            "    1 21    // remote\n"
            ">;",
        )
        tree = DeviceTreeParser().parse_dts(dts)
        assert tree.hardware.topology.numa_nodes[0].distance_matrix == {0: 10, 1: 21}


CPUINFO = """\
processor\t: 0
vendor_id\t: AuthenticAMD
apicid\t\t: 128
power management:

processor\t: 1
vendor_id\t: AuthenticAMD
apicid\t\t: 130
power management:

processor\t: 2
vendor_id\t: AuthenticAMD
apicid\t\t: 132
power management:

processor\t: 3
vendor_id\t: AuthenticAMD
apicid\t\t: 134
power management:
"""

ZONEINFO = """\
Node 0, zone      DMA
  pages free     3968
        spanned  4095
        present  3998
        managed  3977
  start_pfn:           1
Node 0, zone    DMA32
  pages free     100000
        spanned  1044480
        present  500000
        managed  480000
  start_pfn:           4096
Node 0, zone   Normal
  pages free     100000
        spanned  3097152
        present  3000000
        managed  2900000
  start_pfn:           1048576
Node 0, zone  Movable
  pages free     0
        spanned  0
        present  0
        managed  0
  start_pfn:           0
Node 1, zone   Normal
  pages free     100000
        spanned  4194304
        present  4100000
        managed  4000000
  start_pfn:           4194304
"""


@pytest.fixture
def fake_host(tmp_path):
    """Fake sysfs NUMA layout: 2 nodes, 2 logical CPUs each, APIC IDs 128-134."""
    node_dir = tmp_path / "node"
    for node_id, cpulist, distance in [
        (0, "0-1", "10 21"),
        (1, "2-3", "21 10"),
    ]:
        d = node_dir / f"node{node_id}"
        d.mkdir(parents=True)
        (d / "cpulist").write_text(cpulist + "\n")
        (d / "distance").write_text(distance + "\n")

    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(CPUINFO)
    zoneinfo = tmp_path / "zoneinfo"
    zoneinfo.write_text(ZONEINFO)
    return {"node_dir": node_dir, "cpuinfo": cpuinfo, "zoneinfo": zoneinfo}


class TestHostTopologyDiscovery:
    """Discovery must translate sysfs (logical CPUs) into APIC-ID topology."""

    def discover(self, fake_host):
        from kerf.topology import discover_numa_topology

        return discover_numa_topology(
            node_dir=fake_host["node_dir"],
            cpuinfo_path=fake_host["cpuinfo"],
            zoneinfo_path=fake_host["zoneinfo"],
        )

    def test_logical_to_physical_cpu_map(self, fake_host):
        from kerf.topology import read_logical_to_physical_cpu_map

        mapping = read_logical_to_physical_cpu_map(fake_host["cpuinfo"])
        assert mapping == {0: 128, 1: 130, 2: 132, 3: 134}

    def test_nodes_use_physical_cpu_ids(self, fake_host):
        topology = self.discover(fake_host)
        assert set(topology.numa_nodes.keys()) == {0, 1}
        assert topology.numa_nodes[0].cpus == [128, 130]
        assert topology.numa_nodes[1].cpus == [132, 134]

    def test_distance_matrix(self, fake_host):
        topology = self.discover(fake_host)
        assert topology.numa_nodes[0].distance_matrix == {0: 10, 1: 21}
        assert topology.numa_nodes[1].distance_matrix == {0: 21, 1: 10}

    def test_node_memory_ranges_from_zoneinfo(self, fake_host):
        topology = self.discover(fake_host)
        page = 4096
        node0 = topology.numa_nodes[0]
        assert node0.memory_base == 1 * page
        assert node0.memory_size == (1048576 + 3097152 - 1) * page
        node1 = topology.numa_nodes[1]
        assert node1.memory_base == 4194304 * page
        assert node1.memory_size == 4194304 * page

    def test_missing_node_dir_returns_none(self, tmp_path, fake_host):
        from kerf.topology import discover_numa_topology

        assert (
            discover_numa_topology(
                node_dir=tmp_path / "does-not-exist",
                cpuinfo_path=fake_host["cpuinfo"],
                zoneinfo_path=fake_host["zoneinfo"],
            )
            is None
        )


class TestInitTopologyWiring:
    """kerf init must attach discovered topology to the baseline."""

    def test_build_baseline_attaches_discovered_topology(self, monkeypatch):
        from kerf.init import main as init_main

        section = TopologySection(
            numa_nodes={
                0: NUMANode(
                    node_id=0,
                    memory_base=0,
                    memory_size=32 * 1024**3,
                    cpus=[128, 130],
                    distance_matrix={0: 10},
                    memory_type="dram",
                )
            }
        )
        monkeypatch.setattr(init_main, "get_valid_apic_ids_from_system", lambda: {0, 128, 130})
        monkeypatch.setattr(
            init_main,
            "get_multikernel_memory_pools_from_iomem",
            lambda: [(0x4_0000_0000, 0x1_0000_0000)],
        )
        monkeypatch.setattr(init_main, "discover_numa_topology", lambda: section)

        tree = init_main.build_baseline_from_cmdline("128,130")
        assert tree.hardware.topology is section

    def test_build_baseline_without_numa_host(self, monkeypatch):
        from kerf.init import main as init_main

        monkeypatch.setattr(init_main, "get_valid_apic_ids_from_system", lambda: {0, 128, 130})
        monkeypatch.setattr(
            init_main,
            "get_multikernel_memory_pools_from_iomem",
            lambda: [(0x4_0000_0000, 0x1_0000_0000)],
        )
        monkeypatch.setattr(init_main, "discover_numa_topology", lambda: None)

        tree = init_main.build_baseline_from_cmdline("128,130")
        assert tree.hardware.topology is None

    def test_detect_pci_device_discovers_numa_node(self, tmp_path, monkeypatch):
        from kerf.init import main as init_main

        dev_dir = tmp_path / "0000:09:00.0"
        dev_dir.mkdir()
        (dev_dir / "vendor").write_text("0x8086\n")
        (dev_dir / "device").write_text("0x1572\n")
        (dev_dir / "class").write_text("0x020000\n")
        (dev_dir / "numa_node").write_text("1\n")

        class FakeDevice:
            sys_path = str(dev_dir)
            sys_name = "0000:09:00.0"

        class FakeDevices:
            @staticmethod
            def from_path(_context, _path):
                return FakeDevice()

        class FakePyudev:
            Context = staticmethod(lambda: None)
            Devices = FakeDevices

            class DeviceNotFoundError(Exception):
                pass

        monkeypatch.setattr(init_main, "pyudev", FakePyudev)

        info = init_main.detect_pci_device("0000:09:00.0")
        assert info is not None
        assert info.compatible == "pci-network"
        assert info.numa_node == 1


class TestManualAllocationStaysAuthoritative:
    """Explicit resource specs must not have placement policies attached
    implicitly; policies apply only when requested or when auto-allocating."""

    def run_create(self, monkeypatch, args):
        from click.testing import CliRunner
        from kerf.create import main as create_main

        tree = make_tree_with_topology()

        class FakeManager:
            def read_baseline(self):
                return tree

            def has_instance(self, _name):
                return False

        monkeypatch.setattr(create_main, "DeviceTreeManager", FakeManager)
        runner = CliRunner()
        result = runner.invoke(create_main.create, args + ["--dry-run"], obj={})
        assert result.exit_code == 0, result.output
        return result.output

    def test_manual_cpus_record_no_affinity(self, monkeypatch):
        output = self.run_create(monkeypatch, ["web", "--cpus=128,136", "--memory=1GB"])
        assert "CPU Affinity" not in output

    def test_auto_allocation_defaults_to_compact(self, monkeypatch):
        output = self.run_create(monkeypatch, ["web", "--cpu-count=2", "--memory=1GB"])
        assert "CPU Affinity: compact" in output

    def test_manual_cpus_with_explicit_affinity_kept(self, monkeypatch):
        output = self.run_create(
            monkeypatch, ["web", "--cpus=128,136", "--cpu-affinity=spread", "--memory=1GB"]
        )
        assert "CPU Affinity: spread" in output


class TestPciNumaNodeDiscovery:
    def test_reads_numa_node(self, tmp_path):
        from kerf.topology import read_pci_numa_node

        (tmp_path / "numa_node").write_text("1\n")
        assert read_pci_numa_node(tmp_path) == 1

    def test_negative_means_unknown(self, tmp_path):
        from kerf.topology import read_pci_numa_node

        (tmp_path / "numa_node").write_text("-1\n")
        assert read_pci_numa_node(tmp_path) is None

    def test_missing_file_means_unknown(self, tmp_path):
        from kerf.topology import read_pci_numa_node

        assert read_pci_numa_node(tmp_path) is None
