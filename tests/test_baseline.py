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
Tests for kerf baseline manager.
"""

import os
import tempfile

import pytest

from kerf.baseline import BaselineManager
from kerf.exceptions import KernelInterfaceError, ValidationError


class TestBaselineManager:
    """Test baseline manager functionality."""

    def test_validate_baseline_success(self, sample_hardware):
        """Test successful baseline validation."""
        from kerf.models import GlobalDeviceTree

        tree = GlobalDeviceTree(
            hardware=sample_hardware, instances={}, device_references={}  # No instances
        )

        manager = BaselineManager()
        # Should not raise
        manager.validate_baseline(tree)

    def test_validate_baseline_with_instances(self, sample_tree):
        """Test baseline validation fails with instances."""
        manager = BaselineManager()

        with pytest.raises(ValidationError, match="must not contain instances"):
            manager.validate_baseline(sample_tree)

    def test_validate_baseline_missing_hardware(self):
        """Test baseline validation fails without hardware."""
        from kerf.models import GlobalDeviceTree

        tree = GlobalDeviceTree(hardware=None, instances={}, device_references={})

        manager = BaselineManager()

        with pytest.raises(ValidationError, match="must contain hardware"):
            manager.validate_baseline(tree)

    def test_validate_baseline_missing_cpus(self):
        """Test baseline validation fails without CPU info."""
        from kerf.models import GlobalDeviceTree, HardwareInventory, MemoryAllocation

        tree = GlobalDeviceTree(
            hardware=HardwareInventory(
                cpus=None,
                memory=MemoryAllocation(
                    total_bytes=16 * 1024**3,
                    host_reserved_bytes=2 * 1024**3,
                    memory_pool_base=0x80000000,
                    memory_pool_bytes=14 * 1024**3,
                ),
                devices={},
            ),
            instances={},
            device_references={},
        )

        manager = BaselineManager()

        with pytest.raises(ValidationError, match="CPU allocation"):
            manager.validate_baseline(tree)

    def test_write_and_read_baseline(self, sample_hardware):
        """Test writing and reading baseline."""
        from kerf.models import GlobalDeviceTree

        # Create temporary file for baseline
        with tempfile.NamedTemporaryFile(delete=False) as f:
            baseline_path = f.name

        try:
            tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

            manager = BaselineManager(baseline_path=baseline_path)
            manager.write_baseline(tree)

            # Read it back
            read_tree = manager.read_baseline()

            # Verify
            assert read_tree.hardware.cpus.available == sample_hardware.cpus.available
            assert (
                read_tree.hardware.memory.memory_pool_base
                == sample_hardware.memory.memory_pool_base
            )
            assert len(read_tree.instances) == 0
        finally:
            # Cleanup
            if os.path.exists(baseline_path):
                os.unlink(baseline_path)

    def test_read_baseline_not_found(self):
        """Test reading baseline when file doesn't exist."""
        manager = BaselineManager(baseline_path="/nonexistent/path")

        with pytest.raises(KernelInterfaceError, match="not found"):
            manager.read_baseline()

    def test_write_baseline_invalid_tree(self, sample_tree):
        """Test writing baseline with invalid tree (has instances)."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            baseline_path = f.name

        try:
            manager = BaselineManager(baseline_path=baseline_path)

            # Should fail because tree has instances
            with pytest.raises(ValidationError, match="must not contain instances"):
                manager.write_baseline(sample_tree)
        finally:
            if os.path.exists(baseline_path):
                os.unlink(baseline_path)
