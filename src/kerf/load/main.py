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
    flags: int
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
    libc = ctypes.CDLL(None)
    syscall_fn = libc.syscall
    
    syscall_num = get_kexec_file_load_syscall()
    
    # Prepare cmdline
    cmdline_bytes = cmdline.encode('utf-8') if cmdline else b''
    cmdline_len = len(cmdline_bytes)
    # Use None for NULL pointer if cmdline is empty, otherwise create pointer
    cmdline_ptr = ctypes.c_char_p(cmdline_bytes) if cmdline_len > 0 else None
    
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
        ctypes.c_char_p,    # cmdline
        ctypes.c_ulong      # flags
    ]
    syscall_fn.restype = ctypes.c_long
    
    result = syscall_fn(
        syscall_num,
        kernel_fd,
        initrd_fd,
        cmdline_len,
        cmdline_ptr,
        flags
    )
    
    if result < 0:
        # Get errno
        errno_value = ctypes.get_errno()
        raise OSError(errno_value, os.strerror(errno_value))
    
    return result


@click.command()
@click.option('--kernel', '-k', required=True, help='Path to kernel image file')
@click.option('--initrd', '-i', help='Path to initrd image file (optional)')
@click.option('--cmdline', '-c', help='Boot command line parameters')
@click.option('--id', type=int, required=True, help='Multikernel instance ID (1-511)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def load(kernel: str, initrd: Optional[str], cmdline: Optional[str],
         id: int, verbose: bool):
    """
    Load kernel image and initrd using kexec_file_load syscall.
    
    This command loads a kernel image into memory using the kexec_file_load
    syscall. The kernel is loaded in multikernel mode with the specified ID.
    
    Example:
    
        kerf load --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1" --id=1
    """
    try:
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
        
        # Validate id (always required for multikernel mode)
        if id < 1 or id > 511:
            click.echo(
                f"Error: --id must be between 1 and 511 (got {id})",
                err=True
            )
            sys.exit(2)  # Invalid command-line arguments
        
        # Always enable multikernel mode
        flags = KEXEC_MULTIKERNEL | KEXEC_MK_ID(id)
        
        if verbose:
            click.echo(f"Multikernel mode enabled with ID: {id}")
        
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
            
            result = kexec_file_load(kernel_fd, initrd_fd, cmdline_str, flags)
            
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

