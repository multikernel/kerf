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


