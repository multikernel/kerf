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

from typing import List, Set, Optional, Dict
from .models import GlobalDeviceTree, Instance, InstanceResources
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
        iomem_path = Path('/proc/iomem')
        if not iomem_path.exists():
            return regions
        
        with open(iomem_path, 'r') as f:
            for line in f:
                if 'mk-instance-' in line:
                    match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
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
            regions.append((
                instance.resources.memory_base,
                instance.resources.memory_bytes
            ))
    return regions


def find_available_memory_base(
    tree: GlobalDeviceTree,
    size_bytes: int,
    alignment: int = 0x1000,
    use_iomem: bool = True
) -> Optional[int]:
    """
    Find available memory region for allocation.
    
    Args:
        tree: GlobalDeviceTree to analyze (for pool boundaries)
        size_bytes: Size of memory region needed
        alignment: Required alignment (default 4KB)
        use_iomem: If True, read actual allocations from /proc/iomem (kernel source of truth).
                   If False, use allocations from tree (for validation/dry-run).
        
    Returns:
        Base address for allocation, or None if no space available
    """
    pool_base = tree.hardware.memory.memory_pool_base
    pool_end = tree.hardware.memory.memory_pool_end
    
    if use_iomem:
        allocated_regions = get_allocated_memory_regions_from_iomem()
    else:
        allocated_regions = get_allocated_memory_regions(tree)
    # Sort by base address
    allocated_regions.sort()
    
    # Try to find gap between allocations or at start/end
    if not allocated_regions:
        # No allocations yet, use start of pool (aligned)
        aligned_base = (pool_base + alignment - 1) // alignment * alignment
        if aligned_base + size_bytes <= pool_end:
            return aligned_base
        return None
    
    # Check gap at start
    first_base = allocated_regions[0][0]
    aligned_base = (pool_base + alignment - 1) // alignment * alignment
    if aligned_base + size_bytes <= first_base:
        return aligned_base
    
    # Check gaps between allocations
    for i in range(len(allocated_regions) - 1):
        current_end = allocated_regions[i][0] + allocated_regions[i][1]
        next_base = allocated_regions[i + 1][0]
        
        # Align current_end
        aligned_base = (current_end + alignment - 1) // alignment * alignment
        
        if aligned_base + size_bytes <= next_base:
            return aligned_base
    
    # Check gap at end
    last_end = allocated_regions[-1][0] + allocated_regions[-1][1]
    aligned_base = (last_end + alignment - 1) // alignment * alignment
    if aligned_base + size_bytes <= pool_end:
        return aligned_base
    
    return None


def validate_cpu_allocation(
    tree: GlobalDeviceTree,
    requested_cpus: List[int],
    exclude_instance: Optional[str] = None
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
    
    # Check all requested CPUs exist in hardware
    hardware_cpus = set(tree.hardware.cpus.available)
    invalid_cpus = requested_set - hardware_cpus
    if invalid_cpus:
        raise ResourceError(
            f"Invalid CPUs requested: {sorted(invalid_cpus)}. "
            f"Available CPUs: {sorted(hardware_cpus)}"
        )
    
    # Check CPUs are available
    unavailable = requested_set - available_cpus
    if unavailable:
        # Find which instances are using these CPUs
        conflicts = []
        for instance in tree.instances.values():
            if instance.name == exclude_instance:
                continue
            conflict_cpus = set(instance.resources.cpus) & unavailable
            if conflict_cpus:
                conflicts.append(f"{instance.name} uses CPUs {sorted(conflict_cpus)}")
        
        conflict_msg = ", ".join(conflicts) if conflicts else "allocated to other instances"
        raise ResourceError(
            f"CPUs {sorted(unavailable)} are not available ({conflict_msg})"
        )


def validate_memory_allocation(
    tree: GlobalDeviceTree,
    memory_base: int,
    memory_bytes: int,
    exclude_instance: Optional[str] = None
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
    pool_base = tree.hardware.memory.memory_pool_base
    pool_end = tree.hardware.memory.memory_pool_end
    memory_end = memory_base + memory_bytes
    
    # Check memory is within pool
    if memory_base < pool_base:
        raise ResourceError(
            f"Memory base {hex(memory_base)} is below pool base {hex(pool_base)}"
        )
    
    if memory_end > pool_end:
        raise ResourceError(
            f"Memory region extends beyond pool: "
            f"{hex(memory_base)}-{hex(memory_end)} vs pool end {hex(pool_end)}"
        )
    
    # Check alignment (4KB)
    if memory_base % 0x1000 != 0:
        raise ResourceError(
            f"Memory base {hex(memory_base)} is not 4KB-aligned"
        )
    
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
    existing_ids = {inst.id for inst in tree.instances.values()}
    
    # Find first available ID in range 1-511
    for instance_id in range(1, 512):
        if instance_id not in existing_ids:
            return instance_id
    
    raise ResourceError("No available instance IDs (all 1-511 are in use)")

