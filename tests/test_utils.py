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
Tests for kerf utility functions.
"""

from unittest.mock import mock_open, patch

from kerf.utils import get_instance_id_from_name, get_instance_name_from_id, get_instance_status


class TestInstanceUtils:
    """Test instance utility functions."""

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="42\n")
    def test_get_instance_id_from_name(self, mock_file, mock_exists):
        """Test getting instance ID from name."""
        mock_exists.return_value = True

        instance_id = get_instance_id_from_name("test-instance")

        assert instance_id == 42
        mock_file.assert_called_once()

    @patch("pathlib.Path.exists")
    def test_get_instance_id_from_name_not_found(self, mock_exists):
        """Test getting instance ID when file doesn't exist."""
        mock_exists.return_value = False

        instance_id = get_instance_id_from_name("nonexistent")

        assert instance_id is None

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="invalid")
    def test_get_instance_id_from_name_invalid(self, mock_file, mock_exists):  # pylint: disable=unused-argument
        """Test getting instance ID with invalid data."""
        mock_exists.return_value = True

        instance_id = get_instance_id_from_name("test-instance")

        assert instance_id is None

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.iterdir")
    @patch("kerf.utils.get_instance_id_from_name")
    def test_get_instance_name_from_id(self, mock_get_id, mock_iterdir, mock_exists):
        """Test getting instance name from ID."""
        # Setup mocks
        mock_exists.return_value = True

        # Create mock directory objects using MagicMock instead of Path
        from unittest.mock import MagicMock

        mock_dir1 = MagicMock()
        mock_dir1.is_dir.return_value = True
        mock_dir1.name = "instance1"

        mock_dir2 = MagicMock()
        mock_dir2.is_dir.return_value = True
        mock_dir2.name = "instance2"

        mock_iterdir.return_value = [mock_dir1, mock_dir2]

        # Mock get_instance_id_from_name to return IDs
        def get_id_side_effect(name):
            return {"instance1": 1, "instance2": 2}.get(name)

        mock_get_id.side_effect = get_id_side_effect

        # Test
        name = get_instance_name_from_id(2)
        assert name == "instance2"

    @patch("pathlib.Path.exists")
    def test_get_instance_name_from_id_not_found(self, mock_exists):
        """Test getting instance name when ID doesn't exist."""
        mock_exists.return_value = False

        name = get_instance_name_from_id(999)

        assert name is None

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="active\n")
    def test_get_instance_status(self, mock_file, mock_exists):
        """Test getting instance status."""
        mock_exists.return_value = True

        status = get_instance_status("test-instance")

        assert status == "active"
        mock_file.assert_called_once()

    @patch("pathlib.Path.exists")
    def test_get_instance_status_not_found(self, mock_exists):
        """Test getting instance status when file doesn't exist."""
        mock_exists.return_value = False

        status = get_instance_status("nonexistent")

        assert status is None
