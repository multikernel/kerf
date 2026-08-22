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

"""The NUMA node 'kerf init' picks for a memory request that names none."""

import pytest
from click.testing import CliRunner

from kerf.dtc.overlay import OverlayGenerator
from kerf.dtc.parser import DeviceTreeParser
from kerf.exceptions import ParseError
from kerf.init import main
from kerf.models import (
    CPUAllocation,
    GlobalDeviceTree,
    HardwareInventory,
    MemoryAllocation,
    PoolMemoryRegion,
)
from kerf.pool_diff import ANY_NODE, compute_pool_diff

MB = 1 << 20
GB = 1 << 30


def _live_pool(cpus, regions):
    """What the kernel reports for a pool that already holds these resources."""
    return GlobalDeviceTree(
        hardware=HardwareInventory(
            cpus=CPUAllocation(total=16, host_reserved=[0], available=cpus),
            memory=MemoryAllocation(total_bytes=0, host_reserved_bytes=0, regions=regions),
            devices={},
        ),
        instances={},
        device_references={},
    )


@pytest.fixture(name="topology")
def topology_fixture(monkeypatch):
    """Let a test state the APIC id to node mapping the host reports."""
    def install(mapping):
        monkeypatch.setattr(main, "cpu_numa_nodes", lambda: dict(mapping))
    install({})
    return install


def test_cpus_on_one_node_pin_the_request_there(topology):
    topology({1: 1, 2: 1, 3: 1})

    requested, note = main.resolve_memory_nodes({ANY_NODE: 512 * MB}, [1, 2, 3])

    assert requested == {1: 512 * MB}
    assert note == "Memory: 512 MB on node 1 (from CPUs 1-3)"


def test_cpus_split_across_nodes_follow_the_lowest_apic_id(topology):
    topology({2: 1, 5: 0})

    requested, note = main.resolve_memory_nodes({ANY_NODE: GB}, [5, 2])

    assert requested == {1: GB}
    assert "from CPUs 2,5" in note


def test_pool_cpus_fall_back_to_the_chunks_they_run_on(topology):
    # A CPU the pool already holds is offline, so the topology cannot place it.
    topology({0: 0})
    regions = [PoolMemoryRegion(0x1_0000_0000, GB, 1)]

    requested, note = main.resolve_memory_nodes(
        {ANY_NODE: GB}, [1, 2, 3], pool_cpus={1, 2, 3}, pool_regions=regions)

    assert requested == {1: GB}
    assert "chunks the pool already holds" in note


def test_chunks_on_several_nodes_do_not_decide(topology):
    topology({0: 0})
    regions = [PoolMemoryRegion(0x1_0000_0000, GB, 0),
               PoolMemoryRegion(0x2_0000_0000, GB, 1)]

    requested, note = main.resolve_memory_nodes(
        {ANY_NODE: GB}, [1, 2], pool_cpus={1, 2}, pool_regions=regions)

    assert requested == {0: GB}
    assert "no NUMA topology available" in note


def test_no_numa_information_defaults_to_node_zero(topology):
    topology({})

    requested, note = main.resolve_memory_nodes({ANY_NODE: 512 * MB}, [1, 2, 3])

    assert requested == {0: 512 * MB}
    assert note == "Memory: 512 MB on node 0 (no NUMA topology available, defaulting to node 0)"


def test_explicit_nodes_are_left_alone(topology):
    topology({1: 1})

    requested, note = main.resolve_memory_nodes({0: GB, 1: 2 * GB}, [1])

    assert requested == {0: GB, 1: 2 * GB}
    assert note is None


def test_a_resolved_size_joins_an_explicit_request_on_the_same_node(topology):
    topology({4: 0})

    requested, _ = main.resolve_memory_nodes({0: GB, ANY_NODE: GB}, [4])

    assert requested == {0: 2 * GB}


def test_the_request_built_from_the_command_line_names_a_node(topology, monkeypatch):
    topology({0: 0, 1: 1, 2: 1, 3: 1})
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 1, 2, 3})

    tree = main.build_baseline_from_cmdline("1-3", memory="512MB")

    assert tree.hardware.memory.requested == {1: 512 * MB}


def test_the_diff_sees_an_explicit_node(topology, monkeypatch):
    topology({0: 0, 1: 1, 2: 1, 3: 1})
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 1, 2, 3})
    current = _live_pool([1, 2, 3], [PoolMemoryRegion(0x1_0000_0000, GB, 1)])

    requested = main.build_baseline_from_cmdline("1-3", memory="2GB",
                                                 pool_cpus=main.pool_apic_ids(current))
    diff = compute_pool_diff(current, requested)

    assert diff.memory_to_pool == [(1, GB)]


_DTS_UNPINNED = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;

        memory@0 {
            size = <0x0 0x40000000>;
        };
    };
};
"""


def test_a_baseline_file_without_numa_node_id_is_resolved_too(topology):
    topology({4: 1, 5: 1})
    tree = DeviceTreeParser().parse_dts(_DTS_UNPINNED)
    assert tree.hardware.memory.requested == {ANY_NODE: GB}

    requested, note = main.resolve_memory_nodes(
        tree.hardware.memory.requested, tree.hardware.cpus.available)

    assert requested == {1: GB}
    assert "from CPUs 4-5" in note


class _FakeBaselineManager:
    """Stands in for BaselineManager: no pool yet, records the baseline write."""

    def __init__(self):
        self.written = []

    def validate_baseline(self, tree):
        """Accept anything: the baseline shape is covered elsewhere."""

    def read_baseline(self):
        raise ParseError("No memory description in /resources")

    def write_baseline(self, tree):
        self.written.append(tree)


class _FakeManager:
    """Stands in for DeviceTreeManager: never touches sysfs."""

    def __init__(self, *_args, **_kwargs):
        self.overlay_gen = OverlayGenerator()


def test_init_resolves_a_baseline_file_before_writing_it(topology, monkeypatch, tmp_path):
    topology({4: 1, 5: 1})
    baseline_mgr = _FakeBaselineManager()
    monkeypatch.setattr(main, "mount_multikernel_fs", lambda verbose=False: None)
    monkeypatch.setattr(main, "DeviceTreeManager", _FakeManager)
    monkeypatch.setattr(main, "get_busy_chunks_from_iomem", set)
    monkeypatch.setattr(main, "BaselineManager", lambda *a, **kw: baseline_mgr)
    dts = tmp_path / "baseline.dts"
    dts.write_text(_DTS_UNPINNED, encoding="utf-8")

    result = CliRunner().invoke(main.init, [f"--input={dts}"])

    assert result.exit_code == 0, result.output
    assert "Memory: 1024 MB on node 1 (from CPUs 4-5)" in result.output
    assert baseline_mgr.written[-1].hardware.memory.requested == {1: GB}
