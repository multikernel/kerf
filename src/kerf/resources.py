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
Resource allocation utilities.

This module provides helper functions for resource allocation operations.
These utilities assist with allocating CPUs, memory, and devices when
creating or updating kernel instances.
"""

from typing import List, Set, Optional
from .models import GlobalDeviceTree
from .exceptions import ResourceError


def get_available_cpus(tree: GlobalDeviceTree) -> Set[int]:
    """
    Get set of CPUs available for allocation (not allocated to any instance).

    Args:
        tree: GlobalDeviceTree to analyze

    Returns:
        Set of available CPU IDs
    """
    # Get all CPUs in the available pool
    available = set(tree.hardware.cpus.available)

    # Subtract CPUs allocated to instances
    allocated = set()
    for instance in tree.instances.values():
        allocated.update(instance.resources.cpus)

    return available - allocated


def get_allocated_cpus(tree: GlobalDeviceTree) -> Set[int]:
    """
    Get set of CPUs currently allocated to instances.

    Args:
        tree: GlobalDeviceTree to analyze

    Returns:
        Set of allocated CPU IDs
    """
    allocated = set()
    for instance in tree.instances.values():
        allocated.update(instance.resources.cpus)
    return allocated


def get_allocated_memory_regions_from_iomem() -> List[tuple[int, int]]:
    """
    Get list of allocated memory regions from /proc/iomem.

    Reads actual memory allocations from the kernel (source of truth).
    Expected format: "40000000-463fffff : mk-instance-1-web-server-region-0"

    Returns:
        List of (base_address, size_bytes) tuples
    """
    regions = []
    try:
        from pathlib import Path
        import re

        iomem_path = Path("/proc/iomem")
        if not iomem_path.exists():
            return regions

        with open(iomem_path, "r", encoding="utf-8") as f:
            for line in f:
                if "mk-instance-" in line:
                    match = re.search(r"([0-9a-fA-F]+)-([0-9a-fA-F]+)", line)
                    if match:
                        base = int(match.group(1), 16)
                        end = int(match.group(2), 16)
                        size = end - base + 1  # Inclusive range
                        regions.append((base, size))
    except (OSError, IOError, ValueError):
        pass

    return regions


def get_allocated_memory_regions(tree: GlobalDeviceTree) -> List[tuple[int, int]]:
    """
    Get list of allocated memory regions (base, size) tuples.

    This function can use either the tree (for validation/dry-run) or
    /proc/iomem (for actual kernel state). For actual allocations,
    prefer get_allocated_memory_regions_from_iomem().

    Args:
        tree: GlobalDeviceTree to analyze

    Returns:
        List of (base_address, size_bytes) tuples
    """
    regions = []
    for instance in tree.instances.values():
        if instance.resources.memory_base > 0:  # Only include if memory_base is set
            regions.append((instance.resources.memory_base, instance.resources.memory_bytes))
    return regions


def _find_gap_in_pool(
    pool, allocated_regions: List[tuple[int, int]], size_bytes: int, alignment: int
) -> Optional[int]:
    """First-fit a region of size_bytes into one pool, avoiding allocations."""
    # Only allocations overlapping this pool matter
    regions = sorted(
        (base, size)
        for base, size in allocated_regions
        if base < pool.end and base + size > pool.base
    )

    cursor = (pool.base + alignment - 1) // alignment * alignment
    for base, size in regions:
        if cursor + size_bytes <= base:
            return cursor
        cursor = max(cursor, base + size)
        cursor = (cursor + alignment - 1) // alignment * alignment

    if cursor + size_bytes <= pool.end:
        return cursor
    return None


def find_available_memory_base(
    tree: GlobalDeviceTree,
    size_bytes: int,
    alignment: int = 0x1000,
    use_iomem: bool = True,
    numa_nodes: Optional[List[int]] = None,
) -> Optional[int]:
    """
    Find available memory region for allocation.

    Args:
        tree: GlobalDeviceTree to analyze (for pool boundaries)
        size_bytes: Size of memory region needed
        alignment: Required alignment (default 4KB)
        use_iomem: If True, read actual allocations from /proc/iomem (kernel source of truth).
                   If False, use allocations from tree (for validation/dry-run).
        numa_nodes: If given, only allocate from pools on these NUMA nodes.

    Returns:
        Base address for allocation, or None if no space available
    """
    pools = tree.hardware.memory.get_pools()
    if numa_nodes is not None:
        pools = [pool for pool in pools if pool.numa_node in numa_nodes]

    if use_iomem:
        allocated_regions = get_allocated_memory_regions_from_iomem()
    else:
        allocated_regions = get_allocated_memory_regions(tree)

    for pool in pools:
        base = _find_gap_in_pool(pool, allocated_regions, size_bytes, alignment)
        if base is not None:
            return base
    return None


def validate_cpu_allocation(
    tree: GlobalDeviceTree, requested_cpus: List[int], exclude_instance: Optional[str] = None
) -> None:
    """
    Validate that requested CPUs are available for allocation.

    Args:
        tree: GlobalDeviceTree to analyze
        requested_cpus: List of CPU IDs to allocate
        exclude_instance: Instance name to exclude from conflict check
                         (for update operations)

    Raises:
        ResourceError: If CPUs are not available or invalid
    """
    available_cpus = get_available_cpus(tree)

    # Add back CPUs from excluded instance (for updates)
    if exclude_instance and exclude_instance in tree.instances:
        exclude_cpus = set(tree.instances[exclude_instance].resources.cpus)
        available_cpus.update(exclude_cpus)

    requested_set = set(requested_cpus)

    # Check all requested APIC IDs exist in hardware
    hardware_cpus = set(tree.hardware.cpus.available)
    invalid_cpus = requested_set - hardware_cpus
    if invalid_cpus:
        raise ResourceError(
            f"Invalid APIC IDs requested: {sorted(invalid_cpus)}. "
            f"Available APIC IDs: {sorted(hardware_cpus)}"
        )

    # Check APIC IDs are available
    unavailable = requested_set - available_cpus
    if unavailable:
        # Find which instances are using these APIC IDs
        conflicts = []
        for instance in tree.instances.values():
            if instance.name == exclude_instance:
                continue
            conflict_cpus = set(instance.resources.cpus) & unavailable
            if conflict_cpus:
                conflicts.append(f"{instance.name} uses APIC IDs {sorted(conflict_cpus)}")

        conflict_msg = ", ".join(conflicts) if conflicts else "allocated to other instances"
        raise ResourceError(f"APIC IDs {sorted(unavailable)} are not available ({conflict_msg})")


def validate_memory_allocation(
    tree: GlobalDeviceTree,
    memory_base: int,
    memory_bytes: int,
    exclude_instance: Optional[str] = None,
) -> None:
    """
    Validate that memory region is available for allocation.

    Args:
        tree: GlobalDeviceTree to analyze
        memory_base: Base address of memory region
        memory_bytes: Size of memory region
        exclude_instance: Instance name to exclude from conflict check
                         (for update operations)

    Raises:
        ResourceError: If memory region is invalid or conflicts
    """
    pools = tree.hardware.memory.get_pools()
    memory_end = memory_base + memory_bytes

    # The region must lie entirely within a single pool
    if not any(pool.base <= memory_base and memory_end <= pool.end for pool in pools):
        pool_list = ", ".join(
            f"{hex(pool.base)}-{hex(pool.end)}"
            + (f" (node {pool.numa_node})" if pool.numa_node is not None else "")
            for pool in pools
        )
        raise ResourceError(
            f"Memory region {hex(memory_base)}-{hex(memory_end)} is not within "
            f"any memory pool. Pools: {pool_list}"
        )

    # Check alignment (4KB)
    if memory_base % 0x1000 != 0:
        raise ResourceError(f"Memory base {hex(memory_base)} is not 4KB-aligned")

    # Check for overlaps with other instances
    for instance in tree.instances.values():
        if instance.name == exclude_instance:
            continue

        inst_base = instance.resources.memory_base
        inst_end = inst_base + instance.resources.memory_bytes

        # Check for overlap
        if not (memory_end <= inst_base or memory_base >= inst_end):
            raise ResourceError(
                f"Memory region {hex(memory_base)}-{hex(memory_end)} "
                f"overlaps with instance '{instance.name}' "
                f"({hex(inst_base)}-{hex(inst_end)})"
            )


def find_next_instance_id(tree: GlobalDeviceTree) -> int:
    """
    Find next available instance ID.

    Args:
        tree: GlobalDeviceTree to analyze

    Returns:
        Next available ID (1-511 range)

    Raises:
        ResourceError: If no IDs available
    """
    existing_ids = {inst.id for inst in tree.instances.values() if inst.id is not None}

    # Find first available ID in range 1-511
    for instance_id in range(1, 512):
        if instance_id not in existing_ids:
            return instance_id

    raise ResourceError("No available instance IDs (all 1-511 are in use)")
