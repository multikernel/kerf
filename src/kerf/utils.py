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
Utility functions for kernel instance management.

This module provides shared utility functions for interacting with the kernel
filesystem interface for multikernel instances.
"""

from typing import Optional, Tuple
from pathlib import Path

import click


def get_instance_id_from_name(name: str) -> Optional[int]:
    """
    Get instance ID from instance name by reading /sys/fs/multikernel/instances/{name}/id.

    Args:
        name: Instance name

    Returns:
        Instance ID if found, None otherwise
    """
    id_path = Path(f"/sys/fs/multikernel/instances/{name}/id")

    if not id_path.exists():
        return None

    try:
        with open(id_path, "r", encoding="utf-8") as f:
            instance_id = int(f.read().strip())
            return instance_id
    except (OSError, IOError, ValueError):
        return None


def get_instance_name_from_id(instance_id: int) -> Optional[str]:
    """
    Get instance name from instance ID by scanning /sys/fs/multikernel/instances/.

    Args:
        instance_id: Instance ID to search for

    Returns:
        Instance name if found, None otherwise
    """
    instances_dir = Path("/sys/fs/multikernel/instances")

    if not instances_dir.exists():
        return None

    try:
        for inst_dir in instances_dir.iterdir():
            if inst_dir.is_dir():
                found_id = get_instance_id_from_name(inst_dir.name)
                if found_id == instance_id:
                    return inst_dir.name
    except (OSError, IOError):
        pass

    return None


def resolve_instance(name: Optional[str], instance_id: Optional[int]) -> Tuple[str, int]:
    """
    Find the instance a command names, by name or by --id.

    Returns:
        Its name and ID

    Raises:
        click.UsageError: If neither is given or the ID is out of range
        click.ClickException: If there is no such instance
    """
    if name:
        found_id = get_instance_id_from_name(name)
        if found_id is None:
            raise click.ClickException(
                f"Instance '{name}' not found\n"
                "Check available instances in /sys/fs/multikernel/instances/"
            )
        return name, found_id

    if instance_id is None:
        raise click.UsageError("Either instance name or --id must be provided")
    if instance_id < 1 or instance_id > 511:
        raise click.UsageError(f"--id must be between 1 and 511 (got {instance_id})")

    found_name = get_instance_name_from_id(instance_id)
    if not found_name:
        raise click.ClickException(
            f"Instance with ID {instance_id} not found\n"
            "Check available instances in /sys/fs/multikernel/instances/"
        )
    return found_name, instance_id


def get_instance_status(name: str) -> Optional[str]:
    """
    Get instance status from kernel filesystem.

    Args:
        name: Instance name

    Returns:
        Status string if found, None otherwise
    """
    status_path = Path(f"/sys/fs/multikernel/instances/{name}/status")

    if not status_path.exists():
        return None

    try:
        with open(status_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None
