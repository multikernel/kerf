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
Kernel unloading subcommand implementation.
"""

import sys
from pathlib import Path
from typing import Optional

import click

from ..instance import unload_image
from ..models import InstanceState
from ..utils import get_instance_id_from_name


def _cleanup_load_resources(instance_name: str, instance_id: int, verbose: bool) -> None:
    """Clean up resources created by kerf load --image."""
    import shutil

    from ..daxfs import unmount_daxfs, DaxfsError
    from ..metadata import delete_instance_metadata

    try:
        delete_instance_metadata(instance_id)
    except OSError as e:
        click.echo(f"Warning: Failed to remove instance metadata: {e}", err=True)

    # Unmount the daxfs image, releasing its memory back to the pool
    try:
        unmount_daxfs(instance_name)
        if verbose:
            click.echo(f"✓ Unmounted daxfs image for '{instance_name}'")
    except DaxfsError as e:
        click.echo(f"Warning: {e}", err=True)

    # Clean up extracted rootfs
    rootfs_path = Path("/var/lib/kerf/rootfs") / instance_name
    if rootfs_path.exists():
        try:
            shutil.rmtree(rootfs_path)
            if verbose:
                click.echo(f"✓ Cleaned up rootfs: {rootfs_path}")
        except Exception as e:
            if verbose:
                click.echo(f"Warning: Failed to clean up rootfs: {e}", err=True)


@click.command(name="unload")
@click.argument("name", required=False)
@click.option("--id", type=int, help="Multikernel instance ID to unload (alternative to name)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def unload(name: Optional[str], id: Optional[int], verbose: bool):
    """
    Unload a kernel image from a multikernel instance.

    This command unloads a previously loaded kernel image from memory for the specified
    multikernel instance. The instance must be in LOADED state (not ACTIVE).

    Examples:

        \b
        kerf unload web-server
        kerf unload --id=1
        kerf unload --id=1 --verbose
    """
    try:
        if not name and id is None:
            click.echo("Error: Either instance name or --id must be provided", err=True)
            click.echo("Usage: kerf unload <name>  or  kerf unload --id=<id>", err=True)
            sys.exit(2)

        instance_name = None
        instance_id = None

        if name:
            # Use name, convert to ID
            instance_name = name
            instance_id = get_instance_id_from_name(name)

            if instance_id is None:
                click.echo(f"Error: Instance '{name}' not found", err=True)
                click.echo("Check available instances in /sys/fs/multikernel/instances/", err=True)
                sys.exit(1)

            if verbose:
                click.echo(f"Instance name: {name} (ID: {instance_id})")
        else:
            # Use ID directly, need to find name for status check
            instance_id = id

            if instance_id < 1 or instance_id > 511:
                click.echo(f"Error: --id must be between 1 and 511 (got {instance_id})", err=True)
                sys.exit(2)

            instances_dir = Path("/sys/fs/multikernel/instances")
            if instances_dir.exists():
                for inst_dir in instances_dir.iterdir():
                    if inst_dir.is_dir():
                        found_id = get_instance_id_from_name(inst_dir.name)
                        if found_id == instance_id:
                            instance_name = inst_dir.name
                            break

            if not instance_name:
                click.echo(f"Error: Instance with ID {instance_id} not found", err=True)
                click.echo("Check available instances in /sys/fs/multikernel/instances/", err=True)
                sys.exit(1)

        status_path = Path(f"/sys/fs/multikernel/instances/{instance_name}/status")
        if verbose:
            click.echo(f"Checking instance status for '{instance_name}'...")
            click.echo(f"Status file: {status_path}")

        if not status_path.exists():
            click.echo(f"Error: Instance '{instance_name}' status file not found", err=True)
            click.echo("Please ensure the instance exists", err=True)
            sys.exit(1)

        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status = f.read().strip()

            status_lower = status.lower()

            if status_lower == InstanceState.ACTIVE.value:
                click.echo(
                    f"Error: Cannot unload kernel for instance '{instance_name}' (ID: {instance_id})",
                    err=True,
                )
                click.echo(
                    "Instance is currently ACTIVE (running). Stop the instance first.", err=True
                )
                sys.exit(1)
            elif status_lower != InstanceState.LOADED.value:
                click.echo(
                    f"Error: Cannot unload kernel for instance '{instance_name}' (ID: {instance_id})",
                    err=True,
                )
                click.echo(
                    f"Current status: '{status}' (expected: '{InstanceState.LOADED.value}')",
                    err=True,
                )
                click.echo("Only kernels in LOADED state can be unloaded.", err=True)
                sys.exit(1)

            if verbose:
                click.echo(f"Instance status: '{status}' (OK to unload)")
        except (OSError, IOError) as e:
            click.echo(f"Error: Failed to read status file: {e}", err=True)
            click.echo("Please ensure the instance exists", err=True)
            sys.exit(1)

        click.echo(f"Unloading kernel for instance '{instance_name}' (ID: {instance_id})...")

        try:
            unload_image(instance_id)
            click.echo("✓ Kernel unloaded successfully")

            # Clean up resources created by kerf load --image
            _cleanup_load_resources(instance_name, instance_id, verbose)

        except OSError as e:
            click.echo(f"Error: Unloading failed: {e}", err=True)
            if e.errno == 1:  # EPERM
                click.echo("Note: This operation requires root privileges", err=True)
            elif e.errno == 22:  # EINVAL
                click.echo(
                    f"Error: Invalid arguments for instance '{instance_name}' (ID: {instance_id})",
                    err=True,
                )
                click.echo(
                    "The kernel may not support KEXEC_FILE_UNLOAD, or the instance ID is invalid.",
                    err=True,
                )
            elif e.errno == 3:  # ESRCH
                click.echo("Note: Multikernel instance not found or no kernel loaded.", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)
