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
Tests for kerf resource allocation utilities.
"""

import pytest
from kerf.resources import (
    get_available_cpus,
    get_allocated_cpus,
    get_allocated_memory_regions,
    find_available_memory_base,
    validate_cpu_allocation,
    validate_memory_allocation,
    find_next_instance_id,
)
from kerf.exceptions import ResourceError


class TestCPUAllocation:
    """Test CPU allocation utilities."""

    def test_get_available_cpus(self, sample_tree):
        """Test getting available CPUs."""
        available = get_available_cpus(sample_tree)

        # CPUs 4-31 are available, but 4-15 are used by instances
        # web-server uses 4-7, database uses 8-15
        # So 16-31 should be available
        assert available == set(range(16, 32))

    def test_get_allocated_cpus(self, sample_tree):
        """Test getting allocated CPUs."""
        allocated = get_allocated_cpus(sample_tree)

        # web-server uses 4-7, database uses 8-15
        expected = set(range(4, 16))
        assert allocated == expected

    def test_validate_cpu_allocation_success(self, sample_tree):
        """Test successful CPU allocation validation."""
        # Request CPUs that are available (16-19)
        requested_cpus = [16, 17, 18, 19]

        # Should not raise
        validate_cpu_allocation(sample_tree, requested_cpus)

    def test_validate_cpu_allocation_conflict(self, sample_tree):
        """Test CPU allocation conflict detection."""
        # Request CPUs that are already used (4-7 are used by web-server)
        requested_cpus = [4, 5, 6, 7]

        with pytest.raises(ResourceError, match="not available"):
            validate_cpu_allocation(sample_tree, requested_cpus)

    def test_validate_cpu_allocation_invalid_cpu(self, sample_tree):
        """Test validation with invalid CPU IDs."""
        # Request CPU that doesn't exist
        requested_cpus = [999]

        with pytest.raises(ResourceError, match="Invalid APIC IDs requested"):
            validate_cpu_allocation(sample_tree, requested_cpus)

    def test_validate_cpu_allocation_with_exclusion(self, sample_tree):
        """Test CPU allocation validation with excluded instance."""
        # Request CPUs that web-server uses, but exclude web-server
        requested_cpus = [4, 5, 6, 7]

        # Should not raise because web-server is excluded
        validate_cpu_allocation(sample_tree, requested_cpus, exclude_instance="web-server")


class TestMemoryAllocation:
    """Test memory allocation utilities."""

    def test_get_allocated_memory_regions(self, sample_tree):
        """Test getting allocated memory regions."""
        regions = get_allocated_memory_regions(sample_tree)

        # Should have 2 regions for the 2 instances
        assert len(regions) == 2

        # Check regions are correct
        bases = [r[0] for r in regions]
        assert 0x80000000 in bases  # web-server
        assert 0x100000000 in bases  # database

    def test_find_available_memory_base_empty_pool(self, sample_hardware):
        """Test finding memory base in empty pool."""
        from kerf.models import GlobalDeviceTree

        # Create tree with no instances
        tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

        # Request 1GB
        size = 1024**3
        base = find_available_memory_base(tree, size, use_iomem=False)

        # Should get start of pool (aligned)
        assert base == sample_hardware.memory.memory_pool_base

    def test_find_available_memory_base_with_allocations(self, sample_tree):
        """Test finding memory base with existing allocations."""
        # Request 1GB after existing allocations
        size = 1024**3
        base = find_available_memory_base(sample_tree, size, use_iomem=False)

        # Should find a gap or append at end
        assert base is not None
        assert base >= sample_tree.hardware.memory.memory_pool_base

    def test_find_available_memory_base_no_space(self, sample_tree):
        """Test finding memory base when no space available."""
        # Request more memory than available in pool
        size = 100 * 1024**3  # 100GB - way more than pool size
        base = find_available_memory_base(sample_tree, size, use_iomem=False)

        # Should return None
        assert base is None

    def test_validate_memory_allocation_success(self, sample_tree):
        """Test successful memory allocation validation."""
        # Use a region that's not allocated (after database region)
        # database uses 0x100000000 + 8GB = 0x300000000
        memory_base = 0x300000000
        memory_bytes = 1024**3  # 1GB

        # Should not raise
        validate_memory_allocation(sample_tree, memory_base, memory_bytes)

    def test_validate_memory_allocation_overlap(self, sample_tree):
        """Test memory allocation overlap detection."""
        # Use same base as web-server
        memory_base = 0x80000000
        memory_bytes = 1024**3

        with pytest.raises(ResourceError, match="overlaps with instance"):
            validate_memory_allocation(sample_tree, memory_base, memory_bytes)

    def test_validate_memory_allocation_out_of_pool(self, sample_tree):
        """Test memory allocation outside pool."""
        # Use base before pool
        memory_base = 0x10000000  # Below pool base
        memory_bytes = 1024**3

        with pytest.raises(ResourceError, match="below pool base"):
            validate_memory_allocation(sample_tree, memory_base, memory_bytes)

    def test_validate_memory_allocation_misaligned(self, sample_tree):
        """Test memory allocation with misaligned base."""
        # Use misaligned base
        memory_base = 0x200000001  # Not 4KB aligned
        memory_bytes = 1024**3

        with pytest.raises(ResourceError, match="not 4KB-aligned"):
            validate_memory_allocation(sample_tree, memory_base, memory_bytes)

    def test_validate_memory_allocation_with_exclusion(self, sample_tree):
        """Test memory allocation validation with excluded instance."""
        # Use same base as web-server, but exclude web-server
        memory_base = 0x80000000
        memory_bytes = 1024**3

        # Should not raise because web-server is excluded
        validate_memory_allocation(
            sample_tree, memory_base, memory_bytes, exclude_instance="web-server"
        )


class TestInstanceID:
    """Test instance ID allocation."""

    def test_find_next_instance_id_empty(self, sample_hardware):
        """Test finding next ID with no instances."""
        from kerf.models import GlobalDeviceTree

        tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

        next_id = find_next_instance_id(tree)
        assert next_id == 1

    def test_find_next_instance_id_with_instances(self, sample_tree):
        """Test finding next ID with existing instances."""
        # Instances have IDs 1 and 2
        next_id = find_next_instance_id(sample_tree)
        assert next_id == 3

    def test_find_next_instance_id_gaps(self, sample_tree):
        """Test finding next ID with gaps in sequence."""
        # Remove an instance to create a gap
        del sample_tree.instances["web-server"]

        # Should find ID 1 (now available)
        next_id = find_next_instance_id(sample_tree)
        assert next_id == 1

    def test_find_next_instance_id_full(self, sample_hardware):
        """Test when all IDs are exhausted."""
        from kerf.models import GlobalDeviceTree, Instance, InstanceResources

        # Create instances with all IDs from 1-511
        instances = {}
        for i in range(1, 512):
            instances[f"inst{i}"] = Instance(
                name=f"inst{i}",
                id=i,
                resources=InstanceResources(
                    cpus=[4],
                    memory_base=0x80000000 + i * 0x1000000,
                    memory_bytes=0x1000000,
                    devices=[],
                ),
            )

        tree = GlobalDeviceTree(hardware=sample_hardware, instances=instances, device_references={})

        with pytest.raises(ResourceError, match="No available instance IDs"):
            find_next_instance_id(tree)
