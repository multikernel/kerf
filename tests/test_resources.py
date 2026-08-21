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
    get_allocated_memory_regions_from_iomem,
    get_memory_pool_from_iomem,
    get_pool_allocated_bytes,
    get_pool_chunks_from_iomem,
    get_busy_chunks_from_iomem,
    chunk_containing,
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

    def test_get_available_cpus_trusts_the_kernel_free_list(self, sample_tree):
        """A read-back lists no instances, so its free list is the only truth."""
        sample_tree.hardware.cpus.available_free = [20, 21, 22]
        sample_tree.instances = {}

        assert get_available_cpus(sample_tree) == {20, 21, 22}

    def test_get_available_cpus_free_list_minus_tree_instances(self, sample_tree):
        """When the tree does list instances, their CPUs are not free."""
        sample_tree.hardware.cpus.available_free = [4, 5, 20]

        assert get_available_cpus(sample_tree) == {20}

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

    def test_chunk_containing_finds_the_holding_chunk(self, sample_tree):
        """A region inside a chunk resolves to that chunk."""
        chunk = chunk_containing(sample_tree, 0x100000000, 1024**3)

        assert chunk is sample_tree.hardware.memory.regions[0]

    def test_chunk_containing_rejects_a_region_spanning_two_chunks(self, sample_hardware):
        """Host memory between two chunks is not part of the pool."""
        from kerf.models import GlobalDeviceTree, PoolMemoryRegion

        sample_hardware.memory.regions = [
            PoolMemoryRegion(base=0x100000000, size=1024**3, node=0),
            PoolMemoryRegion(base=0x200000000, size=1024**3, node=1),
        ]
        tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

        assert chunk_containing(tree, 0x100000000, 1024**3) is not None
        assert chunk_containing(tree, 0x200000000, 1024**3) is not None
        assert chunk_containing(tree, 0x1C0000000, 1024**3) is None

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
        """Test memory allocation outside every pool chunk."""
        # Use base before the only chunk
        memory_base = 0x10000000
        memory_bytes = 1024**3

        with pytest.raises(ResourceError, match="does not fit in any pool chunk"):
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


class TestIomemPoolAccounting:
    """Test /proc/iomem based pool accounting."""

    SAMPLE_IOMEM = "\n".join(
        [
            "00001000-0009ffff : System RAM",
            "40000000-7fffffff : Multikernel Memory Pool",
            "  40000000-43ffffff : mk-instance-1-web-server-region-0",
            "  44000000-4bffffff : mk-instance-2-database-region-0",
            "  4c000000-4fffffff : daxfs",
            "80000000-8fffffff : PCI Bus 0000:00",
        ]
    )

    @pytest.fixture
    def iomem_file(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text(self.SAMPLE_IOMEM + "\n", encoding="utf-8")
        return str(path)

    def test_get_memory_pool_from_iomem(self, iomem_file):
        pool = get_memory_pool_from_iomem(iomem_file)
        assert pool == (0x40000000, 0x40000000)

    def test_get_memory_pool_from_iomem_missing(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text("00001000-0009ffff : System RAM\n", encoding="utf-8")
        assert get_memory_pool_from_iomem(str(path)) is None
        assert get_memory_pool_from_iomem(str(tmp_path / "absent")) is None

    def test_get_allocated_memory_regions_from_iomem(self, iomem_file):
        regions = get_allocated_memory_regions_from_iomem(iomem_file)
        assert regions == [
            (0x40000000, 0x4000000),
            (0x44000000, 0x8000000),
        ]

    def test_get_pool_allocated_bytes(self, iomem_file):
        usage = get_pool_allocated_bytes(iomem_file)
        # Instance regions plus the daxfs region nested in the pool
        assert usage == (0x40000000, 0x40000000, 0x10000000)

    def test_get_pool_allocated_bytes_empty_pool(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text(
            "40000000-7fffffff : Multikernel Memory Pool\n", encoding="utf-8"
        )
        assert get_pool_allocated_bytes(str(path)) == (0x40000000, 0x40000000, 0)

    def test_get_pool_allocated_bytes_no_pool(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text("00001000-0009ffff : System RAM\n", encoding="utf-8")
        assert get_pool_allocated_bytes(str(path)) is None

    def test_get_pool_allocated_bytes_merges_nested_regions(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text(
            "\n".join(
                [
                    "40000000-7fffffff : Multikernel Memory Pool",
                    "  40000000-43ffffff : mk-instance-1-a-region-0",
                    "    40000000-40ffffff : mk-instance-1-a-subregion",
                    "  44000000-47ffffff : mk-instance-2-b-region-0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        usage = get_pool_allocated_bytes(str(path))
        assert usage == (0x40000000, 0x40000000, 0x8000000)

    TWO_CHUNKS = "\n".join(
        [
            "100000000-13fffffff : Multikernel Memory Pool",
            "  100000000-10fffffff : mk-instance-1-web-region-0",
            "200000000-21fffffff : Multikernel Memory Pool",
        ]
    )

    @pytest.fixture
    def two_chunk_iomem(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text(self.TWO_CHUNKS + "\n", encoding="utf-8")
        return str(path)

    def test_pool_chunks_and_busy(self, two_chunk_iomem):
        assert get_pool_chunks_from_iomem(two_chunk_iomem) == [
            (0x100000000, 1 << 30),
            (0x200000000, 1 << 29),
        ]
        assert get_busy_chunks_from_iomem(two_chunk_iomem) == {0x100000000}

    def test_first_chunk_and_allocation_across_chunks(self, two_chunk_iomem):
        assert get_memory_pool_from_iomem(two_chunk_iomem) == (0x100000000, 1 << 30)
        assert get_pool_allocated_bytes(two_chunk_iomem) == (
            0x100000000,
            (1 << 30) + (1 << 29),
            1 << 28,
        )

    def test_no_chunks(self, tmp_path):
        path = tmp_path / "iomem"
        path.write_text("00001000-0009ffff : System RAM\n", encoding="utf-8")
        assert get_pool_chunks_from_iomem(str(path)) == []
        assert get_busy_chunks_from_iomem(str(path)) == set()
