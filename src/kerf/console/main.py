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
Console attachment subcommand implementation for mktty device.
"""

import os
import select
import sys
import termios
import tty
from pathlib import Path
from typing import Optional

import click

from ..models import InstanceState
from ..utils import get_instance_id_from_name, get_instance_name_from_id, get_instance_status


MKTTY_DEVICE = "/dev/mktty"
CTRL_CLOSE_BRACKET = 0x1D  # Ctrl+]


def run_console(instance_id: int, instance_name: str, verbose: bool = False) -> int:
    """
    Attach to a running instance's console via mktty device.

    Args:
        instance_id: The instance ID to attach to
        instance_name: The instance name (for display purposes)
        verbose: Enable verbose output

    Returns:
        0 on success, non-zero on error
    """
    # Check if mktty device exists
    if not Path(MKTTY_DEVICE).exists():
        click.echo(f"Error: Console device {MKTTY_DEVICE} not found", err=True)
        click.echo("Make sure the mktty kernel module is loaded", err=True)
        return 1

    # Check if stdin is a tty
    if not sys.stdin.isatty():
        click.echo("Error: stdin is not a terminal", err=True)
        return 1

    click.echo(f"Connecting to console for instance '{instance_name}' (ID: {instance_id})...")
    click.echo("Escape sequence: Ctrl+] followed by . to detach")
    click.echo("")

    # Open mktty device
    try:
        mktty_fd = os.open(MKTTY_DEVICE, os.O_RDWR)
    except OSError as e:
        click.echo(f"Error: Failed to open {MKTTY_DEVICE}: {e}", err=True)
        if e.errno == 13:  # EACCES
            click.echo("Note: This operation may require root privileges", err=True)
        return 1

    try:
        # Write instance ID to mktty device to initiate connection
        instance_id_str = f"{instance_id}\n"
        os.write(mktty_fd, instance_id_str.encode("utf-8"))

        # Save terminal settings
        stdin_fd = sys.stdin.fileno()
        stdout_fd = sys.stdout.fileno()
        old_settings = termios.tcgetattr(stdin_fd)

        try:
            # Enter raw mode
            tty.setraw(stdin_fd)

            # State for detach sequence detection
            saw_ctrl_bracket = False

            # I/O loop
            while True:
                readable, _, _ = select.select([stdin_fd, mktty_fd], [], [], 0.1)

                for fd in readable:
                    if fd == stdin_fd:
                        # Read from stdin
                        data = os.read(stdin_fd, 1)
                        if not data:
                            # EOF on stdin
                            return 0

                        byte = data[0]

                        # Check for detach sequence: Ctrl+] followed by .
                        if saw_ctrl_bracket:
                            if byte == ord('.'):
                                # Detach sequence complete
                                return 0
                            else:
                                # Not a detach sequence, send the buffered Ctrl+]
                                os.write(mktty_fd, bytes([CTRL_CLOSE_BRACKET]))
                                saw_ctrl_bracket = False
                                # Fall through to send current byte

                        if byte == CTRL_CLOSE_BRACKET:
                            # Start of potential detach sequence
                            saw_ctrl_bracket = True
                        else:
                            # Send to mktty device
                            os.write(mktty_fd, data)

                    elif fd == mktty_fd:
                        # Read from mktty device
                        try:
                            data = os.read(mktty_fd, 4096)
                            if data:
                                # Translate \n to \r\n for proper terminal display
                                # in raw mode (kernel outputs \n, terminal needs \r\n)
                                # First normalize any existing \r\n to \n, then convert
                                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
                                os.write(stdout_fd, data)
                        except OSError:
                            # Device closed or error
                            return 0

        finally:
            # Restore terminal settings
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
            click.echo("")
            click.echo("Disconnected from console.")

    finally:
        os.close(mktty_fd)

    return 0


@click.command(name="console")
@click.argument("name", required=False)
@click.option("--id", type=int, help="Instance ID (alternative to name)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def console(name: Optional[str], id: Optional[int], verbose: bool):
    """
    Attach to a running instance's console.

    Connect to the console of a running multikernel instance via the mktty
    device. All input including Ctrl+C is passed through to the spawn kernel.

    To detach from the console, press Ctrl+] followed by . (period).

    Examples:

        kerf console web-server
        kerf console --id=1
    """
    try:
        if not name and id is None:
            click.echo("Error: Either instance name or --id must be provided", err=True)
            click.echo("Usage: kerf console <name>  or  kerf console --id=<id>", err=True)
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
            # Use ID directly, need to find name
            instance_id = id

            if instance_id < 1 or instance_id > 511:
                click.echo(f"Error: --id must be between 1 and 511 (got {instance_id})", err=True)
                sys.exit(2)

            instance_name = get_instance_name_from_id(instance_id)
            if not instance_name:
                click.echo(f"Error: Instance with ID {instance_id} not found", err=True)
                click.echo("Check available instances in /sys/fs/multikernel/instances/", err=True)
                sys.exit(1)

            if verbose:
                click.echo(f"Instance name: {instance_name} (ID: {instance_id})")

        # Check instance status - must be active
        status = get_instance_status(instance_name)
        if status is None:
            click.echo(f"Error: Failed to read status for instance '{instance_name}'", err=True)
            sys.exit(1)

        if status.lower() != InstanceState.ACTIVE.value:
            click.echo(
                f"Error: Instance '{instance_name}' is not active (status: '{status}')",
                err=True,
            )
            click.echo(
                f"Console attachment requires the instance to be in '{InstanceState.ACTIVE.value}' state.",
                err=True,
            )
            click.echo(f"Start the instance with: kerf exec {instance_name}", err=True)
            sys.exit(1)

        if verbose:
            click.echo(f"Instance status: {status}")

        # Run the console
        result = run_console(instance_id, instance_name, verbose)
        sys.exit(result)

    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)
