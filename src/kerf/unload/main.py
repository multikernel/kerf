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

import errno
import sys
from pathlib import Path
from typing import Optional

import click

from ..instance import unload_image
from ..utils import resolve_instance


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
    multikernel instance, which must not be running.

    Examples:

        \b
        kerf unload web-server
        kerf unload --id=1
        kerf unload --id=1 --verbose
    """
    instance_name, instance_id = resolve_instance(name, id)

    click.echo(f"Unloading kernel for instance '{instance_name}' (ID: {instance_id})...")
    try:
        unload_image(instance_id)
    except OSError as e:
        click.echo(f"Error: Unloading failed: {e}", err=True)
        if e.errno == errno.EBUSY:
            click.echo(
                f"Note: Instance '{instance_name}' is running. "
                f"Stop it first with: kerf kill {instance_name}",
                err=True,
            )
        elif e.errno == errno.ENOENT:
            click.echo(f"Note: Instance '{instance_name}' has no kernel loaded.", err=True)
        elif e.errno == errno.EPERM:
            click.echo("Note: This operation requires root privileges", err=True)
        sys.exit(1)

    click.echo("✓ Kernel unloaded successfully")

    # Clean up resources created by kerf load --image
    _cleanup_load_resources(instance_name, instance_id, verbose)
