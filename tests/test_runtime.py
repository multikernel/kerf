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
Tests for kerf runtime manager.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kerf.exceptions import KernelInterfaceError, ValidationError
from kerf.runtime import DeviceTreeManager


class TestDeviceTreeManager:
    """Test device tree manager functionality."""

    def test_initialization(self):
        """Test manager initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / "device_tree"
            overlays_dir = Path(tmpdir) / "overlays"
            overlays_dir.mkdir()

            manager = DeviceTreeManager(
                baseline_path=str(baseline_path), overlays_dir=str(overlays_dir)
            )

            assert manager.baseline_path == baseline_path
            assert manager.overlays_dir == overlays_dir
            assert manager.overlays_new == overlays_dir / "new"

    def test_read_baseline(self, sample_hardware):
        """Test reading baseline."""
        from kerf.models import GlobalDeviceTree
        from kerf.baseline import BaselineManager

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / "device_tree"

            # Create baseline first
            tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

            baseline_mgr = BaselineManager(baseline_path=str(baseline_path))
            baseline_mgr.write_baseline(tree)

            # Read it with DeviceTreeManager
            manager = DeviceTreeManager(baseline_path=str(baseline_path))
            read_tree = manager.read_baseline()

            assert read_tree.hardware.cpus.available == sample_hardware.cpus.available

    def test_get_instance_names_empty(self, sample_hardware):
        """Test getting instance names with empty tree."""
        from kerf.models import GlobalDeviceTree
        from kerf.baseline import BaselineManager

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / "device_tree"

            # Create empty baseline
            tree = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})

            baseline_mgr = BaselineManager(baseline_path=str(baseline_path))
            baseline_mgr.write_baseline(tree)

            manager = DeviceTreeManager(baseline_path=str(baseline_path))
            names = manager.get_instance_names()

            assert not names

    @patch("pathlib.Path.is_dir")
    @patch("pathlib.Path.exists")
    def test_has_instance(self, mock_exists, mock_is_dir):
        """Test checking if instance exists."""
        manager = DeviceTreeManager()

        # Mock instance directory exists and is a directory
        mock_exists.return_value = True
        mock_is_dir.return_value = True
        result = manager.has_instance("test-instance")
        assert result is True

        # Mock instance directory doesn't exist
        mock_exists.return_value = False
        result = manager.has_instance("nonexistent")
        assert result is False

    def test_list_transactions_empty(self):
        """Test listing transactions with empty overlays dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            overlays_dir = Path(tmpdir) / "overlays"
            overlays_dir.mkdir()

            manager = DeviceTreeManager(overlays_dir=str(overlays_dir))
            transactions = manager.list_transactions()

            assert not transactions

    def test_list_transactions_with_txs(self):
        """Test listing transactions with transaction directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            overlays_dir = Path(tmpdir) / "overlays"
            overlays_dir.mkdir()

            # Create mock transaction directories
            tx1_dir = overlays_dir / "tx_1"
            tx2_dir = overlays_dir / "tx_2"
            tx1_dir.mkdir()
            tx2_dir.mkdir()

            # Create status files
            (tx1_dir / "status").write_text("applied")
            (tx2_dir / "status").write_text("applied")

            # Create instance files
            (tx1_dir / "instance").write_text("test-instance-1")
            (tx2_dir / "instance").write_text("test-instance-2")

            manager = DeviceTreeManager(overlays_dir=str(overlays_dir))
            transactions = manager.list_transactions()

            assert len(transactions) == 2
            assert transactions[0]["id"] == "1"
            assert transactions[0]["status"] == "applied"
            assert transactions[0]["instance"] == "test-instance-1"
            assert transactions[1]["id"] == "2"

    def test_validate_overlay_cannot_modify_resources(self, sample_tree, sample_hardware):
        """Test that overlays cannot modify hardware resources."""
        from kerf.models import GlobalDeviceTree, CPUAllocation, HardwareInventory

        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / "device_tree"

            # Create modified tree with different hardware
            modified_cpus = CPUAllocation(
                total=64,  # Different from original
                host_reserved=[0, 1],
                available=list(range(2, 64)),
            )

            modified_tree = GlobalDeviceTree(
                hardware=HardwareInventory(
                    cpus=modified_cpus,
                    memory=sample_hardware.memory,
                    devices=sample_hardware.devices,
                ),
                instances=sample_tree.instances,
                device_references={},
            )

            manager = DeviceTreeManager(baseline_path=str(baseline_path))

            # Should raise ValidationError
            with pytest.raises(ValidationError, match="cannot modify hardware resources"):
                manager.apply_overlay(sample_tree, modified_tree)


class TestLocking:
    """Test locking mechanism."""

    def test_lock_acquisition(self):
        """Test that lock is acquired and released."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = DeviceTreeManager()
            manager.lock_file = Path(tmpdir) / "test.lock"

            # Lock should not exist initially
            assert not manager.lock_file.exists()

            # Acquire lock
            with manager._acquire_lock():  # pylint: disable=protected-access
                # Lock should exist
                assert manager.lock_file.exists()

            # Lock should be released
            assert not manager.lock_file.exists()

    def test_lock_timeout(self):
        """Test lock timeout when lock is held."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = DeviceTreeManager()
            manager.lock_file = Path(tmpdir) / "test.lock"

            # Create lock file
            manager.lock_file.touch()

            try:
                # Try to acquire lock (should timeout)
                with pytest.raises(KernelInterfaceError, match="Could not acquire lock"):
                    with manager._acquire_lock():  # pylint: disable=protected-access
                        pass
            finally:
                # Cleanup
                if manager.lock_file.exists():
                    manager.lock_file.unlink()
