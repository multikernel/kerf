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
Kernel shutdown subcommand implementation using reboot syscall with MULTIKERNEL_HALT command.
"""

import click
import sys
import os
import ctypes
import platform
from pathlib import Path
from typing import Optional
from ..models import InstanceState
from ..utils import get_instance_id_from_name


LINUX_REBOOT_MAGIC1 = 0xfee1dead
LINUX_REBOOT_MAGIC2 = 672274793  # 0x28121969
LINUX_REBOOT_CMD_MULTIKERNEL_HALT = 0x4D4B4C48

SYS_REBOOT_X86_64 = 169
SYS_REBOOT_ARM64 = 142
SYS_REBOOT_ARM = 88
SYS_REBOOT_X86 = 88


def get_reboot_syscall():
    """Get the reboot syscall number for current architecture."""
    arch = platform.machine().lower()
    if arch in ('x86_64', 'amd64'):
        return SYS_REBOOT_X86_64
    elif arch in ('aarch64', 'arm64'):
        return SYS_REBOOT_ARM64
    elif arch.startswith('arm'):
        return SYS_REBOOT_ARM
    elif arch in ('i386', 'i686', 'x86'):
        return SYS_REBOOT_X86
    else:
        click.echo(
            f"Warning: Unknown architecture '{arch}', assuming x86_64 syscall number",
            err=True
        )
        return SYS_REBOOT_X86_64


class MultikernelBootArgs(ctypes.Structure):
    """Structure for multikernel boot arguments."""
    _fields_ = [
        ("mk_id", ctypes.c_int),
    ]


def halt_multikernel(mk_id: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall_fn = libc.syscall

    syscall_num = get_reboot_syscall()

    args = MultikernelBootArgs()
    args.mk_id = mk_id

    syscall_fn.argtypes = [
        ctypes.c_long,      # syscall number
        ctypes.c_ulong,     # LINUX_REBOOT_MAGIC1
        ctypes.c_ulong,     # LINUX_REBOOT_MAGIC2
        ctypes.c_ulong,     # LINUX_REBOOT_CMD_MULTIKERNEL_HALT
        ctypes.POINTER(MultikernelBootArgs)
    ]
    syscall_fn.restype = ctypes.c_long

    result = syscall_fn(
        syscall_num,
        LINUX_REBOOT_MAGIC1,
        LINUX_REBOOT_MAGIC2,
        LINUX_REBOOT_CMD_MULTIKERNEL_HALT,
        ctypes.byref(args)
    )

    if result < 0:
        errno_value = ctypes.get_errno()
        raise OSError(errno_value, os.strerror(errno_value))

    return result


@click.command(name='kill')
@click.argument('name', required=False)
@click.option('--id', type=int, help='Multikernel instance ID to shutdown (alternative to name)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def kill_cmd(name: Optional[str], id: Optional[int], verbose: bool):
    """
    Shutdown a multikernel instance using the reboot syscall.

    This command shuts down a running multikernel instance by name or ID using
    the reboot syscall with the MULTIKERNEL_HALT command.

    Examples:

        kerf kill web-server
        kerf kill --id=1
    """
    try:
        if not name and id is None:
            click.echo(
                "Error: Either instance name or --id must be provided",
                err=True
            )
            click.echo(
                "Usage: kerf kill <name>  or  kerf kill --id=<id>",
                err=True
            )
            sys.exit(2)

        instance_name = None
        instance_id = None

        if name:
            instance_name = name
            instance_id = get_instance_id_from_name(name)

            if instance_id is None:
                click.echo(
                    f"Error: Instance '{name}' not found",
                    err=True
                )
                click.echo(
                    f"Check available instances in /sys/fs/multikernel/instances/",
                    err=True
                )
                sys.exit(1)

            if verbose:
                click.echo(f"Instance name: {name} (ID: {instance_id})")
        else:
            instance_id = id

            if instance_id < 1 or instance_id > 511:
                click.echo(
                    f"Error: --id must be between 1 and 511 (got {instance_id})",
                    err=True
                )
                sys.exit(2)

            instances_dir = Path('/sys/fs/multikernel/instances')
            if instances_dir.exists():
                for inst_dir in instances_dir.iterdir():
                    if inst_dir.is_dir():
                        found_id = get_instance_id_from_name(inst_dir.name)
                        if found_id == instance_id:
                            instance_name = inst_dir.name
                            break

            if not instance_name:
                click.echo(
                    f"Error: Instance with ID {instance_id} not found",
                    err=True
                )
                click.echo(
                    f"Check available instances in /sys/fs/multikernel/instances/",
                    err=True
                )
                sys.exit(1)

        status_path = Path(f'/sys/fs/multikernel/instances/{instance_name}/status')
        if verbose:
            click.echo(f"Checking status for instance '{instance_name}'...")
            click.echo(f"Status file: {status_path}")

        if not status_path.exists():
            click.echo(
                f"Error: Instance '{instance_name}' status file not found",
                err=True
            )
            sys.exit(1)

        try:
            with open(status_path, 'r') as f:
                status = f.read().strip()

            status_lower = status.lower()
            if status_lower != InstanceState.ACTIVE.value:
                click.echo(
                    f"Error: Instance '{instance_name}' (ID: {instance_id}) is not running",
                    err=True
                )
                click.echo(
                    f"Current status: '{status}' (expected: '{InstanceState.ACTIVE.value}')",
                    err=True
                )
                sys.exit(1)

            if verbose:
                click.echo(f"Instance status: '{status}'")
        except (OSError, IOError) as e:
            click.echo(
                f"Error: Failed to read status file: {e}",
                err=True
            )
            sys.exit(1)

        if verbose:
            click.echo(f"✓ Instance '{instance_name}' is running")
            click.echo(f"Instance ID to shutdown: {instance_id}")
            click.echo(f"Using reboot syscall with command: 0x{LINUX_REBOOT_CMD_MULTIKERNEL_HALT:x}")
            click.echo("Calling reboot syscall...")
        else:
            click.echo(f"Shutting down instance '{instance_name}' (ID: {instance_id})...")

        result = halt_multikernel(instance_id)

        if verbose:
            click.echo(f"✓ Shutdown command executed successfully (result: {result})")
        else:
            click.echo("✓ Shutdown command executed successfully")

    except OSError as e:
        click.echo(f"Error: reboot syscall failed: {e}", err=True)
        if e.errno == 1:  # EPERM
            click.echo(
                "Note: This operation requires root privileges",
                err=True
            )
        elif e.errno == 22:  # EINVAL
            click.echo(
                f"Error: Invalid arguments for instance '{instance_name}' (ID: {instance_id})",
                err=True
            )
            click.echo(
                "The kernel may not support the MULTIKERNEL_HALT reboot command, or the instance ID is invalid.",
                err=True
            )
        elif e.errno == 3:  # ESRCH
            click.echo(
                "Note: Multikernel instance not found or not running.",
                err=True
            )
        sys.exit(1)

    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

