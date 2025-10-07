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
