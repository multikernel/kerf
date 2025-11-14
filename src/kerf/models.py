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
Data models for multikernel device tree representation.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Set, Tuple
from enum import Enum


class WorkloadType(Enum):
    """Types of workloads for kernel instances."""
    WEB_SERVER = "web-server"
    DATABASE_OLTP = "database-oltp"
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"


class InstanceState(Enum):
    """
    Instance state enum matching kernel mk_instance_state.
    
    States:
    - EMPTY: Instance directory exists but no DTB
    - READY: DTB loaded, resources reserved
    - LOADED: Kernel loaded, ready to start
    - ACTIVE: Kernel running
    - FAILED: Error occurred
    """
    EMPTY = "empty"
    READY = "ready"
    LOADED = "loaded"
    ACTIVE = "active"
    FAILED = "failed"


@dataclass
class InstanceConfig:
    """Configuration for a kernel instance."""
    workload_type: WorkloadType
    priority: Optional[int] = None
    timeout: Optional[int] = None
    enable_pgo: Optional[bool] = None
    pgo_profile: Optional[str] = None
    enable_numa: Optional[bool] = None

@dataclass
class CPUTopology:
    """CPU topology information."""
    cpu_id: int
    numa_node: int
    core_id: int
    thread_id: int
    socket_id: int
    cache_levels: List[int]  # Cache sizes at each level
    flags: List[str]  # CPU flags like "smt", "ht", etc.

@dataclass
class NUMANode:
    """NUMA node information."""
    node_id: int
    memory_base: int
    memory_size: int
    cpus: List[int]
    distance_matrix: Dict[int, int]  # Distance to other NUMA nodes
    memory_type: str  # "dram", "hbm", "cxl", etc.

@dataclass
class CPUAllocation:
    """CPU allocation information."""
    total: int
    host_reserved: List[int]
    available: List[int]
    topology: Optional[Dict[int, CPUTopology]] = None  # CPU ID -> topology info
    
    def get_allocated_cpus(self) -> Set[int]:
        """Get set of CPUs allocated to instances."""
        return set(self.available) - set(self.host_reserved)


@dataclass
class MemoryAllocation:
    """Memory allocation information."""
    total_bytes: int
    host_reserved_bytes: int
    memory_pool_base: int
    memory_pool_bytes: int
    
    @property
    def memory_pool_end(self) -> int:
        """End address of memory pool."""
        return self.memory_pool_base + self.memory_pool_bytes


@dataclass
class DeviceInfo:
    """Device information from hardware inventory."""
    name: str
    compatible: str
    pci_id: Optional[str] = None
    sriov_vfs: Optional[int] = None
    host_reserved_vf: Optional[int] = None
    available_vfs: Optional[List[int]] = None
    namespaces: Optional[int] = None
    host_reserved_ns: Optional[int] = None
    available_ns: Optional[List[int]] = None


@dataclass
class InstanceResources:
    """Resource allocation for a kernel instance."""
    cpus: List[int]
    memory_base: int
    memory_bytes: int
    devices: List[str]  # List of device references
    numa_nodes: Optional[List[int]] = None  # Preferred NUMA nodes
    cpu_affinity: Optional[str] = None  # "compact", "spread", "local"
    memory_policy: Optional[str] = None  # "local", "interleave", "bind"


@dataclass
class Instance:
    """A kernel instance definition."""
    name: str
    id: int
    resources: InstanceResources
    config: Optional[InstanceConfig] = None

@dataclass
class TopologySection:
    """Generic topology section containing all topology types."""
    numa_nodes: Optional[Dict[int, NUMANode]] = None  # NUMA node ID -> node info
    
    def get_cpus_in_numa_node(self, numa_node: int) -> List[int]:
        """Get all CPUs in a specific NUMA node."""
        if not self.numa_nodes or numa_node not in self.numa_nodes:
            return []
        return self.numa_nodes[numa_node].cpus
    
    def get_numa_node_for_cpu(self, cpu_id: int) -> Optional[int]:
        """Get NUMA node ID for a specific CPU."""
        if not self.numa_nodes:
            return None
        for node_id, node in self.numa_nodes.items():
            if cpu_id in node.cpus:
                return node_id
        return None
    
    def get_memory_region_for_numa_node(self, numa_node: int) -> Optional[Tuple[int, int]]:
        """Get memory region (base, size) for a specific NUMA node."""
        if not self.numa_nodes or numa_node not in self.numa_nodes:
            return None
        node = self.numa_nodes[numa_node]
        return (node.memory_base, node.memory_size)


@dataclass
class HardwareInventory:
    """Complete hardware inventory."""
    cpus: CPUAllocation
    memory: MemoryAllocation
    topology: Optional[TopologySection] = None
    devices: Dict[str, DeviceInfo] = None


@dataclass
class OverlayInstanceData:
    """Instance data parsed from an overlay (both creates and removals)."""
    instances: Dict[str, Instance]
    removals: Set[str]


@dataclass
class GlobalDeviceTree:
    """Global device tree representation."""
    hardware: HardwareInventory
    instances: Dict[str, Instance]
    device_references: Dict[str, Dict]  # phandle references


@dataclass
class ValidationResult:
    """Result of validation process."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    suggestions: List[str]


@dataclass
class ResourceUsage:
    """Resource usage summary."""
    cpus_allocated: int
    cpus_total: int
    memory_allocated: int
    memory_total: int
    devices_allocated: int
    devices_total: int
