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

import errno
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import click

from ..metadata import (
    inspect_initrd_image,
    inspect_kernel_image,
    save_instance_metadata,
)
from ..initrd import InitrdError, open_initrd_with_kerf_init
from ..instance import KEXEC_FILE_DEBUG, load_image
from ..lockdown import image_supports_lockdown
from ..utils import resolve_instance
from ..signature import kexec_signatures_enforced
from ..vmlinuz import BZIMAGE_HEADER_SIZE, VmlinuzError, is_bzimage, open_kernel_fd


def build_ip_param(
    ip_addr: Optional[str],
    gateway: Optional[str],
    netmask: Optional[str],
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

    dhcp = ip_addr.lower() == "dhcp"
    if dhcp and not nic and not hostname:
        return "ip=dhcp"

    parts = [
        "" if dhcp else ip_addr,
        "",
        "" if dhcp else gateway or "",
        "" if dhcp else netmask or "255.255.255.0",
        hostname or "",
        nic or "",
        "dhcp" if dhcp else "off",
    ]
    return "ip=" + ":".join(parts)


# What a spawn kernel cannot do without, per architecture. On arm64 the host
# can only force a wedged spawn down with a stop interrupt, and that reaches
# a CPU that has interrupts off only when the spawn runs with pseudo-NMIs
# (it must also be built with CONFIG_ARM64_PSEUDO_NMI).
REQUIRED_KERNEL_PARAMS = {
    "aarch64": ("irqchip.gicv3_pseudo_nmi=1",),
    "arm64": ("irqchip.gicv3_pseudo_nmi=1",),
}


def required_kernel_params(cmdline_parts: List[str], arch: Optional[str] = None) -> List[str]:
    """
    The parameters that must still be appended to a spawn's command line.

    The kernel honors the last setting of a parameter, so one that the
    command line sets differently is appended again and wins; one whose
    last setting already is the required one is left alone.

    Args:
        cmdline_parts: The command line so far, as it will be joined
        arch: Architecture to decide for instead of this machine's

    Returns:
        Parameters to append, in order
    """
    arch = (arch or platform.machine()).lower()
    words = " ".join(cmdline_parts).split()
    missing = []
    for param in REQUIRED_KERNEL_PARAMS.get(arch, ()):
        key = param.split("=", 1)[0] + "="
        settings = [word for word in words if word.startswith(key)]
        if not settings or settings[-1] != param:
            missing.append(param)
    return missing


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
@click.option("--netmask", help="Network mask (default: 255.255.255.0)")
@click.option("--nic", help="Network interface name (e.g., eth0)")
@click.option("--hostname", help="Hostname for spawn kernel")
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
    netmask: Optional[str],
    nic: Optional[str],
    hostname: Optional[str],
    verbose: bool,
):
    """
    Load kernel image using kexec_file_load syscall.

    This command loads a kernel image into memory using the kexec_file_load
    syscall. The kernel is loaded in multikernel mode with the specified ID.

    Both an uncompressed ELF vmlinux and a compressed bzImage (vmlinuz) are
    accepted. For a bzImage, the embedded vmlinux is extracted in userspace
    before loading, since the kernel side only accepts ELF images. Note that
    an extracted vmlinux carries no signature, so this will not work on
    hosts enforcing kexec signature verification (Secure Boot lockdown).

    The command line built here is what kerf start begins with for every
    run; it adds the console and what its own options ask for, such as
    --lockdown.
    kerf-init is added to an --initrd so that kerf start --deny can run it
    first; it does nothing unless a start asks for it.

    When --image or --rootfs-dir is provided, a daxfs image is created and the
    kernel boots directly into daxfs as root filesystem (no initrd needed if
    daxfs is built into the kernel).

    If the spawn kernel builds daxfs as a module, pass --initrd with an
    initramfs containing daxfs.ko and an init that loads the module, mounts
    daxfs using the rootflags= parameters from the command line, and
    switch_roots into it (exec'ing init= from the command line).

    Examples:

        \b
        kerf load web-server --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1"
        kerf load --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \\
                 --cmdline="root=/dev/sda1" --id=1

        \b
        # Load with Docker image as rootfs
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest

        \b
        # Load with custom entrypoint
        kerf load worker --kernel=/boot/vmlinuz --image=python:3.11 \\
                 --entrypoint=/app/worker.py

        \b
        # Load with pre-extracted rootfs directory
        kerf load custom --kernel=/boot/vmlinuz --rootfs-dir=/mnt/rootfs \\
                 --entrypoint=/sbin/init

        \b
        # Load with static IP configuration
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest \\
                 --ip=192.168.1.100 --gateway=192.168.1.1 --nic=eth0

        \b
        # Load with DHCP
        kerf load web-server --kernel=/boot/vmlinuz --image=nginx:latest \\
                 --ip=dhcp --nic=eth0
    """
    try:
        if image and rootfs_dir:
            click.echo("Error: --image and --rootfs-dir are mutually exclusive", err=True)
            sys.exit(2)

        if rootfs_dir and not entrypoint:
            click.echo("Error: --entrypoint is required when using --rootfs-dir", err=True)
            sys.exit(2)

        if entrypoint and not image and not rootfs_dir:
            click.echo("Error: --entrypoint requires --image or --rootfs-dir", err=True)
            sys.exit(2)

        network = {"--gateway": gateway, "--netmask": netmask, "--nic": nic, "--hostname": hostname}
        given = [option for option, value in network.items() if value]
        if given and not ip_addr:
            click.echo(f"Error: {', '.join(given)} requires --ip (an address or dhcp)", err=True)
            sys.exit(2)

        instance_name, instance_id = resolve_instance(name, id)
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
        image_id = None

        if image:
            from ..docker.image import extract_image, DockerError
            from ..daxfs import create_daxfs_image, DaxfsError

            try:
                if verbose:
                    click.echo(f"Extracting Docker image: {image}")

                rootfs_path, default_cmd, image_id = extract_image(image, instance_name)

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
            from ..daxfs import create_daxfs_image, DaxfsError

            try:
                rootfs_path = Path(rootfs_dir)
                if not rootfs_path.is_dir():
                    click.echo(f"Error: Rootfs directory '{rootfs_dir}' does not exist", err=True)
                    sys.exit(3)

                init_path = entrypoint

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

        # Prepare command line
        cmdline_parts = []
        if cmdline:
            cmdline_parts.append(cmdline)

        # Add daxfs root parameters if using daxfs. With an initrd, the
        # kernel does not mount root itself; the initrd is expected to
        # load daxfs, mount it using these same rootflags, and switch_root
        # into it (exec'ing init=, which is kerf-init).
        if daxfs_image:
            from ..daxfs import KERF_INIT_PATH

            cmdline_parts.append("rootfstype=daxfs")
            cmdline_parts.append(f"rootflags=phys=0x{daxfs_image.phys_addr:x},size={daxfs_image.size}")
            cmdline_parts.append(f"init={KERF_INIT_PATH}")
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

        # Last, so that it wins over anything given above
        required = required_kernel_params(cmdline_parts)
        if required:
            cmdline_parts.extend(required)
            if verbose:
                click.echo(f"Required on this architecture: {' '.join(required)}")

        cmdline_str = " ".join(cmdline_parts)

        if verbose:
            click.echo(f"Command line: {cmdline_str if cmdline_str else '(empty)'}")

        # Open kernel file, extracting the embedded vmlinux if it is a
        # bzImage, since the kexec ELF loader only accepts vmlinux
        try:
            with open(kernel_path, "rb") as f:
                kernel_is_bzimage = is_bzimage(f.read(BZIMAGE_HEADER_SIZE))
            if kernel_is_bzimage and verbose:
                click.echo("bzImage detected, extracting embedded vmlinux")
            if kernel_is_bzimage and kexec_signatures_enforced():
                click.echo(
                    "Warning: this host enforces kernel signatures, and a bzImage is "
                    "loaded as its extracted, unsigned vmlinux. Sign it with 'kerf sign'.",
                    err=True,
                )
            kernel_fd = open_kernel_fd(kernel_path)
            # Read from the image now, for kerf start --lockdown to warn from
            lockdown_lsm = image_supports_lockdown(kernel_fd)
        except VmlinuzError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(3)
        except OSError as e:
            click.echo(f"Error: Failed to open kernel image: {e}", err=True)
            sys.exit(3)

        # Open initrd file if provided
        initrd_fd = -1
        if initrd_path:
            try:
                # What is in the initramfs is fixed here, and a later
                # kerf start --deny needs kerf-init in it
                initrd_fd = open_initrd_with_kerf_init(initrd_path)
                if verbose:
                    click.echo("Added kerf-init to the initrd")
            except (OSError, InitrdError) as e:
                os.close(kernel_fd)
                click.echo(f"Error: Failed to open initrd image: {e}", err=True)
                sys.exit(3)

        try:
            # Call kexec_file_load syscall
            if verbose:
                click.echo("Calling kexec_file_load syscall...")

            debug = ctx.obj.get("debug", False) if ctx and ctx.obj else False
            flags = KEXEC_FILE_DEBUG if debug else 0
            # The descriptor is a handle only: the image stays loaded without it
            os.close(load_image(instance_id, kernel_fd, initrd_fd, cmdline_str, flags))

            click.echo("✓ Kernel loaded successfully")

            # Record image provenance for kerf show; the kernel only
            # knows about the loaded segments, not the source file
            rootfs_meta = None
            if image or rootfs_dir:
                rootfs_meta = {
                    "source": "docker" if image else "directory",
                    "image": image,
                    "path": str(rootfs_path),
                    "entrypoint": init_path,
                }
                if image_id:
                    rootfs_meta["image_id"] = image_id
                if daxfs_image:
                    rootfs_meta["daxfs"] = {
                        "phys_addr": daxfs_image.phys_addr,
                        "size": daxfs_image.size,
                    }
            try:
                save_instance_metadata(instance_id, {
                    "kernel": inspect_kernel_image(kernel_path),
                    "initrd": inspect_initrd_image(initrd_path) if initrd_path else None,
                    "rootfs": rootfs_meta,
                    "lockdown_lsm": lockdown_lsm,
                    "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
            except OSError as e:
                click.echo(f"Warning: Failed to record instance metadata: {e}", err=True)

        except OSError as e:
            click.echo(f"Error: kexec_file_load failed: {e}", err=True)
            if e.errno == errno.EPERM and os.geteuid() == 0:
                click.echo(
                    "Note: Refused by kernel lockdown; the kernel image must be signed "
                    "(see 'kerf sign').", err=True
                )
            elif e.errno == 1:  # EPERM
                click.echo("Note: This operation requires root privileges", err=True)
            elif e.errno == errno.ENODATA:
                click.echo(
                    "Note: The kernel image is not signed and this host requires "
                    "signatures. Sign it with 'kerf sign'.", err=True
                )
            elif e.errno in (errno.ENOKEY, errno.EKEYREJECTED, errno.EKEYEXPIRED,
                             errno.EKEYREVOKED, errno.EBADMSG):
                click.echo(
                    "Note: The kernel signature was rejected. The signing certificate "
                    "must be trusted by this host (for example enrolled as a MOK).", err=True
                )
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

    except click.ClickException:
        raise
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)
