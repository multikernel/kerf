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

import pytest

from kerf.topology import (
    cpu_id_name,
    cpu_numa_nodes,
    logical_to_apic,
    logical_to_mpidr,
    logical_to_physical,
    node_for_cpus,
)


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


def _arm64_cpu_root(tmp_path, cpus):
    """cpus: logical number -> ("dt", reg bytes) or ("acpi", uid)"""
    root = tmp_path / "cpu"
    root.mkdir()
    (root / "cpufreq").mkdir()
    for cpu, (kind, value) in cpus.items():
        node = root / f"cpu{cpu}" / ("of_node" if kind == "dt" else "firmware_node")
        node.mkdir(parents=True)
        if kind == "dt":
            (node / "reg").write_bytes(value)
        else:
            (node / "uid").write_text(f"{value}\n", encoding="utf-8")
    return str(root)


def _madt(tmp_path, gicc):
    """gicc: list of (uid, mpidr, enabled)"""
    table = bytearray(44)
    table[0:4] = b"APIC"
    for uid, mpidr, enabled in gicc:
        entry = bytearray(80)
        entry[0], entry[1] = 0x0B, 80
        entry[8:12] = uid.to_bytes(4, "little")
        entry[12:16] = (1 if enabled else 0).to_bytes(4, "little")
        entry[68:76] = mpidr.to_bytes(8, "little")
        table += entry
    table += bytes([0x0C, 24]) + bytes(22)  # a GIC distributor entry to skip
    path = tmp_path / "APIC"
    path.write_bytes(bytes(table))
    return str(path)


def test_arm64_dt_host_reads_mpidr_from_cpu_nodes(tmp_path):
    cpu_root = _arm64_cpu_root(
        tmp_path,
        {0: ("dt", (0).to_bytes(4, "big")), 1: ("dt", (0x100).to_bytes(8, "big"))},
    )

    assert logical_to_mpidr(cpu_root, str(tmp_path / "none")) == {0: 0, 1: 0x100}


def test_arm64_acpi_host_reads_mpidr_from_madt(tmp_path):
    cpu_root = _arm64_cpu_root(tmp_path, {0: ("acpi", 7), 1: ("acpi", 9), 2: ("acpi", 11)})
    madt = _madt(tmp_path, [(9, 0x101, True), (7, 0x100, True), (11, 0x200, False)])

    assert logical_to_mpidr(cpu_root, madt) == {0: 0x100, 1: 0x101}


def test_physical_ids_are_mpidrs_where_cpuinfo_has_no_apic_ids(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nBogoMIPS\t: 125.00\n", encoding="utf-8")
    cpu_root = _arm64_cpu_root(tmp_path, {0: ("dt", (2).to_bytes(8, "big"))})
    node_root = _node_root(tmp_path, {0: "0"})

    assert logical_to_physical(str(cpuinfo), cpu_root, "") == {0: 2}
    assert cpu_numa_nodes(node_root, str(cpuinfo), cpu_root, "") == {2: 0}


def test_physical_ids_are_apic_ids_on_x86(tmp_path):
    cpuinfo = _cpuinfo(tmp_path, {0: 0, 1: 2})

    assert logical_to_physical(cpuinfo, str(tmp_path / "none"), "") == {0: 0, 1: 2}


@pytest.mark.parametrize("machine, name", [("x86_64", "APIC ID"), ("aarch64", "MPIDR")])
def test_messages_name_the_physical_id_of_the_architecture(monkeypatch, machine, name):
    monkeypatch.setattr("platform.machine", lambda: machine)
    assert cpu_id_name() == name
