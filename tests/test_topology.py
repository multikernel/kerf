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

"""Reading the host NUMA topology out of sysfs and /proc/cpuinfo."""

from kerf.topology import cpu_numa_nodes, logical_to_apic, node_for_cpus


def _cpuinfo(tmp_path, apic_of_cpu):
    path = tmp_path / "cpuinfo"
    blocks = []
    for cpu, apic in sorted(apic_of_cpu.items()):
        blocks.append(
            f"processor\t: {cpu}\n"
            "vendor_id\t: GenuineIntel\n"
            "cpu family\t: 25\n"
            f"apicid\t\t: {apic}\n"
            f"initial apicid\t: {apic}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


def _node_root(tmp_path, cpulists):
    root = tmp_path / "node"
    root.mkdir()
    for node, cpulist in cpulists.items():
        node_dir = root / f"node{node}"
        node_dir.mkdir()
        (node_dir / "cpulist").write_text(cpulist + "\n", encoding="utf-8")
    return str(root)


def test_logical_cpus_map_to_apic_ids(tmp_path):
    cpuinfo = _cpuinfo(tmp_path, {0: 0, 1: 2, 2: 4})

    assert logical_to_apic(cpuinfo) == {0: 0, 1: 2, 2: 4}


def test_single_node_places_every_cpu(tmp_path):
    cpuinfo = _cpuinfo(tmp_path, {0: 0, 1: 2, 2: 4, 3: 6})
    node_root = _node_root(tmp_path, {0: "0-3"})

    mapping = cpu_numa_nodes(node_root, cpuinfo)

    assert mapping == {0: 0, 2: 0, 4: 0, 6: 0}
    assert node_for_cpus([2, 4], mapping) == 0


def test_two_nodes_keep_their_own_cpus(tmp_path):
    cpuinfo = _cpuinfo(tmp_path, {0: 0, 1: 2, 2: 4, 3: 6})
    node_root = _node_root(tmp_path, {0: "0,1", 1: "2-3"})

    mapping = cpu_numa_nodes(node_root, cpuinfo)

    assert mapping == {0: 0, 2: 0, 4: 1, 6: 1}
    assert node_for_cpus([4, 6], mapping) == 1


def test_cpus_split_across_nodes_follow_the_lowest_apic_id(tmp_path):
    cpuinfo = _cpuinfo(tmp_path, {0: 0, 1: 2, 2: 4, 3: 6})
    node_root = _node_root(tmp_path, {0: "0,1", 1: "2-3"})

    mapping = cpu_numa_nodes(node_root, cpuinfo)

    assert node_for_cpus([2, 4], mapping) == 0
    assert node_for_cpus([4, 2], mapping) == 0


def test_offline_cpus_are_absent_from_the_mapping(tmp_path):
    # The pool's own CPUs leave /proc/cpuinfo and the node cpulist.
    cpuinfo = _cpuinfo(tmp_path, {0: 0})
    node_root = _node_root(tmp_path, {0: "0"})

    mapping = cpu_numa_nodes(node_root, cpuinfo)

    assert node_for_cpus([2, 4], mapping) is None


def test_missing_files_leave_the_node_undecided(tmp_path):
    missing = str(tmp_path / "nowhere")

    assert not cpu_numa_nodes(missing, missing)
    assert node_for_cpus([0, 1], {}) is None

    cpuinfo = _cpuinfo(tmp_path, {0: 0})
    assert not cpu_numa_nodes(missing, cpuinfo)
    assert not cpu_numa_nodes(_node_root(tmp_path, {0: "0"}), missing)


def test_memoryless_node_with_no_cpus_is_skipped(tmp_path):
    cpuinfo = _cpuinfo(tmp_path, {0: 0, 1: 2})
    node_root = _node_root(tmp_path, {0: "0-1", 1: ""})

    assert cpu_numa_nodes(node_root, cpuinfo) == {0: 0, 2: 0}
