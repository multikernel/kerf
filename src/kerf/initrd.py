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
Minimal initrd generation for daxfs root filesystem booting.
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

KERF_INITRD_DIR = "/var/lib/kerf/initrd"

INIT_SCRIPT_TEMPLATE = """#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

mkdir -p /newroot
mount -t daxfs -o phys={phys_addr:#x},size={size} none /newroot

if [ ! -d /newroot/bin ] && [ ! -d /newroot/usr ]; then
    echo "Failed to mount daxfs root filesystem"
    echo "Dropping to shell..."
    exec /bin/sh
fi

mkdir -p /newroot/dev /newroot/proc /newroot/sys
mount --move /dev /newroot/dev
mount --move /proc /newroot/proc
mount --move /sys /newroot/sys

exec switch_root /newroot {entrypoint}
"""


class InitrdError(Exception):
    """Exception raised for initrd generation errors."""


def _find_busybox() -> Optional[str]:
    """Find busybox binary on the system."""
    paths = [
        "/bin/busybox",
        "/usr/bin/busybox",
        "/sbin/busybox",
        "/usr/sbin/busybox",
    ]
    for path in paths:
        if os.path.exists(path):
            return path

    which_result = shutil.which("busybox")
    if which_result:
        return which_result

    return None


def _check_busybox_static(busybox_path: str) -> bool:
    """Check if busybox is statically linked."""
    try:
        result = subprocess.run(
            ["file", busybox_path],
            capture_output=True,
            text=True,
            check=True
        )
        return "statically linked" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def create_daxfs_initrd(
    instance_name: str,
    entrypoint: str,
    phys_addr: int,
    size: int,
) -> str:
    """
    Create a minimal initrd for daxfs root filesystem booting.

    Args:
        instance_name: Instance name for naming the initrd
        entrypoint: Path to the entrypoint in the rootfs (e.g., /init)
        phys_addr: Physical address of the daxfs image
        size: Size of the daxfs image in bytes

    Returns:
        Path to the generated initrd file

    Raises:
        InitrdError: If initrd generation fails
    """
    busybox = _find_busybox()
    if not busybox:
        raise InitrdError(
            "busybox not found. Install with: yum install busybox"
        )

    if not _check_busybox_static(busybox):
        busybox_static = shutil.which("busybox-static") or shutil.which("busybox.static")
        if busybox_static:
            busybox = busybox_static
        else:
            raise InitrdError(
                f"busybox at {busybox} is dynamically linked. "
                "A statically linked busybox is required for initrd. "
                "Install with: yum install busybox-static"
            )

    initrd_dir = Path(KERF_INITRD_DIR)
    initrd_dir.mkdir(parents=True, exist_ok=True)
    initrd_path = initrd_dir / f"{instance_name}.cpio.gz"

    with tempfile.TemporaryDirectory() as tmpdir:
        rootfs = Path(tmpdir)

        for dirname in ["bin", "sbin", "dev", "proc", "sys", "newroot"]:
            (rootfs / dirname).mkdir()

        shutil.copy(busybox, rootfs / "bin" / "busybox")
        os.chmod(rootfs / "bin" / "busybox", 0o755)

        busybox_cmds = [
            "sh", "mount", "umount", "mkdir", "cat", "echo",
            "switch_root", "sleep", "ls", "cp", "mv", "rm"
        ]
        for cmd in busybox_cmds:
            (rootfs / "bin" / cmd).symlink_to("busybox")

        init_file = rootfs / "init"
        init_file.write_text(
            INIT_SCRIPT_TEMPLATE.format(
                entrypoint=entrypoint,
                phys_addr=phys_addr,
                size=size,
            )
        )
        os.chmod(init_file, 0o755)

        os.mknod(rootfs / "dev" / "console", stat.S_IFCHR | 0o600, os.makedev(5, 1))
        os.mknod(rootfs / "dev" / "null", stat.S_IFCHR | 0o666, os.makedev(1, 3))

        with subprocess.Popen(
            ["find", ".", "-print0"],
            cwd=rootfs,
            stdout=subprocess.PIPE
        ) as find_cmd:
            with subprocess.Popen(
                ["cpio", "--null", "-o", "-H", "newc"],
                cwd=rootfs,
                stdin=find_cmd.stdout,
                stdout=subprocess.PIPE
            ) as cpio_cmd:
                find_cmd.stdout.close()

                with open(initrd_path, "wb") as f:
                    subprocess.run(
                        ["gzip", "-9"],
                        stdin=cpio_cmd.stdout,
                        stdout=f,
                        check=True
                    )

                cpio_cmd.wait()
                if cpio_cmd.returncode != 0:
                    raise InitrdError("cpio failed to create initramfs")

    return str(initrd_path)
