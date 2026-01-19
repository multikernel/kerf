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
Kernel unloading subcommand implementation using kexec_file_load syscall with KEXEC_FILE_UNLOAD flag.
"""

import ctypes
import os
import platform
import sys
from pathlib import Path
from typing import Optional

import click

from ..models import InstanceState
from ..utils import get_instance_id_from_name


def _cleanup_load_resources(instance_name: str, _instance_id: int, verbose: bool) -> None:
    """Clean up resources created by kerf load --image."""
    import shutil

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


# KEXEC flags definitions
KEXEC_FILE_UNLOAD = 0x00000001
KEXEC_MULTIKERNEL = 0x00000010
KEXEC_MK_ID_MASK = 0x0000FFE0
KEXEC_MK_ID_SHIFT = 5


def KEXEC_MK_ID(id: int) -> int:  # pylint: disable=invalid-name
    """Generate KEXEC_MK_ID flag value from kernel ID."""
    return (id << KEXEC_MK_ID_SHIFT) & KEXEC_MK_ID_MASK


# Syscall numbers (architecture-dependent)
# Same syscall number as kexec_file_load
# For x86_64: 320, for x86: 320, for ARM64: 294, for ARM: 382
SYS_KEXEC_FILE_LOAD_X86_64 = 320
SYS_KEXEC_FILE_LOAD_ARM64 = 294
SYS_KEXEC_FILE_LOAD_ARM = 382
SYS_KEXEC_FILE_LOAD_X86 = 320


def get_kexec_file_load_syscall():
    """Get the kexec_file_load syscall number for current architecture."""
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        return SYS_KEXEC_FILE_LOAD_X86_64
    if arch in ("aarch64", "arm64"):
        return SYS_KEXEC_FILE_LOAD_ARM64
    if arch.startswith("arm"):
        return SYS_KEXEC_FILE_LOAD_ARM
    if arch in ("i386", "i686", "x86"):
        return SYS_KEXEC_FILE_LOAD_X86
    # Default to x86_64, but warn
    click.echo(
        f"Warning: Unknown architecture '{arch}', assuming x86_64 syscall number", err=True
    )
    return SYS_KEXEC_FILE_LOAD_X86_64


def kexec_file_unload(flags: int, debug: bool = False) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall_fn = libc.syscall

    syscall_num = get_kexec_file_load_syscall()

    # For unload: kernel_fd = -1, initrd_fd = -1, cmdline = NULL
    kernel_fd = -1
    initrd_fd = -1
    cmdline_len = 0
    cmdline_ptr = None

    # syscall signature: long syscall(long number, ...)
    # kexec_file_load: long kexec_file_load(int kernel_fd, int initrd_fd,
    #                                       unsigned long cmdline_len,
    #                                       const char *cmdline,
    #                                       unsigned long flags)
    syscall_fn.argtypes = [
        ctypes.c_long,  # syscall number
        ctypes.c_int,  # kernel_fd (-1 for unload)
        ctypes.c_int,  # initrd_fd (-1 for unload)
        ctypes.c_ulong,  # cmdline_len (0 for unload)
        ctypes.c_char_p,  # cmdline (NULL for unload)
        ctypes.c_ulong,  # flags
    ]
    syscall_fn.restype = ctypes.c_long

    if debug:
        click.echo(
            f"DEBUG: syscall_num={syscall_num}, kernel_fd={kernel_fd}, initrd_fd={initrd_fd}, cmdline_len={cmdline_len}, flags=0x{flags:x}",
            err=True,
        )
        click.echo(
            f"DEBUG: KEXEC_FILE_UNLOAD=0x{KEXEC_FILE_UNLOAD:x}, KEXEC_MULTIKERNEL=0x{KEXEC_MULTIKERNEL:x}, KEXEC_MK_ID_MASK=0x{KEXEC_MK_ID_MASK:x}, KEXEC_MK_ID_SHIFT={KEXEC_MK_ID_SHIFT}",
            err=True,
        )
        click.echo("DEBUG: cmdline_ptr=NULL (unload operation)", err=True)

    result = syscall_fn(syscall_num, kernel_fd, initrd_fd, cmdline_len, cmdline_ptr, flags)

    if debug:
        click.echo(f"DEBUG: syscall returned: {result}", err=True)

    if result < 0:
        # Get errno
        errno_value = ctypes.get_errno()
        if errno_value == 0:
            # If errno is 0 but result is negative, use -result as errno
            # This handles cases where the syscall returns -errno directly
            errno_value = -result
        raise OSError(errno_value, os.strerror(errno_value))

    return result


@click.command(name="unload")
@click.argument("name", required=False)
@click.option("--id", type=int, help="Multikernel instance ID to unload (alternative to name)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def unload(ctx: click.Context, name: Optional[str], id: Optional[int], verbose: bool):
    """
    Unload a kernel image from a multikernel instance using kexec_file_load with KEXEC_FILE_UNLOAD flag.

    This command unloads a previously loaded kernel image from memory for the specified
    multikernel instance. The instance must be in LOADED state (not ACTIVE).

    Examples:

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

        mk_id_flags = KEXEC_MK_ID(instance_id)
        flags = KEXEC_FILE_UNLOAD | KEXEC_MULTIKERNEL | mk_id_flags

        if verbose:
            click.echo(f"Unloading kernel for instance '{instance_name}' (ID: {instance_id})...")
            click.echo(
                f"Flags: KEXEC_FILE_UNLOAD=0x{KEXEC_FILE_UNLOAD:x}, KEXEC_MULTIKERNEL=0x{KEXEC_MULTIKERNEL:x}, KEXEC_MK_ID({instance_id})=0x{mk_id_flags:x}, combined=0x{flags:x}"
            )
            click.echo("Calling kexec_file_load syscall with KEXEC_FILE_UNLOAD flag...")
        else:
            click.echo(f"Unloading kernel for instance '{instance_name}' (ID: {instance_id})...")

        try:
            debug = ctx.obj.get("debug", False) if ctx and ctx.obj else False
            result = kexec_file_unload(flags, debug=debug)

            if verbose:
                click.echo(f"✓ Kernel unloaded successfully (result: {result})")
            else:
                click.echo("✓ Kernel unloaded successfully")

            # Clean up resources created by kerf load --image
            _cleanup_load_resources(instance_name, instance_id, verbose)

        except OSError as e:
            click.echo(f"Error: kexec_file_unload failed: {e}", err=True)
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
