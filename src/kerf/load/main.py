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

import ctypes
import os
import platform
import sys
from pathlib import Path
from typing import Optional

import click

from ..utils import get_instance_id_from_name, get_instance_name_from_id


# KEXEC flags definitions
KEXEC_FILE_NO_INITRAMFS = 0x00000004
KEXEC_FILE_DEBUG = 0x00000008
KEXEC_MULTIKERNEL = 0x00000010
KEXEC_MK_ID_MASK = 0x0000FFE0
KEXEC_MK_ID_SHIFT = 5


def KEXEC_MK_ID(id: int) -> int:  # pylint: disable=invalid-name
    """Generate KEXEC_MK_ID flag value from kernel ID."""
    return (id << KEXEC_MK_ID_SHIFT) & KEXEC_MK_ID_MASK


# Syscall numbers (architecture-dependent)
# For x86_64: 320, for x86: 320, for ARM64: 294, for ARM: 382
SYS_KEXEC_FILE_LOAD_X86_64 = 320
SYS_KEXEC_FILE_LOAD_ARM64 = 294
SYS_KEXEC_FILE_LOAD_ARM = 382
SYS_KEXEC_FILE_LOAD_X86 = 320


def build_ip_param(
    ip_addr: Optional[str],
    gateway: Optional[str],
    netmask: str,
    hostname: Optional[str],
    nic: Optional[str],
) -> Optional[str]:
    """
    Build Linux kernel ip= boot parameter.

    Format: ip=<client-ip>:<server-ip>:<gw-ip>:<netmask>:<hostname>:<device>:<autoconf>

    Args:
        ip_addr: Client IP address or "dhcp"
        gateway: Gateway IP address
        netmask: Network mask
        hostname: Hostname
        nic: Network interface name

    Returns:
        ip= parameter string, or None if no IP configuration
    """
    if not ip_addr:
        return None

    if ip_addr.lower() == "dhcp":
        if nic:
            return f"ip=:::::::{nic}:dhcp"
        return "ip=dhcp"

    # Static IP configuration
    # Format: ip=<client-ip>:<server-ip>:<gw-ip>:<netmask>:<hostname>:<device>:<autoconf>
    parts = [
        ip_addr,              # client-ip
        "",                   # server-ip (empty)
        gateway or "",        # gw-ip
        netmask,              # netmask
        hostname or "",       # hostname
        nic or "",            # device
        "off",                # autoconf (off for static)
    ]
    return "ip=" + ":".join(parts)


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


def kexec_file_load(
    kernel_fd: int, initrd_fd: int, cmdline: str, flags: int, debug: bool = False
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
        cmdline_bytes = cmdline.encode("utf-8")
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
        ctypes.c_long,  # syscall number
        ctypes.c_int,  # kernel_fd
        ctypes.c_int,  # initrd_fd
        ctypes.c_ulong,  # cmdline_len
        ctypes.c_char_p,  # cmdline (c_char_p handles None as NULL)
        ctypes.c_ulong,  # flags
    ]
    syscall_fn.restype = ctypes.c_long

    if debug:
        click.echo(
            f"DEBUG: syscall_num={syscall_num}, kernel_fd={kernel_fd}, initrd_fd={initrd_fd}, cmdline_len={cmdline_len}, flags=0x{flags:x}",
            err=True,
        )
        click.echo(
            f"DEBUG: KEXEC_MULTIKERNEL=0x{KEXEC_MULTIKERNEL:x}, KEXEC_MK_ID_MASK=0x{KEXEC_MK_ID_MASK:x}, KEXEC_MK_ID_SHIFT={KEXEC_MK_ID_SHIFT}",
            err=True,
        )
        if cmdline:
            click.echo(f"DEBUG: cmdline='{cmdline}', cmdline_ptr={cmdline_ptr}", err=True)
            # Verify the buffer content
            click.echo(
                f"DEBUG: cmdline_buf.value={cmdline_buf.value!r}, len={len(cmdline_buf.value)}",
                err=True,
            )
        else:
            click.echo("DEBUG: cmdline_ptr=NULL", err=True)

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


@click.command()
@click.pass_context
@click.argument("name", required=False)
@click.option("--kernel", "-k", required=True, help="Path to kernel image file")
@click.option("--initrd", "-i", help="Path to initrd image file (optional)")
@click.option("--cmdline", "-c", help="Boot command line parameters")
@click.option("--id", type=int, help="Multikernel instance ID (1-511)")
@click.option("--image", help="Docker image to use as rootfs (e.g., nginx:latest)")
@click.option("--entrypoint", help="Override image entrypoint for init")
@click.option("--rootfs-dir", help="Use existing directory as rootfs instead of Docker image")
@click.option("--ip", "ip_addr", help="IP address for spawn kernel (or 'dhcp')")
@click.option("--gateway", help="Default gateway IP address")
@click.option("--netmask", default="255.255.255.0", help="Network mask (default: 255.255.255.0)")
@click.option("--nic", help="Network interface name (e.g., eth0)")
@click.option("--hostname", help="Hostname for spawn kernel")
@click.option("--console", "console_device", help="Console device (e.g., mktty0)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def load(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    ctx: click.Context,
    name: Optional[str],
    kernel: str,
    initrd: Optional[str],
    cmdline: Optional[str],
    id: Optional[int],
    image: Optional[str],
    entrypoint: Optional[str],
    rootfs_dir: Optional[str],
    ip_addr: Optional[str],
    gateway: Optional[str],
    netmask: str,
    nic: Optional[str],
    hostname: Optional[str],
    console_device: Optional[str],
    verbose: bool,
):
    """
    Load kernel image using kexec_file_load syscall.

    This command loads a kernel image into memory using the kexec_file_load
    syscall. The kernel is loaded in multikernel mode with the specified ID.

    When --image or --rootfs-dir is provided, a daxfs image is created and the
    kernel boots directly into daxfs as root filesystem (no initrd needed).
    Use --initrd to override this and provide your own initrd.

    Examples:

        kerf load web-server --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1"
        kerf load --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1" --id=1

        # Load with Docker image as rootfs
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest

        # Load with custom entrypoint
        kerf load worker --kernel=/boot/vmlinuz --image=python:3.11 \\
                 --entrypoint=/app/worker.py

        # Load with pre-extracted rootfs directory
        kerf load custom --kernel=/boot/vmlinuz --rootfs-dir=/mnt/rootfs \\
                 --entrypoint=/sbin/init

        # Load with static IP configuration
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest \\
                 --ip=192.168.1.100 --gateway=192.168.1.1 --nic=eth0

        # Load with DHCP
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest \\
                 --ip=dhcp --nic=eth0

        # Load with console enabled
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest \\
                 --console=mktty0
    """
    try:
        if not name and id is None:
            click.echo("Error: Either instance name or --id must be provided", err=True)
            click.echo(
                "Usage: kerf load <name> --kernel=<path>  or  kerf load --id=<id> --kernel=<path>",
                err=True,
            )
            sys.exit(2)

        if image and rootfs_dir:
            click.echo("Error: --image and --rootfs-dir are mutually exclusive", err=True)
            sys.exit(2)

        if rootfs_dir and not entrypoint:
            click.echo("Error: --entrypoint is required when using --rootfs-dir", err=True)
            sys.exit(2)

        instance_name = None
        instance_id = None

        if name:
            instance_name = name
            instance_id = get_instance_id_from_name(name)

            if instance_id is None:
                click.echo(f"Error: Instance '{name}' not found", err=True)
                click.echo("Check available instances in /sys/fs/multikernel/instances/", err=True)
                sys.exit(1)

            if verbose:
                click.echo(f"Instance name: {name} (ID: {instance_id})")
        else:
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

        # Handle Docker image or rootfs directory
        daxfs_image = None
        init_path = None

        if image:
            from ..docker.image import extract_image, DockerError
            from ..daxfs import create_daxfs_image, DaxfsError, inject_kerf_init

            try:
                if verbose:
                    click.echo(f"Extracting Docker image: {image}")

                rootfs_path, default_cmd = extract_image(image, instance_name)

                if verbose:
                    click.echo(f"Rootfs extracted to: {rootfs_path}")
                    click.echo(f"Image command: {default_cmd}")

                if entrypoint:
                    init_path = entrypoint
                elif default_cmd:
                    init_path = default_cmd[0]
                else:
                    click.echo(
                        "Error: Image has no ENTRYPOINT/CMD, use --entrypoint to specify init",
                        err=True
                    )
                    sys.exit(2)

                if not initrd_path:
                    inject_kerf_init(rootfs_path)
                    if verbose:
                        click.echo(f"Injected /init wrapper (entrypoint: {init_path})")

                if verbose:
                    click.echo(f"Creating daxfs image for instance {instance_name}...")

                daxfs_image = create_daxfs_image(rootfs_path, instance_name)

                if verbose:
                    click.echo(f"Daxfs image created at phys=0x{daxfs_image.phys_addr:x}, size={daxfs_image.size}")
                    click.echo(f"Entrypoint: {init_path}")

            except DockerError as e:
                click.echo(f"Error: Docker operation failed: {e}", err=True)
                sys.exit(1)
            except DaxfsError as e:
                click.echo(f"Error: Daxfs image creation failed: {e}", err=True)
                sys.exit(1)
            except FileNotFoundError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)

        elif rootfs_dir:
            from ..daxfs import create_daxfs_image, DaxfsError, inject_kerf_init

            try:
                rootfs_path = Path(rootfs_dir)
                if not rootfs_path.is_dir():
                    click.echo(f"Error: Rootfs directory '{rootfs_dir}' does not exist", err=True)
                    sys.exit(3)

                init_path = entrypoint

                if not initrd_path:
                    inject_kerf_init(str(rootfs_path))
                    if verbose:
                        click.echo(f"Injected /init wrapper (entrypoint: {init_path})")

                if verbose:
                    click.echo(f"Using rootfs directory: {rootfs_path}")
                    click.echo(f"Creating daxfs image for instance {instance_name}...")

                daxfs_image = create_daxfs_image(str(rootfs_path), instance_name)

                if verbose:
                    click.echo(f"Daxfs image created at phys=0x{daxfs_image.phys_addr:x}, size={daxfs_image.size}")
                    click.echo(f"Entrypoint: {init_path}")

            except DaxfsError as e:
                click.echo(f"Error: Daxfs image creation failed: {e}", err=True)
                sys.exit(1)
            except FileNotFoundError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)

        # Always enable multikernel mode
        mk_id_flags = KEXEC_MK_ID(instance_id)
        flags = KEXEC_MULTIKERNEL | mk_id_flags

        # If no initrd, set NO_INITRAMFS flag
        if not initrd_path:
            flags |= KEXEC_FILE_NO_INITRAMFS

        if verbose:
            click.echo(f"Multikernel mode enabled with ID: {instance_id}")
            flag_parts = [f"KEXEC_MULTIKERNEL=0x{KEXEC_MULTIKERNEL:x}", f"KEXEC_MK_ID({instance_id})=0x{mk_id_flags:x}"]
            if not initrd_path:
                flag_parts.append(f"KEXEC_FILE_NO_INITRAMFS=0x{KEXEC_FILE_NO_INITRAMFS:x}")
            click.echo(f"Flags: {', '.join(flag_parts)}, combined=0x{flags:x}")

        # Prepare command line
        cmdline_parts = []
        if cmdline:
            cmdline_parts.append(cmdline)

        # Add daxfs root parameters if using daxfs
        if daxfs_image and not initrd_path:
            cmdline_parts.append("rootfstype=daxfs")
            cmdline_parts.append(f"rootflags=phys=0x{daxfs_image.phys_addr:x},size={daxfs_image.size}")
            cmdline_parts.append("init=/init")
            if init_path:
                # Quote entrypoint if it contains spaces
                if ' ' in init_path:
                    cmdline_parts.append(f'kerf.entrypoint="{init_path}"')
                else:
                    cmdline_parts.append(f"kerf.entrypoint={init_path}")
            if verbose:
                click.echo(f"Daxfs root: rootfstype=daxfs rootflags=phys=0x{daxfs_image.phys_addr:x},size={daxfs_image.size}")
                if init_path:
                    click.echo(f"Entrypoint: kerf.entrypoint={init_path}")

        # Add IP configuration if specified
        ip_param = build_ip_param(ip_addr, gateway, netmask, hostname, nic)
        if ip_param:
            cmdline_parts.append(ip_param)
            if verbose:
                click.echo(f"Network config: {ip_param}")

        # Add console device if specified
        if console_device:
            cmdline_parts.append(f"console={console_device}")
            if verbose:
                click.echo(f"Console: console={console_device}")

        cmdline_str = " ".join(cmdline_parts)

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

            debug = ctx.obj.get("debug", False) if ctx and ctx.obj else False
            if debug:
                flags |= KEXEC_FILE_DEBUG
            result = kexec_file_load(kernel_fd, initrd_fd, cmdline_str, flags, debug=debug)

            if verbose:
                click.echo(f"✓ Kernel loaded successfully (result: {result})")
            else:
                click.echo("✓ Kernel loaded successfully")

        except OSError as e:
            click.echo(f"Error: kexec_file_load failed: {e}", err=True)
            if e.errno == 1:  # EPERM
                click.echo("Note: This operation requires root privileges", err=True)
            elif e.errno == 16:  # EBUSY
                click.echo(
                    f"Note: Instance '{instance_name}' already has a kernel loaded. "
                    f"Run 'kerf unload {instance_name}' first.", err=True
                )
            elif e.errno == 22:  # EINVAL
                click.echo(
                    "Note: Invalid arguments. Check kernel image format and flags.", err=True
                )
            elif e.errno == 95:  # EOPNOTSUPP
                click.echo("Note: kexec_file_load not supported on this system.", err=True)
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
