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
Tests for kerf validator.
"""

import pytest
from kerf.dtc.validator import MultikernelValidator
from kerf.exceptions import ValidationError


class TestMultikernelValidator:
    """Test multikernel validator."""

    def test_valid_configuration(self, sample_tree):
        """Test validation of valid configuration."""
        validator = MultikernelValidator()
        result = validator.validate(sample_tree)

        assert result.is_valid
        assert len(result.errors) == 0

    def test_cpu_conflict_detection(self, sample_hardware):
        """Test CPU conflict detection."""
        from kerf.models import Instance, InstanceResources, GlobalDeviceTree

        # Create instances with CPU overlap
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],
                    memory_base=0x80000000,
                    memory_bytes=2 * 1024**3,
                    devices=[]
                ),
            ),
            'app2': Instance(
                name='app2',
                id=2,
                resources=InstanceResources(
                    cpus=[6, 7, 8, 9],  # Overlaps with app1
                    memory_base=0x100000000,
                    memory_bytes=2 * 1024**3,
                    devices=[]
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("CPU allocation conflict" in error for error in result.errors)

    def test_memory_conflict_detection(self, sample_hardware):
        """Test memory conflict detection."""
        from kerf.models import Instance, InstanceResources, GlobalDeviceTree

        # Create instances with memory overlap
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],
                    memory_base=0x80000000,
                    memory_bytes=2 * 1024**3,
                    devices=[]
                ),
            ),
            'app2': Instance(
                name='app2',
                id=2,
                resources=InstanceResources(
                    cpus=[8, 9, 10, 11],
                    memory_base=0x80000000,  # Same base as app1
                    memory_bytes=2 * 1024**3,
                    devices=[]
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("Memory region overlap" in error for error in result.errors)

    def test_memory_overflow_detection(self, sample_hardware):
        """Test memory overflow detection."""
        from kerf.models import Instance, InstanceResources, GlobalDeviceTree

        # Create instance that exceeds memory pool
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],
                    memory_base=0x80000000,
                    memory_bytes=20 * 1024**3,  # Exceeds memory pool
                    devices=[]
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("exceeds memory pool" in error for error in result.errors)

    def test_duplicate_instance_names(self, sample_hardware):
        """Test duplicate instance name detection."""
        from kerf.models import Instance, InstanceResources, GlobalDeviceTree

        # Create tree with duplicate instance names (using list to simulate duplicate names)
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],
                    memory_base=0x80000000,
                    memory_bytes=2 * 1024**3,
                    devices=[]
                ),
            ),
            'app2': Instance(  # Same name as app1
                name='app1',  # Duplicate name
                id=2,
                resources=InstanceResources(
                    cpus=[8, 9, 10, 11],
                    memory_base=0x100000000,
                    memory_bytes=2 * 1024**3,
                    devices=[]
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("Duplicate instance name" in error for error in result.errors)


class TestNUMAValidation:
    """Test NUMA topology validation."""

    def test_validate_numa_constraints(self, sample_hardware):
        """Test NUMA node constraint validation."""
        from kerf.models import (
            Instance, InstanceResources, GlobalDeviceTree,
            TopologySection, NUMANode
        )

        # Add topology to hardware
        numa_nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3, 4, 5, 6, 7],
                distance_matrix={0: 10, 1: 20},
                memory_type='dram'
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x100000000,
                memory_size=8 * 1024**3,
                cpus=[8, 9, 10, 11, 12, 13, 14, 15],
                distance_matrix={0: 20, 1: 10},
                memory_type='dram'
            )
        }

        sample_hardware.topology = TopologySection(numa_nodes=numa_nodes)

        # Create instance with invalid NUMA node
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],
                    memory_base=0x80000000,
                    memory_bytes=2 * 1024**3,
                    devices=[],
                    numa_nodes=[999]  # Invalid NUMA node
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        assert not result.is_valid
        assert any("NUMA node" in error and "does not exist" in error for error in result.errors)

    def test_validate_cpu_numa_mismatch_warning(self, sample_hardware):
        """Test warning for CPU/NUMA node mismatch."""
        from kerf.models import (
            Instance, InstanceResources, GlobalDeviceTree,
            TopologySection, NUMANode
        )

        # Add topology to hardware
        numa_nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3, 4, 5, 6, 7],
                distance_matrix={},
                memory_type='dram'
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x100000000,
                memory_size=8 * 1024**3,
                cpus=[8, 9, 10, 11, 12, 13, 14, 15],
                distance_matrix={},
                memory_type='dram'
            )
        }

        sample_hardware.topology = TopologySection(numa_nodes=numa_nodes)

        # Create instance with CPU from NUMA 0 but requesting NUMA 1
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],  # NUMA 0 CPUs
                    memory_base=0x100000000,  # NUMA 1 memory
                    memory_bytes=2 * 1024**3,
                    devices=[],
                    numa_nodes=[1]  # Requesting NUMA 1
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        # Should have warnings about NUMA mismatch
        assert any("NUMA" in warning for warning in result.warnings)

    def test_validate_compact_affinity(self, sample_hardware):
        """Test compact CPU affinity validation."""
        from kerf.models import (
            Instance, InstanceResources, GlobalDeviceTree,
            TopologySection, NUMANode
        )

        # Add topology to hardware
        numa_nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3, 4, 5, 6, 7],
                distance_matrix={},
                memory_type='dram'
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x100000000,
                memory_size=8 * 1024**3,
                cpus=[8, 9, 10, 11, 12, 13, 14, 15],
                distance_matrix={},
                memory_type='dram'
            )
        }

        sample_hardware.topology = TopologySection(numa_nodes=numa_nodes)

        # Create instance with compact affinity but CPUs span multiple NUMA nodes
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 8, 9],  # CPUs from both NUMA 0 and 1
                    memory_base=0x80000000,
                    memory_bytes=2 * 1024**3,
                    devices=[],
                    cpu_affinity='compact'
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        # Should have warnings about compact affinity
        assert any("Compact" in warning and "multiple NUMA nodes" in warning 
                  for warning in result.warnings)

    def test_validate_spread_affinity(self, sample_hardware):
        """Test spread CPU affinity validation."""
        from kerf.models import (
            Instance, InstanceResources, GlobalDeviceTree,
            TopologySection, NUMANode
        )

        # Add topology to hardware
        numa_nodes = {
            0: NUMANode(
                node_id=0,
                memory_base=0x80000000,
                memory_size=8 * 1024**3,
                cpus=[0, 1, 2, 3, 4, 5, 6, 7],
                distance_matrix={},
                memory_type='dram'
            ),
            1: NUMANode(
                node_id=1,
                memory_base=0x100000000,
                memory_size=8 * 1024**3,
                cpus=[8, 9, 10, 11, 12, 13, 14, 15],
                distance_matrix={},
                memory_type='dram'
            )
        }

        sample_hardware.topology = TopologySection(numa_nodes=numa_nodes)

        # Create instance with spread affinity but CPUs from single NUMA node
        instances = {
            'app1': Instance(
                name='app1',
                id=1,
                resources=InstanceResources(
                    cpus=[4, 5, 6, 7],  # All from NUMA 0
                    memory_base=0x80000000,
                    memory_bytes=2 * 1024**3,
                    devices=[],
                    cpu_affinity='spread'
                ),
            )
        }

        tree = GlobalDeviceTree(
            hardware=sample_hardware,
            instances=instances,
            device_references={}
        )

        validator = MultikernelValidator()
        result = validator.validate(tree)

        # Should have warnings about spread affinity
        assert any("Spread" in warning and "single NUMA node" in warning 
                  for warning in result.warnings)

