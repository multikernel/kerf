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
Tests for kerf data models.
"""

import pytest
from kerf.models import (
    CPUAllocation, MemoryAllocation, DeviceInfo, Instance, InstanceResources,
    HardwareInventory, GlobalDeviceTree
)


class TestCPUAllocation:
    """Test CPU allocation model."""

    def test_cpu_allocation_creation(self):
        """Test CPU allocation creation."""
        cpus = CPUAllocation(
            total=32,
            host_reserved=[0, 1, 2, 3],
            available=list(range(4, 32))
        )

        assert cpus.total == 32
        assert cpus.host_reserved == [0, 1, 2, 3]
        assert cpus.available == list(range(4, 32))

    def test_get_allocated_cpus(self):
        """Test getting allocated CPUs."""
        cpus = CPUAllocation(
            total=32,
            host_reserved=[0, 1, 2, 3],
            available=list(range(4, 32))
        )

        allocated = cpus.get_allocated_cpus()
        expected = set(range(4, 32)) - set([0, 1, 2, 3])
        assert allocated == expected


class TestMemoryAllocation:
    """Test memory allocation model."""

    def test_memory_allocation_creation(self):
        """Test memory allocation creation."""
        memory = MemoryAllocation(
            total_bytes=16 * 1024**3,
            host_reserved_bytes=2 * 1024**3,
            memory_pool_base=0x80000000,
            memory_pool_bytes=14 * 1024**3
        )

        assert memory.total_bytes == 16 * 1024**3
        assert memory.host_reserved_bytes == 2 * 1024**3
        assert memory.memory_pool_base == 0x80000000
        assert memory.memory_pool_bytes == 14 * 1024**3

    def test_memory_pool_end(self):
        """Test memory pool end calculation."""
        memory = MemoryAllocation(
            total_bytes=16 * 1024**3,
            host_reserved_bytes=2 * 1024**3,
            memory_pool_base=0x80000000,
            memory_pool_bytes=14 * 1024**3
        )

        expected_end = 0x80000000 + 14 * 1024**3
        assert memory.memory_pool_end == expected_end


class TestDeviceInfo:
    """Test device information model."""

    def test_device_info_creation(self):
        """Test device info creation."""
        device = DeviceInfo(
            name='eth0',
            compatible='intel,i40e',
            pci_id='0000:01:00.0',
            sriov_vfs=8,
            host_reserved_vf=0,
            available_vfs=[1, 2, 3, 4, 5, 6, 7]
        )

        assert device.name == 'eth0'
        assert device.compatible == 'intel,i40e'
        assert device.pci_id == '0000:01:00.0'
        assert device.sriov_vfs == 8
        assert device.host_reserved_vf == 0
        assert device.available_vfs == [1, 2, 3, 4, 5, 6, 7]


class TestInstance:
    """Test instance model."""

    def test_instance_creation(self):
        """Test instance creation."""
        resources = InstanceResources(
            cpus=[4, 5, 6, 7],
            memory_base=0x80000000,
            memory_bytes=2 * 1024**3,
            devices=['eth0_vf1']
        )

        instance = Instance(
            name='web-server',
            id=1,
            resources=resources
        )

        assert instance.name == 'web-server'
        assert instance.id == 1
        assert instance.resources == resources

    def test_instance_with_numa_nodes(self):
        """Test instance with NUMA node constraints."""
        resources = InstanceResources(
            cpus=[4, 5, 6, 7],
            memory_base=0x80000000,
            memory_bytes=2 * 1024**3,
            devices=[],
            numa_nodes=[0, 1]
        )

        instance = Instance(
            name='numa-instance',
            id=1,
            resources=resources
        )

        assert instance.resources.numa_nodes == [0, 1]

    def test_instance_with_cpu_affinity(self):
        """Test instance with CPU affinity."""
        resources = InstanceResources(
            cpus=[4, 5, 6, 7],
            memory_base=0x80000000,
            memory_bytes=2 * 1024**3,
            devices=[],
            cpu_affinity='compact'
        )

        instance = Instance(
            name='affinity-instance',
            id=1,
            resources=resources
        )

        assert instance.resources.cpu_affinity == 'compact'

    def test_instance_with_memory_policy(self):
        """Test instance with memory policy."""
        resources = InstanceResources(
            cpus=[4, 5, 6, 7],
            memory_base=0x80000000,
            memory_bytes=2 * 1024**3,
            devices=[],
            memory_policy='local'
        )

        instance = Instance(
            name='policy-instance',
            id=1,
            resources=resources
        )

        assert instance.resources.memory_policy == 'local'


class TestTopology:
    """Test topology models."""

    def test_numa_node_creation(self):
        """Test NUMA node creation."""
        from kerf.models import NUMANode

        node = NUMANode(
            node_id=0,
            memory_base=0x80000000,
            memory_size=8 * 1024**3,
            cpus=[0, 1, 2, 3],
            distance_matrix={0: 10, 1: 20},
            memory_type='dram'
        )

        assert node.node_id == 0
        assert node.memory_base == 0x80000000
        assert node.memory_size == 8 * 1024**3
        assert node.cpus == [0, 1, 2, 3]
        assert node.distance_matrix == {0: 10, 1: 20}
        assert node.memory_type == 'dram'

    def test_topology_section_get_cpus_in_numa_node(self):
        """Test getting CPUs in NUMA node."""
        from kerf.models import TopologySection, NUMANode

        nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3],
                distance_matrix={},
                memory_type='dram'
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x100000000,
                memory_size=8 * 1024**3,
                cpus=[4, 5, 6, 7],
                distance_matrix={},
                memory_type='dram'
            )
        }

        topology = TopologySection(numa_nodes=nodes)

        cpus = topology.get_cpus_in_numa_node(0)
        assert cpus == [0, 1, 2, 3]

        cpus = topology.get_cpus_in_numa_node(1)
        assert cpus == [4, 5, 6, 7]

        cpus = topology.get_cpus_in_numa_node(999)
        assert cpus == []

    def test_topology_section_get_numa_node_for_cpu(self):
        """Test getting NUMA node for CPU."""
        from kerf.models import TopologySection, NUMANode

        nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3],
                distance_matrix={},
                memory_type='dram'
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x100000000,
                memory_size=8 * 1024**3,
                cpus=[4, 5, 6, 7],
                distance_matrix={},
                memory_type='dram'
            )
        }

        topology = TopologySection(numa_nodes=nodes)

        assert topology.get_numa_node_for_cpu(0) == 0
        assert topology.get_numa_node_for_cpu(5) == 1
        assert topology.get_numa_node_for_cpu(999) is None

    def test_topology_section_get_memory_region_for_numa_node(self):
        """Test getting memory region for NUMA node."""
        from kerf.models import TopologySection, NUMANode

        nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3],
                distance_matrix={},
                memory_type='dram'
            )
        }

        topology = TopologySection(numa_nodes=nodes)

        region = topology.get_memory_region_for_numa_node(0)
        assert region == (0x80000000, 8 * 1024**3)

        region = topology.get_memory_region_for_numa_node(999)
        assert region is None


class TestWorkloadType:
    """Test workload type enum."""

    def test_workload_types(self):
        """Test workload type values."""
        from kerf.models import WorkloadType

        assert WorkloadType.WEB_SERVER.value == "web-server"
        assert WorkloadType.DATABASE_OLTP.value == "database-oltp"
        assert WorkloadType.COMPUTE.value == "compute"
        assert WorkloadType.STORAGE.value == "storage"
        assert WorkloadType.NETWORK.value == "network"


class TestInstanceState:
    """Test instance state enum."""

    def test_instance_states(self):
        """Test instance state values."""
        from kerf.models import InstanceState

        assert InstanceState.EMPTY.value == "empty"
        assert InstanceState.READY.value == "ready"
        assert InstanceState.LOADED.value == "loaded"
        assert InstanceState.ACTIVE.value == "active"
        assert InstanceState.FAILED.value == "failed"


