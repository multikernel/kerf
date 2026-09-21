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
Host CPU topology, as sysfs and /proc/cpuinfo describe it.

Multikernel names CPUs by their physical IDs, the one thing all kernels on
a machine agree on: APIC ids on x86, MPIDR affinity values on arm64.
"""

import os
import re
import struct
from pathlib import Path
from typing import Dict, Iterable, List, Optional

NODE_ROOT = "/sys/devices/system/node"
CPUINFO = "/proc/cpuinfo"
CPU_ROOT = "/sys/devices/system/cpu"
MADT = "/sys/firmware/acpi/tables/APIC"

# ACPI MADT: the table header, and the GICC entry that describes an arm64 CPU
MADT_HEADER_SIZE = 44
MADT_GICC = 0x0B
MADT_GICC_UID = 8
MADT_GICC_MPIDR = 68
MADT_GICC_ENABLED = 1

_NODE_DIR = re.compile(r"^node(\d+)$")


def _parse_cpulist(text: str) -> List[int]:
    """Expand a sysfs cpulist ("0-3,8") into logical CPU numbers."""
    cpus: List[int] = []
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start, _, end = part.partition("-")
                cpus.extend(range(int(start), int(end) + 1))
            else:
                cpus.append(int(part))
        except ValueError:
            continue
    return cpus


def logical_to_apic(cpuinfo_path: str = CPUINFO) -> Dict[int, int]:
    """
    Map logical CPU number to APIC id.

    Args:
        cpuinfo_path: Path to read instead of /proc/cpuinfo

    Returns:
        Mapping of logical CPU number to APIC id, empty if unreadable
    """
    mapping: Dict[int, int] = {}
    processor: Optional[int] = None
    try:
        with open(cpuinfo_path, "r", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key, value = key.strip(), value.strip()
                if key == "processor":
                    processor = _int_or_none(value)
                elif key == "apicid" and processor is not None:
                    apic = _int_or_none(value)
                    if apic is not None:
                        mapping[processor] = apic
                    processor = None
    except OSError:
        return {}
    return mapping


def _madt_mpidrs(madt_path: str) -> Dict[int, int]:
    """Map ACPI processor UID to MPIDR from the MADT's GICC entries."""
    try:
        with open(madt_path, "rb") as f:
            madt = f.read()
    except OSError:
        return {}

    mpidr_of: Dict[int, int] = {}
    offset = MADT_HEADER_SIZE
    while offset + 2 <= len(madt):
        entry_type, length = madt[offset], madt[offset + 1]
        if length < 2:
            break
        if entry_type == MADT_GICC and length >= MADT_GICC_MPIDR + 8:
            uid, flags = struct.unpack_from("<II", madt, offset + MADT_GICC_UID)
            if flags & MADT_GICC_ENABLED:
                mpidr_of[uid] = struct.unpack_from("<Q", madt, offset + MADT_GICC_MPIDR)[0]
        offset += length
    return mpidr_of


def logical_to_mpidr(cpu_root: str = CPU_ROOT, madt_path: str = MADT) -> Dict[int, int]:
    """
    Map logical CPU number to MPIDR affinity on arm64.

    /proc/cpuinfo does not carry it. A CPU's device tree node does, in its
    reg, and on an ACPI host the MADT does, keyed by the processor UID that
    the CPU's firmware node reports. Both stay in sysfs for a CPU that was
    given to the pool, unlike its /proc/cpuinfo entry on x86.

    Args:
        cpu_root: Directory to read instead of /sys/devices/system/cpu
        madt_path: File to read instead of /sys/firmware/acpi/tables/APIC

    Returns:
        Mapping of logical CPU number to MPIDR, empty if unreadable
    """
    mapping: Dict[int, int] = {}
    madt: Optional[Dict[int, int]] = None
    try:
        entries = os.listdir(cpu_root)
    except OSError:
        return {}

    for entry in entries:
        match = re.fullmatch(r"cpu(\d+)", entry)
        if not match:
            continue
        cpu, cpu_dir = int(match.group(1)), os.path.join(cpu_root, entry)
        try:
            with open(os.path.join(cpu_dir, "of_node", "reg"), "rb") as f:
                reg = f.read()
            if len(reg) in (4, 8):
                mapping[cpu] = int.from_bytes(reg, "big")
            continue
        except OSError:
            pass
        try:
            with open(os.path.join(cpu_dir, "firmware_node", "uid"), "r", encoding="utf-8") as f:
                uid = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if madt is None:
            madt = _madt_mpidrs(madt_path)
        if uid in madt:
            mapping[cpu] = madt[uid]
    return mapping


def logical_to_physical(
    cpuinfo_path: str = CPUINFO, cpu_root: str = CPU_ROOT, madt_path: str = MADT
) -> Dict[int, int]:
    """
    Map logical CPU number to the physical ID multikernel names the CPU by.

    Returns:
        APIC ids where /proc/cpuinfo has them, MPIDRs otherwise; empty if
        neither can be read
    """
    return logical_to_apic(cpuinfo_path) or logical_to_mpidr(cpu_root, madt_path)


def _int_or_none(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def cpu_numa_nodes(
    node_root: str = NODE_ROOT,
    cpuinfo_path: str = CPUINFO,
    cpu_root: str = CPU_ROOT,
    madt_path: str = MADT,
) -> Dict[int, int]:
    """
    Map physical CPU id to NUMA node for every CPU the host still reports.

    Args:
        node_root: Path to read instead of /sys/devices/system/node
        cpuinfo_path: Path to read instead of /proc/cpuinfo
        cpu_root: Path to read instead of /sys/devices/system/cpu
        madt_path: Path to read instead of /sys/firmware/acpi/tables/APIC

    Returns:
        Mapping of physical CPU id to NUMA node, empty if the topology is unreadable
    """
    apic_of = logical_to_physical(cpuinfo_path, cpu_root, madt_path)
    if not apic_of:
        return {}

    try:
        entries = sorted(Path(node_root).iterdir())
    except OSError:
        return {}

    nodes: Dict[int, int] = {}
    for entry in entries:
        match = _NODE_DIR.match(entry.name)
        if not match:
            continue
        try:
            cpulist = (entry / "cpulist").read_text(encoding="utf-8")
        except OSError:
            continue
        for cpu in _parse_cpulist(cpulist):
            apic = apic_of.get(cpu)
            if apic is not None:
                nodes[apic] = int(match.group(1))
    return nodes


def node_for_cpus(apic_ids: Iterable[int], mapping: Dict[int, int]) -> Optional[int]:
    """
    Pick the NUMA node these CPUs belong to.

    A request has to name one node, so CPUs spread over several follow the
    lowest APIC id rather than leaving the choice to the kernel.

    Args:
        apic_ids: APIC ids of the requested CPUs
        mapping: APIC id to NUMA node mapping from cpu_numa_nodes()

    Returns:
        The chosen node, or None if no requested CPU has a known node
    """
    known = sorted(apic for apic in apic_ids if apic in mapping)
    if not known:
        return None
    nodes = {mapping[apic] for apic in known}
    if len(nodes) == 1:
        return nodes.pop()
    return mapping[known[0]]
