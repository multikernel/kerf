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

"""Host NUMA topology, as sysfs and /proc/cpuinfo describe it."""

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

NODE_ROOT = "/sys/devices/system/node"
CPUINFO = "/proc/cpuinfo"

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


def _int_or_none(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def cpu_numa_nodes(node_root: str = NODE_ROOT, cpuinfo_path: str = CPUINFO) -> Dict[int, int]:
    """
    Map APIC id to NUMA node for every CPU the host still reports.

    Args:
        node_root: Path to read instead of /sys/devices/system/node
        cpuinfo_path: Path to read instead of /proc/cpuinfo

    Returns:
        Mapping of APIC id to NUMA node, empty if the topology is unreadable
    """
    apic_of = logical_to_apic(cpuinfo_path)
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
