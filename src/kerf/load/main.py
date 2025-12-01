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
Kernel loading subcommand implementation using kexec_file_load syscall.
"""

import click
import sys
import os
import ctypes
import platform
from pathlib import Path
from typing import Optional
from ..utils import get_instance_id_from_name, get_instance_name_from_id


# KEXEC flags definitions
KEXEC_MULTIKERNEL = 0x00000010
KEXEC_MK_ID_MASK = 0x0000ffe0
KEXEC_MK_ID_SHIFT = 5


def KEXEC_MK_ID(id: int) -> int:
    """Generate KEXEC_MK_ID flag value from kernel ID."""
    return ((id << KEXEC_MK_ID_SHIFT) & KEXEC_MK_ID_MASK)


# Syscall numbers (architecture-dependent)
# For x86_64: 320, for x86: 320, for ARM64: 294, for ARM: 382
SYS_KEXEC_FILE_LOAD_X86_64 = 320
SYS_KEXEC_FILE_LOAD_ARM64 = 294
SYS_KEXEC_FILE_LOAD_ARM = 382
SYS_KEXEC_FILE_LOAD_X86 = 320


def get_kexec_file_load_syscall():
    """Get the kexec_file_load syscall number for current architecture."""
    arch = platform.machine().lower()
    if arch in ('x86_64', 'amd64'):
        return SYS_KEXEC_FILE_LOAD_X86_64
    elif arch in ('aarch64', 'arm64'):
        return SYS_KEXEC_FILE_LOAD_ARM64
    elif arch.startswith('arm'):
        return SYS_KEXEC_FILE_LOAD_ARM
    elif arch in ('i386', 'i686', 'x86'):
        return SYS_KEXEC_FILE_LOAD_X86
    else:
        # Default to x86_64, but warn
        click.echo(
            f"Warning: Unknown architecture '{arch}', assuming x86_64 syscall number",
            err=True
        )
        return SYS_KEXEC_FILE_LOAD_X86_64


def kexec_file_load(
    kernel_fd: int,
    initrd_fd: int,
    cmdline: str,
    flags: int,
    debug: bool = False
) -> int:
    """
    Call kexec_file_load syscall.
    
    Args:
        kernel_fd: File descriptor for kernel image
        initrd_fd: File descriptor for initrd (use -1 if not provided)
        cmdline: Boot command line string
        flags: KEXEC flags (e.g., KEXEC_MULTIKERNEL | KEXEC_MK_ID(id))
    
    Returns:
        0 on success, -errno on failure
    
    Raises:
        OSError: If syscall fails
    """
    libc = ctypes.CDLL(None, use_errno=True)
    syscall_fn = libc.syscall
    
    syscall_num = get_kexec_file_load_syscall()
    
    # Prepare cmdline
    # kexec_file_load expects: cmdline_len is length INCLUDING null terminator
    #                         cmdline is pointer to null-terminated string (or NULL)
    # Note: kexec-tools uses strlen(cmdline) + 1 for cmdline_len
    cmdline_buf = None
    if cmdline:
        cmdline_bytes = cmdline.encode('utf-8')
        cmdline_buf = ctypes.create_string_buffer(cmdline_bytes)
        cmdline_len = len(cmdline_bytes) + 1
        cmdline_ptr = cmdline_buf
    else:
        cmdline_len = 0
        cmdline_ptr = None
    
    # syscall signature: long syscall(long number, ...)
    # kexec_file_load: long kexec_file_load(int kernel_fd, int initrd_fd,
    #                                       unsigned long cmdline_len,
    #                                       const char *cmdline,
    #                                       unsigned long flags)
    syscall_fn.argtypes = [
        ctypes.c_long,      # syscall number
        ctypes.c_int,       # kernel_fd
        ctypes.c_int,       # initrd_fd
        ctypes.c_ulong,     # cmdline_len
        ctypes.c_char_p,    # cmdline (c_char_p handles None as NULL)
        ctypes.c_ulong      # flags
    ]
    syscall_fn.restype = ctypes.c_long
    
    if debug:
        click.echo(f"DEBUG: syscall_num={syscall_num}, kernel_fd={kernel_fd}, initrd_fd={initrd_fd}, cmdline_len={cmdline_len}, flags=0x{flags:x}", err=True)
        click.echo(f"DEBUG: KEXEC_MULTIKERNEL=0x{KEXEC_MULTIKERNEL:x}, KEXEC_MK_ID_MASK=0x{KEXEC_MK_ID_MASK:x}, KEXEC_MK_ID_SHIFT={KEXEC_MK_ID_SHIFT}", err=True)
        if cmdline:
            click.echo(f"DEBUG: cmdline='{cmdline}', cmdline_ptr={cmdline_ptr}", err=True)
            # Verify the buffer content
            click.echo(f"DEBUG: cmdline_buf.value={cmdline_buf.value!r}, len={len(cmdline_buf.value)}", err=True)
        else:
            click.echo("DEBUG: cmdline_ptr=NULL", err=True)

    result = syscall_fn(
        syscall_num,
        kernel_fd,
        initrd_fd,
        cmdline_len,
        cmdline_ptr,
        flags
    )

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


@click.command()
@click.pass_context
@click.argument('name', required=False)
@click.option('--kernel', '-k', required=True, help='Path to kernel image file')
@click.option('--initrd', '-i', help='Path to initrd image file (optional)')
@click.option('--cmdline', '-c', help='Boot command line parameters')
@click.option('--id', type=int, help='Multikernel instance ID (1-511)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def load(ctx: click.Context, name: Optional[str], kernel: str, initrd: Optional[str], 
         cmdline: Optional[str], id: Optional[int], verbose: bool):
    """
    Load kernel image and initrd using kexec_file_load syscall.
    
    This command loads a kernel image into memory using the kexec_file_load
    syscall. The kernel is loaded in multikernel mode with the specified ID.
    
    Examples:
    
        kerf load web-server --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1"
        kerf load --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1" --id=1
    """
    try:
        if not name and id is None:
            click.echo(
                "Error: Either instance name or --id must be provided",
                err=True
            )
            click.echo(
                "Usage: kerf load <name> --kernel=<path>  or  kerf load --id=<id> --kernel=<path>",
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
            
            instance_name = get_instance_name_from_id(instance_id)
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
            
            if verbose:
                click.echo(f"Instance name: {instance_name} (ID: {instance_id})")
        
        # Validate kernel file
        kernel_path = Path(kernel)
        if not kernel_path.exists():
            click.echo(f"Error: Kernel image '{kernel}' does not exist", err=True)
            sys.exit(3)  # File I/O error
        
        if not kernel_path.is_file():
            click.echo(f"Error: '{kernel}' is not a regular file", err=True)
            sys.exit(3)
        
        if verbose:
            click.echo(f"Kernel image: {kernel_path}")
        
        # Validate initrd file if provided
        initrd_path = None
        if initrd:
            initrd_path = Path(initrd)
            if not initrd_path.exists():
                click.echo(f"Error: Initrd image '{initrd}' does not exist", err=True)
                sys.exit(3)
            
            if not initrd_path.is_file():
                click.echo(f"Error: '{initrd}' is not a regular file", err=True)
                sys.exit(3)
            
            if verbose:
                click.echo(f"Initrd image: {initrd_path}")
        
        # Always enable multikernel mode
        mk_id_flags = KEXEC_MK_ID(instance_id)
        flags = KEXEC_MULTIKERNEL | mk_id_flags
        
        if verbose:
            click.echo(f"Multikernel mode enabled with ID: {instance_id}")
            click.echo(f"Flags: KEXEC_MULTIKERNEL=0x{KEXEC_MULTIKERNEL:x}, KEXEC_MK_ID({instance_id})=0x{mk_id_flags:x}, combined=0x{flags:x}")

        # Prepare command line (default to empty string if not provided)
        cmdline_str = cmdline if cmdline else ''
        
        if verbose:
            click.echo(f"Command line: {cmdline_str if cmdline_str else '(empty)'}")
            click.echo(f"Flags: 0x{flags:x}")
        
        # Open kernel file
        try:
            kernel_fd = os.open(str(kernel_path), os.O_RDONLY)
        except OSError as e:
            click.echo(f"Error: Failed to open kernel image: {e}", err=True)
            sys.exit(3)
        
        # Open initrd file if provided
        initrd_fd = -1
        if initrd_path:
            try:
                initrd_fd = os.open(str(initrd_path), os.O_RDONLY)
            except OSError as e:
                os.close(kernel_fd)
                click.echo(f"Error: Failed to open initrd image: {e}", err=True)
                sys.exit(3)
        
        try:
            # Call kexec_file_load syscall
            if verbose:
                click.echo("Calling kexec_file_load syscall...")
            
            debug = ctx.obj.get('debug', False) if ctx and ctx.obj else False
            result = kexec_file_load(kernel_fd, initrd_fd, cmdline_str, flags, debug=debug)
            
            if verbose:
                click.echo(f"✓ Kernel loaded successfully (result: {result})")
            else:
                click.echo("✓ Kernel loaded successfully")
            
        except OSError as e:
            click.echo(f"Error: kexec_file_load failed: {e}", err=True)
            if e.errno == 1:  # EPERM
                click.echo(
                    "Note: This operation requires root privileges",
                    err=True
                )
            elif e.errno == 22:  # EINVAL
                click.echo(
                    "Note: Invalid arguments. Check kernel image format and flags.",
                    err=True
                )
            elif e.errno == 95:  # EOPNOTSUPP
                click.echo(
                    "Note: kexec_file_load not supported on this system.",
                    err=True
                )
            sys.exit(1)
        
        finally:
            # Clean up file descriptors
            os.close(kernel_fd)
            if initrd_fd >= 0:
                os.close(initrd_fd)
    
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

