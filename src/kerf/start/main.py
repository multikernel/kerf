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
Kernel execution subcommand implementation.
"""

import errno
import os
import sys
from typing import Optional

import click

from .. import instance
from ..utils import resolve_instance


def _explain(error: OSError, name: str) -> Optional[str]:
    """What a failed start means, when the errno alone does not say."""
    if error.errno == errno.ENOEXEC:
        return (
            f"No kernel image is loaded for instance '{name}'. "
            f"Load one first using: kerf load {name} --kernel=<path>"
        )
    if error.errno == errno.EBUSY:
        return f"Instance '{name}' is already running, or another kexec operation is in progress."
    if error.errno == errno.EPERM:
        return "This operation requires root privileges"
    return None


@click.command(name="start")
@click.argument("name", required=False)
@click.option("--id", type=int, help="Multikernel instance ID to boot (alternative to name)")
@click.option("--console", "attach_console", is_flag=True, help="Attach to console after boot")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def start_cmd(name: Optional[str], id: Optional[int], attach_console: bool, verbose: bool):
    """
    Boot a multikernel instance.

    This command boots a previously loaded multikernel instance by name or ID.

    Use --console to immediately attach to the instance's console after boot.
    Press Ctrl+] followed by . to detach from the console.

    Examples:

        \b
        kerf start web-server
        kerf start --id=1
        kerf start web-server --console
    """
    instance_name, instance_id = resolve_instance(name, id)

    click.echo(f"Booting instance '{instance_name}' (ID: {instance_id})...")
    try:
        fd = instance.open_instance(instance_id)
        try:
            instance.start(fd)
        finally:
            os.close(fd)
    except OSError as e:
        click.echo(f"Error: Failed to start instance '{instance_name}': {e}", err=True)
        note = _explain(e, instance_name)
        if note:
            click.echo(f"Note: {note}", err=True)
        sys.exit(1)

    click.echo("✓ Boot command executed successfully")

    if attach_console:
        from ..console import run_console

        console_result = run_console(instance_id, instance_name, verbose)
        if console_result != 0:
            sys.exit(console_result)
