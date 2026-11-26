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
Kernel shutdown subcommand implementation.
"""

import errno
import os
import sys
from typing import Optional

import click

from .. import instance
from ..utils import resolve_instance

# How long a graceful shutdown may take before kerf gives up waiting
HALT_TIMEOUT_MS = 30000


@click.command(name='kill')
@click.argument('name', required=False)
@click.option('--id', type=int, help='Multikernel instance ID to shutdown (alternative to name)')
@click.option('--force', '-f', is_flag=True, help='Force shutdown without graceful termination')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def kill_cmd(name: Optional[str], id: Optional[int], force: bool, verbose: bool):
    """
    Shutdown a multikernel instance.

    This command shuts down a running multikernel instance by name or ID. The
    spawn kernel is asked to shut down; with --force its CPUs are stopped
    without asking.

    Examples:

        \b
        kerf kill web-server
        kerf kill --id=1
        kerf kill --force web-server
    """
    instance_name, instance_id = resolve_instance(name, id)

    try:
        fd = instance.open_instance(instance_id)
    except OSError as e:
        click.echo(f"Error: Failed to open instance '{instance_name}': {e}", err=True)
        if e.errno == errno.EPERM:
            click.echo("Note: This operation requires root privileges", err=True)
        sys.exit(1)

    try:
        state = instance.info(fd)
        # A force halt is also valid on a stopped instance: a previous
        # halt settles the state before every CPU is confirmed parked,
        # and rerunning it is how CPUs missed the first time are rescued.
        if not state.running and not (force and state.loaded):
            click.echo(
                f"Error: Instance '{instance_name}' (ID: {instance_id}) is not running",
                err=True
            )
            sys.exit(1)

        if verbose and state.running:
            click.echo(f"✓ Instance '{instance_name}' is running")
        force_str = " (force)" if force else ""
        click.echo(f"Shutting down instance '{instance_name}' (ID: {instance_id}){force_str}...")

        instance.halt(fd, force=force, timeout_ms=HALT_TIMEOUT_MS)
        click.echo("✓ Shutdown command executed successfully")
    except OSError as e:
        click.echo(f"Error: Failed to shut down instance '{instance_name}': {e}", err=True)
        if e.errno == errno.ETIMEDOUT and not force:
            click.echo(
                f"Instance '{instance_name}' did not answer the shutdown request; "
                "stop it with --force",
                err=True
            )
        sys.exit(1)
    finally:
        os.close(fd)
