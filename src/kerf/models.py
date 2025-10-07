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
class CPUAllocation:
    """CPU allocation information."""
    total: int
    host_reserved: List[int]
    available: List[int]
    
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


@dataclass
class Instance:
    """A kernel instance definition."""
    name: str
    id: int
    resources: InstanceResources
    config: Optional[InstanceConfig] = None

@dataclass
class HardwareInventory:
    """Complete hardware inventory."""
    cpus: CPUAllocation
    memory: MemoryAllocation
    devices: Dict[str, DeviceInfo]


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
