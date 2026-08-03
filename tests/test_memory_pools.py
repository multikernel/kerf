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
Tests for per-NUMA-node memory pools.
"""

import pytest

from kerf.dtc.extractor import InstanceExtractor
from kerf.dtc.parser import DeviceTreeParser
from kerf.models import (
    CPUAllocation,
    GlobalDeviceTree,
    HardwareInventory,
    MemoryAllocation,
    MemoryPool,
    NUMANode,
    TopologySection,
)

GB = 1024**3


def make_pools_memory():
    """Two per-node pools: node 0 at 16GB, node 1 at 48GB."""
    pools = [
        MemoryPool(base=0x4_0000_0000, size=8 * GB, numa_node=0),
        MemoryPool(base=0xC_0000_0000, size=8 * GB, numa_node=1),
    ]
    return MemoryAllocation(
        total_bytes=0xC_0000_0000 + 8 * GB,
        host_reserved_bytes=0x4_0000_0000,
        memory_pool_base=0x4_0000_0000,
        memory_pool_bytes=16 * GB,
        pools=pools,
    )


def make_pools_tree():
    cpus = CPUAllocation(total=8, host_reserved=[0], available=[128, 130, 136, 138])
    topology = TopologySection(
        numa_nodes={
            0: NUMANode(
                node_id=0,
                memory_base=0x0,
                memory_size=0x8_0000_0000,
                cpus=[128, 130],
                distance_matrix={0: 10, 1: 21},
                memory_type="dram",
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x8_0000_0000,
                memory_size=0x8_0000_0000,
                cpus=[136, 138],
                distance_matrix={0: 21, 1: 10},
                memory_type="dram",
            ),
        }
    )
    hardware = HardwareInventory(
        cpus=cpus, memory=make_pools_memory(), topology=topology, devices={}
    )
    return GlobalDeviceTree(hardware=hardware, instances={}, device_references={})


class TestMemoryPoolModel:
    def test_get_pools_returns_declared_pools(self):
        memory = make_pools_memory()
        pools = memory.get_pools()
        assert len(pools) == 2
        assert pools[0].numa_node == 0
        assert pools[1].base == 0xC_0000_0000

    def test_get_pools_synthesizes_single_pool_for_legacy_allocation(self):
        memory = MemoryAllocation(
            total_bytes=16 * GB,
            host_reserved_bytes=2 * GB,
            memory_pool_base=0x8000_0000,
            memory_pool_bytes=14 * GB,
        )
        pools = memory.get_pools()
        assert len(pools) == 1
        assert pools[0].base == 0x8000_0000
        assert pools[0].size == 14 * GB
        assert pools[0].numa_node is None

    def test_pool_end(self):
        pool = MemoryPool(base=0x1000, size=0x2000, numa_node=None)
        assert pool.end == 0x3000


class TestMemoryPoolsDtbRoundtrip:
    def roundtrip(self, tree):
        dtb = InstanceExtractor().generate_global_dtb(tree)
        return DeviceTreeParser().parse_dtb_from_bytes(dtb)

    def test_pools_survive_roundtrip(self):
        parsed = self.roundtrip(make_pools_tree())
        pools = parsed.hardware.memory.pools
        assert pools is not None
        assert len(pools) == 2
        assert (pools[0].base, pools[0].size, pools[0].numa_node) == (0x4_0000_0000, 8 * GB, 0)
        assert (pools[1].base, pools[1].size, pools[1].numa_node) == (0xC_0000_0000, 8 * GB, 1)

    def test_pool_without_node_survives_roundtrip(self):
        tree = make_pools_tree()
        tree.hardware.memory.pools = [MemoryPool(base=0x4_0000_0000, size=8 * GB, numa_node=None)]
        parsed = self.roundtrip(tree)
        pools = parsed.hardware.memory.pools
        assert len(pools) == 1
        assert pools[0].numa_node is None

    def test_legacy_single_pool_has_no_pools_section(self):
        tree = make_pools_tree()
        tree.hardware.memory.pools = None
        parsed = self.roundtrip(tree)
        assert parsed.hardware.memory.pools is None
        assert parsed.hardware.memory.memory_pool_base == 0x4_0000_0000


class TestPoolAwareAllocation:
    """find_available_memory_base must respect pool boundaries and NUMA filters."""

    def find(self, tree, size, **kwargs):
        from kerf.resources import find_available_memory_base

        return find_available_memory_base(tree, size, use_iomem=False, **kwargs)

    def add_instance(self, tree, name, base, size):
        from kerf.models import Instance, InstanceResources

        tree.instances[name] = Instance(
            name=name,
            id=len(tree.instances) + 1,
            resources=InstanceResources(cpus=[], memory_base=base, memory_bytes=size, devices=[]),
        )

    def test_allocates_from_first_pool(self):
        tree = make_pools_tree()
        assert self.find(tree, 1 * GB) == 0x4_0000_0000

    def test_numa_filter_selects_matching_pool(self):
        tree = make_pools_tree()
        assert self.find(tree, 1 * GB, numa_nodes=[1]) == 0xC_0000_0000

    def test_numa_filter_without_matching_pool_returns_none(self):
        tree = make_pools_tree()
        assert self.find(tree, 1 * GB, numa_nodes=[7]) is None

    def test_full_pool_spills_to_next_pool(self):
        tree = make_pools_tree()
        self.add_instance(tree, "big", 0x4_0000_0000, 8 * GB)
        assert self.find(tree, 1 * GB) == 0xC_0000_0000

    def test_allocation_never_spans_pool_gap(self):
        tree = make_pools_tree()
        self.add_instance(tree, "head", 0x4_0000_0000, 7 * GB)
        # 2GB does not fit in the 1GB tail of pool 0; must come from pool 1
        assert self.find(tree, 2 * GB) == 0xC_0000_0000

    def test_gap_between_instances_within_pool(self):
        tree = make_pools_tree()
        self.add_instance(tree, "a", 0x4_0000_0000, 1 * GB)
        self.add_instance(tree, "b", 0x4_0000_0000 + 2 * GB, 1 * GB)
        assert self.find(tree, 1 * GB) == 0x4_0000_0000 + 1 * GB


class TestPoolAwareValidation:
    def validate(self, tree, base, size):
        from kerf.resources import validate_memory_allocation

        validate_memory_allocation(tree, base, size)

    def test_region_within_second_pool_is_valid(self):
        self.validate(make_pools_tree(), 0xC_0000_0000, 1 * GB)

    def test_region_in_gap_between_pools_rejected(self):
        from kerf.exceptions import ResourceError

        with pytest.raises(ResourceError):
            self.validate(make_pools_tree(), 0x8_0000_0000, 1 * GB)

    def test_region_crossing_pool_end_rejected(self):
        from kerf.exceptions import ResourceError

        with pytest.raises(ResourceError):
            self.validate(make_pools_tree(), 0x4_0000_0000 + 7 * GB, 2 * GB)


class TestValidatorMultiPool:
    def validate_tree(self, tree):
        from kerf.dtc.validator import MultikernelValidator

        return MultikernelValidator().validate(tree)

    def make_instance(self, base, size):
        from kerf.models import Instance, InstanceResources

        return Instance(
            name="inst",
            id=1,
            resources=InstanceResources(
                cpus=[128], memory_base=base, memory_bytes=size, devices=[]
            ),
        )

    def test_instance_in_second_pool_passes(self):
        tree = make_pools_tree()
        tree.instances["inst"] = self.make_instance(0xC_0000_0000, 1 * GB)
        result = self.validate_tree(tree)
        assert result.is_valid, result.errors

    def test_instance_in_pool_gap_fails(self):
        tree = make_pools_tree()
        tree.instances["inst"] = self.make_instance(0x8_0000_0000, 1 * GB)
        result = self.validate_tree(tree)
        assert not result.is_valid


class TestInitPerNodePools:
    """kerf init must allocate one pool per requested NUMA node."""

    def setup_init(self, monkeypatch, iomem_pools, topology=None):
        from kerf.init import main as init_main

        allocations = []

        def fake_allocate(size_bytes, node=-1):
            base = {0: 0x4_0000_0000, 1: 0xC_0000_0000, -1: 0x4_0000_0000}[node]
            allocations.append((size_bytes, node))
            return base

        monkeypatch.setattr(init_main, "get_valid_apic_ids_from_system", lambda: {0, 128, 136})
        monkeypatch.setattr(init_main, "allocate_multikernel_pool", fake_allocate)
        monkeypatch.setattr(
            init_main, "get_multikernel_memory_pools_from_iomem", lambda: list(iomem_pools)
        )
        monkeypatch.setattr(init_main, "discover_numa_topology", lambda: topology)
        return init_main, allocations

    def test_parse_memory_pool_spec(self):
        from kerf.init.main import parse_memory_pool_spec

        assert parse_memory_pool_spec("1GB") == [(GB, None)]
        assert parse_memory_pool_spec("8GB@0,4GB@1") == [(8 * GB, 0), (4 * GB, 1)]

    def test_parse_memory_pool_spec_rejects_mixed_entries(self):
        from kerf.init.main import parse_memory_pool_spec

        with pytest.raises(ValueError):
            parse_memory_pool_spec("8GB@0,4GB")

    def test_parse_memory_pool_spec_rejects_duplicate_nodes(self):
        from kerf.init.main import parse_memory_pool_spec

        with pytest.raises(ValueError):
            parse_memory_pool_spec("8GB@0,4GB@0")

    def test_per_node_pools_allocated_with_node(self, monkeypatch):
        init_main, allocations = self.setup_init(monkeypatch, iomem_pools=[])

        tree = init_main.build_baseline_from_cmdline("128,136", memory="8GB@0,4GB@1")

        assert allocations == [(8 * GB, 0), (4 * GB, 1)]
        pools = tree.hardware.memory.pools
        assert len(pools) == 2
        assert (pools[0].base, pools[0].size, pools[0].numa_node) == (0x4_0000_0000, 8 * GB, 0)
        assert (pools[1].base, pools[1].size, pools[1].numa_node) == (0xC_0000_0000, 4 * GB, 1)

    def test_single_anonymous_pool_stays_legacy(self, monkeypatch):
        init_main, allocations = self.setup_init(monkeypatch, iomem_pools=[])

        tree = init_main.build_baseline_from_cmdline("128,136", memory="1GB")

        assert allocations == [(GB, -1)]
        assert tree.hardware.memory.pools is None
        assert tree.hardware.memory.memory_pool_base == 0x4_0000_0000
        assert tree.hardware.memory.memory_pool_bytes == GB

    def test_existing_pools_get_nodes_from_topology(self, monkeypatch):
        topology = TopologySection(
            numa_nodes={
                0: NUMANode(0, 0x0, 0x8_0000_0000, [128], {}, "dram"),
                1: NUMANode(1, 0x8_0000_0000, 0x8_0000_0000, [136], {}, "dram"),
            }
        )
        init_main, allocations = self.setup_init(
            monkeypatch,
            iomem_pools=[(0x4_0000_0000, 8 * GB), (0xC_0000_0000, 8 * GB)],
            topology=topology,
        )

        tree = init_main.build_baseline_from_cmdline("128,136")

        assert not allocations
        pools = tree.hardware.memory.pools
        assert [pool.numa_node for pool in pools] == [0, 1]


class TestMemoryPolicyPlacement:
    """Memory policies must drive which pool an instance's memory comes from."""

    def allocate(self, tree, size, cpus, numa_nodes=None, policy=None):
        from kerf.create.main import allocate_memory_region

        return allocate_memory_region(tree, size, cpus, numa_nodes, policy)

    def test_local_places_memory_on_cpu_node(self):
        tree = make_pools_tree()
        # CPUs 136/138 are on node 1, whose pool starts at 0xC00000000
        assert self.allocate(tree, 1 * GB, [136, 138], policy="local") == 0xC_0000_0000

    def test_local_without_topology_fails(self):
        from kerf.exceptions import ResourceError

        tree = make_pools_tree()
        tree.hardware.topology = None
        with pytest.raises(ResourceError, match="local"):
            self.allocate(tree, 1 * GB, [136], policy="local")

    def test_local_with_undersized_node_pool_fails(self):
        from kerf.exceptions import ResourceError

        tree = make_pools_tree()
        tree.hardware.memory.pools[1].size = 1 * GB
        with pytest.raises(ResourceError, match="local"):
            self.allocate(tree, 2 * GB, [136], policy="local")

    def test_bind_requires_numa_nodes(self):
        from kerf.exceptions import ResourceError

        with pytest.raises(ResourceError, match="bind"):
            self.allocate(make_pools_tree(), 1 * GB, [128], policy="bind")

    def test_bind_places_memory_on_requested_node(self):
        tree = make_pools_tree()
        base = self.allocate(tree, 1 * GB, [128], numa_nodes=[1], policy="bind")
        assert base == 0xC_0000_0000

    def test_default_prefers_cpu_local_pool(self):
        tree = make_pools_tree()
        assert self.allocate(tree, 1 * GB, [136, 138]) == 0xC_0000_0000

    def test_default_falls_back_when_local_pool_too_small(self):
        tree = make_pools_tree()
        tree.hardware.memory.pools[1].size = 1 * GB
        # Node 1 pool cannot fit 2GB; default policy silently falls back
        assert self.allocate(tree, 2 * GB, [136, 138]) == 0x4_0000_0000

    def test_cli_local_policy_places_memory_on_cpu_node(self, monkeypatch):
        from click.testing import CliRunner
        from kerf.create import main as create_main

        tree = make_pools_tree()

        class FakeManager:
            def read_baseline(self):
                return tree

            def has_instance(self, _name):
                return False

        monkeypatch.setattr(create_main, "DeviceTreeManager", FakeManager)
        result = CliRunner().invoke(
            create_main.create,
            ["db", "--cpus=136,138", "--memory=1GB", "--memory-policy=local", "--dry-run"],
            obj={},
        )
        assert result.exit_code == 0, result.output
        assert "0xc00000000" in result.output


POOLS_DTS = """
/multikernel-v1/;

/ {
    compatible = "linux,multikernel-host";

    resources {
        cpus = <128 130 136 138>;

        memory-pools {
            pool@0 {
                base = <0x4 0x00000000>;
                size = <0x2 0x00000000>;
                numa-node = <0>;
            };
            pool@1 {
                base = <0xC 0x00000000>;
                size = <0x2 0x00000000>;
                numa-node = <1>;
            };
        };
    };
};
"""


class TestMemoryPoolsDtsParsing:
    def test_pools_parsed_from_dts(self):
        tree = DeviceTreeParser().parse_dts(POOLS_DTS)
        pools = tree.hardware.memory.pools
        assert pools is not None
        assert len(pools) == 2
        assert (pools[0].base, pools[0].size, pools[0].numa_node) == (0x4_0000_0000, 8 * GB, 0)
        assert (pools[1].base, pools[1].size, pools[1].numa_node) == (0xC_0000_0000, 8 * GB, 1)

    def test_envelope_derived_from_pools_when_memory_base_absent(self):
        tree = DeviceTreeParser().parse_dts(POOLS_DTS)
        memory = tree.hardware.memory
        assert memory.memory_pool_base == 0x4_0000_0000
        assert memory.memory_pool_bytes == 16 * GB
