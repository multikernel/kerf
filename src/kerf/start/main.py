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
from typing import List, Optional, Tuple

import click

from .. import instance
from ..load.main import required_kernel_params
from ..lockdown import (
    LOCKDOWN_LEVELS,
    LockdownError,
    LockdownRequest,
    lockdown_help,
    resolve_lockdown,
)
from ..metadata import load_instance_metadata, save_instance_metadata
from ..utils import resolve_instance


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


def split_cmdline(cmdline: str) -> List[str]:
    """Split a kernel command line into parameters, as the kernel does."""
    words = []
    word = ""
    quoted = False
    for char in cmdline:
        if char == '"':
            quoted = not quoted
        if char.isspace() and not quoted:
            if word:
                words.append(word)
            word = ""
        else:
            word += char
    if word:
        words.append(word)
    return words


def _runs_kerf_init(words: List[str]) -> bool:
    """Whether the spawn's PID 1 is kerf-init, for good."""
    inits = [word for word in words if word.startswith(("init=", "rdinit="))]
    return any(word.strip('"').endswith("kerf-init") for word in inits)


def compose_cmdline(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    base: str,
    has_initrd: bool,
    console: str,
    lockdown: Optional[str],
    deny: Optional[str],
    entrypoint: Optional[str],
    append: Optional[str],
) -> Tuple[str, Optional[LockdownRequest]]:
    """
    Build the command line of one run from the one the image was loaded with.

    Raises:
        LockdownError, click.UsageError: The options cannot be honored for this spawn
    """
    words = split_cmdline(base)
    kerf_init = _runs_kerf_init(words)

    if entrypoint:
        if not kerf_init:
            raise click.UsageError(
                "--entrypoint needs kerf-init as the spawn's init; "
                "load it with --image or --rootfs-dir"
            )
        words = [word for word in words if not word.startswith("kerf.entrypoint=")]
        quote = '"' if " " in entrypoint else ""
        words.append(f"kerf.entrypoint={quote}{entrypoint}{quote}")

    words += split_cmdline(append or "")
    if not any(word.startswith("console=") for word in words):
        words.append(f"console={console}")
    request = resolve_lockdown(lockdown, deny, " ".join(words), kerf_init, has_initrd)
    # After --append: with --deny on an initramfs spawn, kerf's rdinit= has
    # to be the last one for kerf-init to run first
    if request:
        words += request.params
    words += required_kernel_params(words)
    return " ".join(words), request


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
    if error.errno == errno.E2BIG:
        return "The command line of this run is longer than the spawn kernel accepts."
    return None


def _warn(request: Optional[LockdownRequest], metadata: Optional[dict]) -> None:
    if not request:
        return
    for warning in request.warnings:
        click.echo(f"Warning: {warning}", err=True)
    # Found out from the image when it was loaded
    if metadata and metadata.get("lockdown_lsm") is False:
        click.echo(
            "Warning: this kernel image does not appear to be built with the "
            "lockdown LSM (CONFIG_SECURITY_LOCKDOWN_LSM); it will ignore "
            f"lockdown={request.level}.",
            err=True,
        )


def _record_run(instance_id: int, metadata: Optional[dict], cmdline: Optional[str],
                request: Optional[LockdownRequest]) -> None:
    """Keep what this run was started with, for kerf show."""
    if metadata is None:
        return
    metadata["last_run"] = {
        "cmdline": cmdline,
        "lockdown": {
            "level": request.level,
            "deny": list(request.deny),
            "pinned": request.chained,
        } if request else None,
    }
    try:
        save_instance_metadata(instance_id, metadata)
    except OSError as e:
        click.echo(f"Warning: Failed to record instance metadata: {e}", err=True)


@click.command(name="start", epilog=lockdown_help())
@click.argument("name", required=False)
@click.option("--id", type=int, help="Multikernel instance ID to boot (alternative to name)")
@click.option("--attach", "-a", "attach_console", is_flag=True,
              help="Attach to console after boot")
@click.option(
    "--console",
    "console_device",
    default="mktty0",
    show_default=True,
    help="Console device for this run, ignored when the command line already sets console=",
)
@click.option(
    "--lockdown",
    type=click.Choice(LOCKDOWN_LEVELS),
    help="Lock down the spawn kernel for this run. integrity restricts what lets "
    "root modify the running kernel; confidentiality also restricts what lets root "
    "read kernel memory. Both are listed below.",
)
@click.option(
    "--deny",
    help="With --lockdown=integrity, also restrict these confidentiality features "
    "(comma-separated names from the list below). Needs a spawn kernel with BPF "
    "LSM and BTF, loaded with --image, --rootfs-dir or --initrd. With an initrd "
    "alone, root in the spawn is trusted not to lift the restrictions.",
)
@click.option("--ip", "ip_addr", help="IP address for spawn kernel (or 'dhcp')")
@click.option("--gateway", help="Default gateway IP address")
@click.option("--netmask", help="Network mask (default: 255.255.255.0)")
@click.option("--nic", help="Network interface name (e.g., eth0)")
@click.option("--hostname", help="Hostname for spawn kernel")
@click.option("--entrypoint", help="Run this instead of the entrypoint the spawn was loaded with")
@click.option("--append", help="Boot parameters to add to the command line for this run")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def start_cmd(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    name: Optional[str],
    id: Optional[int],
    attach_console: bool,
    console_device: str,
    lockdown: Optional[str],
    deny: Optional[str],
    ip_addr: Optional[str],
    gateway: Optional[str],
    netmask: Optional[str],
    nic: Optional[str],
    hostname: Optional[str],
    entrypoint: Optional[str],
    append: Optional[str],
    verbose: bool,
):
    """
    Boot a multikernel instance.

    This command boots a previously loaded multikernel instance by name or ID.

    Every run boots with the command line the instance was loaded with, plus
    the console and whatever the network options, --lockdown, --deny,
    --entrypoint and --append ask for. They apply to this run only: the next
    start without them has no network configuration, is not locked down and
    runs the loaded entrypoint again.

    --deny is enforced by kerf-init. Loaded with --image or --rootfs-dir it is
    the spawn's PID 1 and keeps the restrictions in place for good. Loaded
    with --initrd alone, kerf-init applies the restrictions before the
    initramfs's own init runs and then hands over to it; from there on they
    hold only until root in the spawn removes them.

    Use --attach to immediately attach to the instance's console after boot.
    Press Ctrl+] followed by . to detach from the console.

    Examples:

        \b
        kerf start web-server
        kerf start --id=1
        kerf start web-server --attach

        \b
        # Start with a static IP configuration, or with DHCP
        kerf start web-server --ip=192.168.1.100 --gateway=192.168.1.1 --nic=eth0
        kerf start web-server --ip=dhcp --nic=eth0

        \b
        # Start with no console
        kerf start web-server --console=ttynull

        \b
        # Start a spawn whose root can neither modify the kernel nor read
        # /proc/kcore and IPsec keys, but can still use perf and tracing
        kerf start web-server --lockdown=integrity --deny=kcore,xfrm-secret

        \b
        # Run something else for once, with one more boot parameter
        kerf start worker --entrypoint=/app/migrate.py --append=loglevel=7
    """
    network = {"--gateway": gateway, "--netmask": netmask, "--nic": nic, "--hostname": hostname}
    given = [option for option, value in network.items() if value]
    if given and not ip_addr:
        raise click.UsageError(f"{', '.join(given)} requires --ip (an address or dhcp)")
    # Behind --append, so that the address asked for here is the one in effect
    ip_param = build_ip_param(ip_addr, gateway, netmask, hostname, nic)
    append = " ".join(part for part in (append, ip_param) if part) or None

    instance_name, instance_id = resolve_instance(name, id)
    metadata = load_instance_metadata(instance_id)

    cmdline, request = None, None

    click.echo(f"Booting instance '{instance_name}' (ID: {instance_id})...")
    try:
        fd = instance.open_instance(instance_id)
        try:
            cmdline, request = compose_cmdline(
                instance.loaded_cmdline(fd), bool(metadata and metadata.get("initrd")),
                console_device, lockdown, deny, entrypoint, append,
            )
            _warn(request, metadata)
            if verbose:
                click.echo(f"Command line: {cmdline}")
            instance.start(fd, cmdline)
        finally:
            os.close(fd)
    except LockdownError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except OSError as e:
        click.echo(f"Error: Failed to start instance '{instance_name}': {e}", err=True)
        note = _explain(e, instance_name)
        if note:
            click.echo(f"Note: {note}", err=True)
        sys.exit(1)

    click.echo("✓ Boot command executed successfully")
    _record_run(instance_id, metadata, cmdline, request)

    if attach_console:
        from ..console import run_console

        console_result = run_console(instance_id, instance_name, verbose)
        if console_result != 0:
            sys.exit(console_result)
