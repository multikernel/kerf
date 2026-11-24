# Copyright 2026 Multikernel Technologies, Inc.
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
Put kerf-init into a spawn's own initramfs.

The kernel unpacks every archive of an initramfs in turn into the same
tree, so one more archive appended to the user's file adds kerf-init
without the file having to be understood, or even decompressed.
"""

import os
import shutil

from .data import KERF_INIT_PATH, get_init_binary_path
from .exceptions import KerfError

_ALIGNMENT = 4


class InitrdError(KerfError):
    """Raised when kerf-init cannot be added to an initrd."""


def _pad(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % _ALIGNMENT)


def _newc_entry(name: str, mode: int, data: bytes) -> bytes:
    # ino, mode, uid, gid, nlink, mtime, filesize, devmajor, devminor,
    # rdevmajor, rdevminor, namesize, check
    fields = (0, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0)
    header = b"070701" + b"".join(b"%08X" % field for field in fields)
    return _pad(header + name.encode() + b"\0") + _pad(data)


def kerf_init_archive() -> bytes:
    """A cpio archive holding kerf-init at KERF_INIT_PATH."""
    binary = get_init_binary_path()
    if not binary.exists():
        raise InitrdError(f"Init binary not found at {binary}. Run 'make' to build it first.")
    return (
        _newc_entry(KERF_INIT_PATH[1:], 0o100755, binary.read_bytes())
        + _newc_entry("TRAILER!!!", 0, b"")
    )


def open_initrd_with_kerf_init(path) -> int:
    """
    Open an initrd for kexec_file_load with kerf-init added to it.

    Returns an anonymous in-memory file; the initrd itself is not modified.
    """
    archive = kerf_init_archive()
    memfd = os.memfd_create("kerf-initrd")
    try:
        with open(path, "rb") as initrd, open(memfd, "wb", closefd=False) as out:
            shutil.copyfileobj(initrd, out)
            # The kernel takes an archive only at an aligned offset, and
            # skips zeros on its way to one
            out.write(b"\0" * (-out.tell() % _ALIGNMENT))
            out.write(archive)
        os.lseek(memfd, 0, os.SEEK_SET)
    except OSError:
        os.close(memfd)
        raise
    return memfd
