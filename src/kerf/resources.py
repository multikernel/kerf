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

import re
from pathlib import Path
from typing import List, Set, Optional, Tuple

from .models import GlobalDeviceTree, PoolMemoryRegion
from .exceptions import ResourceError

IOMEM_PATH = "/proc/iomem"

#: iomem resource name the kernel registers every pool chunk under.
MULTIKERNEL_POOL_NAME = "Multikernel Memory Pool"

_IOMEM_RANGE_RE = re.compile(r"([0-9a-fA-F]+)-([0-9a-fA-F]+)\s*:\s*(.*)")


def _parse_iomem_regions(iomem_path: str) -> List[Tuple[int, int, str]]:
    """Return (base, end, name) tuples for every range line in /proc/iomem."""
    regions = []
    try:
        path = Path(iomem_path)
        if not path.exists():
            return regions
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                match = _IOMEM_RANGE_RE.search(line)
                if match:
                    base = int(match.group(1), 16)
                    end = int(match.group(2), 16)
                    regions.append((base, end, match.group(3).strip()))
    except (OSError, IOError, ValueError):
        pass
    return regions


def _pool_chunks(regions: List[Tuple[int, int, str]]) -> List[Tuple[int, int]]:
    """Pick the pool chunks out of already parsed /proc/iomem regions."""
    return [
        (base, end - base + 1)
        for base, end, name in regions
        if MULTIKERNEL_POOL_NAME in name
    ]


def _chunk_children(regions: List[Tuple[int, int, str]],
                    chunks: List[Tuple[int, int]]) -> List[Tuple[int, int, int]]:
    """Map every region nested in a pool chunk to (chunk_base, base, end)."""
    children = []
    for base, end, name in regions:
        if MULTIKERNEL_POOL_NAME in name:
            continue
        for chunk_base, chunk_size in chunks:
            if chunk_base <= base and end <= chunk_base + chunk_size - 1:
                children.append((chunk_base, base, end))
                break
    return children


def get_pool_chunks_from_iomem(iomem_path: str = IOMEM_PATH) -> List[Tuple[int, int]]:
    """
    List every multikernel pool chunk registered in /proc/iomem.

    Returns:
        (base_address, size_bytes) tuples in /proc/iomem order
    """
    return _pool_chunks(_parse_iomem_regions(iomem_path))


def get_busy_chunks_from_iomem(iomem_path: str = IOMEM_PATH) -> Set[int]:
    """
    Find the pool chunks that still hold an allocation.

    A chunk with any nested region cannot be returned to the host, so the
    pool diff avoids picking it when it has a choice.

    Returns:
        Base addresses of the chunks with at least one child region
    """
    regions = _parse_iomem_regions(iomem_path)
    return {chunk_base for chunk_base, _, _ in _chunk_children(regions, _pool_chunks(regions))}


def get_memory_pool_from_iomem(iomem_path: str = IOMEM_PATH) -> Optional[Tuple[int, int]]:
    """
    Get the first multikernel memory pool chunk from /proc/iomem.

    Returns:
        (base_address, size_bytes) or None if the pool is not registered
    """
    chunks = get_pool_chunks_from_iomem(iomem_path)
    return chunks[0] if chunks else None


def get_pool_allocated_bytes(iomem_path: str = IOMEM_PATH) -> Optional[Tuple[int, int, int]]:
    """
    Compute pool usage from /proc/iomem, the single source of truth.

    Every region nested inside a pool chunk (instance memory, daxfs
    heaps, ...) counts as allocated. Overlapping and nested child regions
    are merged so nothing is double counted.

    Returns:
        (first_chunk_base, pool_bytes, allocated_bytes), or None if the
        pool is not registered in /proc/iomem
    """
    regions = _parse_iomem_regions(iomem_path)
    chunks = _pool_chunks(regions)
    if not chunks:
        return None

    allocated = 0
    current_base = current_end = None
    for _, base, end in sorted(_chunk_children(regions, chunks), key=lambda c: c[1:]):
        if current_base is None:
            current_base, current_end = base, end
        elif base <= current_end + 1:
            current_end = max(current_end, end)
        else:
            allocated += current_end - current_base + 1
            current_base, current_end = base, end
    if current_base is not None:
        allocated += current_end - current_base + 1

    return (chunks[0][0], sum(size for _, size in chunks), allocated)


def get_available_cpus(tree: GlobalDeviceTree) -> Set[int]:
    """
    Get set of CPUs available for allocation (not allocated to any instance).

    The kernel's own free list wins when the tree carries one: the root
    read-back has no instances section, so deriving free CPUs from pool
    membership alone would hand out CPUs that are already lent out.

    Args:
        tree: GlobalDeviceTree to analyze

    Returns:
        Set of available CPU IDs
    """
    allocated = get_allocated_cpus(tree)

    free = tree.hardware.cpus.available_free
    if free is not None:
        return set(free) - allocated

    return set(tree.hardware.cpus.available) - allocated


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


def get_allocated_memory_regions_from_iomem(
    iomem_path: str = IOMEM_PATH,
) -> List[tuple[int, int]]:
    """
    Get list of allocated memory regions from /proc/iomem.

    Reads actual memory allocations from the kernel (source of truth).
    Expected format: "40000000-463fffff : mk-instance-1-web-server-region-0"

    Returns:
        List of (base_address, size_bytes) tuples
    """
    return [
        (base, end - base + 1)
        for base, end, name in _parse_iomem_regions(iomem_path)
        if "mk-instance-" in name
    ]


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


def chunk_containing(
    tree: GlobalDeviceTree, base: int, size: int
) -> Optional[PoolMemoryRegion]:
    """
    Find the pool chunk that holds a whole memory region.

    Args:
        tree: GlobalDeviceTree carrying the live pool chunks
        base: Base address of the region
        size: Size of the region in bytes

    Returns:
        The chunk containing the region, or None if no single chunk holds it
    """
    for chunk in tree.hardware.memory.regions:
        if chunk.base <= base and base + size <= chunk.base + chunk.size:
            return chunk
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
        ResourceError: If the region is misaligned, leaves the pool chunk that
                       holds it, or conflicts with another instance
    """
    if memory_base % 0x1000 != 0:
        raise ResourceError(f"Memory base {hex(memory_base)} is not 4KB-aligned")

    memory_end = memory_base + memory_bytes

    # The pool is a list of chunks, so a region has to sit inside one of them;
    # spanning two chunks means spanning the host memory between them.
    chunks = tree.hardware.memory.regions
    if chunks and chunk_containing(tree, memory_base, memory_bytes) is None:
        listed = ", ".join(f"{hex(c.base)}-{hex(c.base + c.size - 1)}" for c in chunks)
        raise ResourceError(
            f"Memory region {hex(memory_base)}-{hex(memory_end)} does not fit in "
            f"any pool chunk ({listed})"
        )

    for instance in tree.instances.values():
        if instance.name == exclude_instance:
            continue

        inst_base = instance.resources.memory_base
        if not inst_base:
            continue
        inst_end = inst_base + instance.resources.memory_bytes

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
