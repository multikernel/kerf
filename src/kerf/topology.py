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
Host topology discovery.

Builds a TopologySection describing the host's NUMA layout from sysfs and
procfs. The multikernel stack identifies CPUs by physical ID (APIC ID on
x86), while sysfs topology files are keyed by logical CPU number, so
discovery translates every CPU list through the logical-to-physical map
from /proc/cpuinfo.
"""

import re
from pathlib import Path
from typing import Dict, Optional, Union

from .models import NUMANode, TopologySection

PAGE_SIZE = 4096

SYSFS_NODE_DIR = Path("/sys/devices/system/node")
PROC_CPUINFO = Path("/proc/cpuinfo")
PROC_ZONEINFO = Path("/proc/zoneinfo")

PathLike = Union[str, Path]


def parse_cpu_list(cpulist: str) -> list:
    """Parse a kernel cpulist string ("0-3,8,10-11") into logical CPU IDs."""
    cpus = []
    for part in cpulist.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            cpus.extend(range(int(start), int(end) + 1))
        else:
            cpus.append(int(part))
    return cpus


def read_logical_to_physical_cpu_map(cpuinfo_path: PathLike = PROC_CPUINFO) -> Dict[int, int]:
    """
    Build the logical CPU to physical CPU ID (APIC ID) map from /proc/cpuinfo.

    Returns an empty dict if the file is unavailable or contains no
    processor/apicid pairs.
    """
    mapping: Dict[int, int] = {}
    current_processor = None
    try:
        with open(cpuinfo_path, "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "processor":
                    try:
                        current_processor = int(value)
                    except ValueError:
                        current_processor = None
                elif key == "apicid" and current_processor is not None:
                    try:
                        mapping[current_processor] = int(value)
                    except ValueError:
                        pass
    except OSError:
        return {}
    return mapping


def _read_node_memory_ranges(zoneinfo_path: PathLike) -> Dict[int, tuple]:
    """
    Derive per-node physical memory spans from /proc/zoneinfo.

    Returns {node_id: (base_bytes, size_bytes)}. The span may include
    holes; it is the [min(start_pfn), max(start_pfn + spanned)) envelope
    across the node's zones, which is what NUMA-aware placement needs to
    classify an address.
    """
    node_pfns: Dict[int, list] = {}
    current_node = None
    start_pfn = None
    spanned = None

    def commit():
        if current_node is None or start_pfn is None or not spanned:
            return
        node_pfns.setdefault(current_node, []).append((start_pfn, start_pfn + spanned))

    try:
        with open(zoneinfo_path, "r", encoding="utf-8") as f:
            for line in f:
                header = re.match(r"Node (\d+), zone", line)
                if header:
                    commit()
                    current_node = int(header.group(1))
                    start_pfn = None
                    spanned = None
                    continue
                spanned_match = re.match(r"\s+spanned\s+(\d+)", line)
                if spanned_match:
                    spanned = int(spanned_match.group(1))
                    continue
                start_match = re.match(r"\s+start_pfn:\s+(\d+)", line)
                if start_match:
                    start_pfn = int(start_match.group(1))
            commit()
    except OSError:
        return {}

    ranges = {}
    for node_id, spans in node_pfns.items():
        base = min(s for s, _ in spans)
        end = max(e for _, e in spans)
        ranges[node_id] = (base * PAGE_SIZE, (end - base) * PAGE_SIZE)
    return ranges


def discover_numa_topology(
    node_dir: PathLike = SYSFS_NODE_DIR,
    cpuinfo_path: PathLike = PROC_CPUINFO,
    zoneinfo_path: PathLike = PROC_ZONEINFO,
) -> Optional[TopologySection]:
    """
    Discover the host NUMA topology.

    Returns a TopologySection whose CPU lists contain physical CPU IDs
    (APIC IDs), or None if the host exposes no NUMA information.
    """
    node_dir = Path(node_dir)
    if not node_dir.is_dir():
        return None

    node_ids = []
    for entry in node_dir.iterdir():
        match = re.fullmatch(r"node(\d+)", entry.name)
        if match and entry.is_dir():
            node_ids.append(int(match.group(1)))
    if not node_ids:
        return None
    node_ids.sort()

    cpu_map = read_logical_to_physical_cpu_map(cpuinfo_path)
    memory_ranges = _read_node_memory_ranges(zoneinfo_path)

    numa_nodes: Dict[int, NUMANode] = {}
    for node_id in node_ids:
        node_path = node_dir / f"node{node_id}"

        physical_cpus = []
        try:
            cpulist = (node_path / "cpulist").read_text(encoding="utf-8")
            for logical_cpu in parse_cpu_list(cpulist):
                if logical_cpu in cpu_map:
                    physical_cpus.append(cpu_map[logical_cpu])
        except OSError:
            pass

        distance_matrix: Dict[int, int] = {}
        try:
            distances = (node_path / "distance").read_text(encoding="utf-8").split()
            distance_matrix = {
                target: int(distance) for target, distance in zip(node_ids, distances)
            }
        except (OSError, ValueError):
            pass

        memory_base, memory_size = memory_ranges.get(node_id, (0, 0))

        numa_nodes[node_id] = NUMANode(
            node_id=node_id,
            memory_base=memory_base,
            memory_size=memory_size,
            cpus=sorted(physical_cpus),
            distance_matrix=distance_matrix,
            memory_type="dram",
        )

    return TopologySection(numa_nodes=numa_nodes)


def read_pci_numa_node(device_sys_path: PathLike) -> Optional[int]:
    """
    Read the NUMA node of a PCI device from its sysfs directory.

    Returns None when the file is missing or the kernel reports -1
    (no locality information).
    """
    try:
        value = int((Path(device_sys_path) / "numa_node").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value >= 0 else None
