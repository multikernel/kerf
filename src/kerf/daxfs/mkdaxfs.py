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
DAXFS filesystem image creation.

Creates a daxfs image from a directory and writes it to DMA heap memory.
The daxfs filesystem holds a reference to the dma-buf, so the memory persists
even after the dmabuf fd is closed.
"""

import ctypes
import fcntl
import mmap
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kerf.data import get_init_binary_path

DAXFS_MAGIC = 0x64646178
DAXFS_VERSION = 1
DAXFS_BLOCK_SIZE = 4096
DAXFS_INODE_SIZE = 64
DAXFS_ROOT_INO = 1

DMA_HEAP_IOC_MAGIC = ord('H')
DMA_HEAP_IOCTL_ALLOC = 0xC0184800


class DaxfsError(Exception):
    """Exception raised for daxfs errors."""


@dataclass
class DaxfsImage:
    """Represents a created daxfs image."""
    phys_addr: int
    size: int


@dataclass
class FileEntry:
    """Represents a file entry in the daxfs image."""
    path: str
    name: str
    stat: os.stat_result
    ino: int = 0
    parent_ino: int = 0
    first_child: int = 0
    next_sibling: int = 0
    data_offset: int = 0
    name_strtab_offset: int = 0


class DaxfsBuilder:
    """Builds a daxfs filesystem image."""

    def __init__(self, src_dir: str):
        self.src_dir = Path(src_dir)
        self.files: list[FileEntry] = []
        self.next_ino = 1
        self.strtab_size = 0

    def _add_file(self, relpath: str, stat_result: os.stat_result) -> FileEntry:
        """Add a file entry."""
        entry = FileEntry(
            path=relpath,
            name=os.path.basename(relpath) if relpath else "",
            stat=stat_result,
            ino=self.next_ino,
        )
        self.next_ino += 1
        self.strtab_size += len(entry.name) + 1
        self.files.append(entry)
        return entry

    def _scan_directory_recursive(self, relpath: str) -> None:
        """Recursively scan a directory."""
        if relpath:
            fullpath = self.src_dir / relpath
        else:
            fullpath = self.src_dir

        try:
            entries = list(fullpath.iterdir())
        except PermissionError:
            return

        for entry in sorted(entries, key=lambda e: e.name):
            if relpath:
                newrel = f"{relpath}/{entry.name}"
            else:
                newrel = entry.name

            try:
                file_stat = entry.lstat()
            except (PermissionError, FileNotFoundError):
                continue

            self._add_file(newrel, file_stat)

            if entry.is_dir() and not entry.is_symlink():
                self._scan_directory_recursive(newrel)

    def scan(self) -> None:
        """Scan the source directory."""
        root_stat = self.src_dir.lstat()
        self._add_file("", root_stat)
        self._scan_directory_recursive("")

    def _find_by_path(self, path: str) -> Optional[FileEntry]:
        """Find file entry by path."""
        for e in self.files:
            if e.path == path:
                return e
        return None

    def _find_by_ino(self, ino: int) -> Optional[FileEntry]:
        """Find file entry by inode number."""
        for e in self.files:
            if e.ino == ino:
                return e
        return None

    def build_tree(self) -> None:
        """Build the directory tree structure."""
        for e in self.files:
            if not e.path:
                e.parent_ino = 0
                continue

            parent_path = os.path.dirname(e.path)
            parent = self._find_by_path(parent_path)
            if parent:
                e.parent_ino = parent.ino

                if parent.first_child == 0:
                    parent.first_child = e.ino
                else:
                    sibling = self._find_by_ino(parent.first_child)
                    while sibling and sibling.next_sibling:
                        sibling = self._find_by_ino(sibling.next_sibling)
                    if sibling:
                        sibling.next_sibling = e.ino

    def calculate_offsets(self) -> None:
        """Calculate data offsets for all files."""
        inode_offset = DAXFS_BLOCK_SIZE
        strtab_offset = inode_offset + len(self.files) * DAXFS_INODE_SIZE
        data_offset = self._align(strtab_offset + self.strtab_size, DAXFS_BLOCK_SIZE)
        str_off = 0

        for e in self.files:
            e.name_strtab_offset = str_off
            str_off += len(e.name) + 1

            if self._is_regular(e.stat.st_mode) or self._is_symlink(e.stat.st_mode):
                e.data_offset = data_offset
                data_offset += self._align(e.stat.st_size, DAXFS_BLOCK_SIZE)

    def calculate_total_size(self) -> int:
        """Calculate total image size."""
        inode_offset = DAXFS_BLOCK_SIZE
        strtab_offset = inode_offset + len(self.files) * DAXFS_INODE_SIZE
        data_offset = self._align(strtab_offset + self.strtab_size, DAXFS_BLOCK_SIZE)
        total = data_offset

        for e in self.files:
            if self._is_regular(e.stat.st_mode) or self._is_symlink(e.stat.st_mode):
                total += self._align(e.stat.st_size, DAXFS_BLOCK_SIZE)

        return total

    def write_image(self, mem: mmap.mmap, mem_size: int) -> None:
        """Write the daxfs image to memory."""
        inode_offset = DAXFS_BLOCK_SIZE
        strtab_offset = inode_offset + len(self.files) * DAXFS_INODE_SIZE
        data_offset = self._align(strtab_offset + self.strtab_size, DAXFS_BLOCK_SIZE)
        total_size = self.calculate_total_size()

        mem.seek(0)
        mem.write(b'\x00' * mem_size)

        mem.seek(0)
        super_block = struct.pack(
            '<IIIIQQIIQQQ',
            DAXFS_MAGIC,
            DAXFS_VERSION,
            0,
            DAXFS_BLOCK_SIZE,
            total_size,
            inode_offset,
            len(self.files),
            DAXFS_ROOT_INO,
            strtab_offset,
            self.strtab_size,
            data_offset,
        )
        mem.write(super_block)

        for e in self.files:
            mem.seek(inode_offset + (e.ino - 1) * DAXFS_INODE_SIZE)
            inode_data = struct.pack(
                '<IIIIQQIIIIII8s',
                e.ino,
                e.stat.st_mode,
                e.stat.st_uid,
                e.stat.st_gid,
                e.stat.st_size,
                e.data_offset,
                e.name_strtab_offset,
                len(e.name),
                e.parent_ino,
                e.stat.st_nlink,
                e.first_child,
                e.next_sibling,
                b'\x00' * 8,
            )
            mem.write(inode_data)

            mem.seek(strtab_offset + e.name_strtab_offset)
            mem.write(e.name.encode('utf-8') + b'\x00')

            if self._is_regular(e.stat.st_mode):
                fullpath = self.src_dir / e.path
                try:
                    with open(fullpath, 'rb') as f:
                        content = f.read()
                    mem.seek(e.data_offset)
                    mem.write(content)
                except (PermissionError, FileNotFoundError, IsADirectoryError):
                    pass

            elif self._is_symlink(e.stat.st_mode):
                fullpath = self.src_dir / e.path
                try:
                    target = os.readlink(fullpath)
                    mem.seek(e.data_offset)
                    mem.write(target.encode('utf-8'))
                except (PermissionError, FileNotFoundError):
                    pass

    @staticmethod
    def _align(value: int, alignment: int) -> int:
        """Align value to a boundary."""
        return (value + alignment - 1) & ~(alignment - 1)

    @staticmethod
    def _is_regular(mode: int) -> bool:
        """Check if mode is regular file."""
        import stat
        return stat.S_ISREG(mode)

    @staticmethod
    def _is_symlink(mode: int) -> bool:
        """Check if mode is symlink."""
        import stat
        return stat.S_ISLNK(mode)


def _get_libc():
    """Get libc with errno support."""
    return ctypes.CDLL(None, use_errno=True)


def _syscall(libc, nr, *args):
    """Invoke a syscall and raise OSError on failure."""
    fn = libc.syscall
    fn.restype = ctypes.c_long
    ret = fn(ctypes.c_long(nr), *[ctypes.c_long(a) if isinstance(a, int) else a for a in args])
    if ret < 0:
        errno_val = ctypes.get_errno()
        raise OSError(errno_val, os.strerror(errno_val))
    return ret


def _get_daxfs_iomem() -> tuple[int, int]:
    """
    Parse /proc/iomem to find daxfs allocation.
    Returns (phys_addr, size) tuple.
    """
    try:
        with open('/proc/iomem', 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(' : ')
                if len(parts) == 2 and parts[1] == 'daxfs':
                    addr_range = parts[0].strip()
                    start_str, end_str = addr_range.split('-')
                    start = int(start_str, 16)
                    end = int(end_str, 16)
                    return start, end - start + 1
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    raise DaxfsError("Could not find 'daxfs' entry in /proc/iomem")


def _allocate_dma_heap(heap_path: str, size: int) -> tuple[int, mmap.mmap]:
    """
    Allocate memory from DMA heap.
    Returns (dmabuf_fd, mmap) tuple.
    """
    heap_fd = os.open(heap_path, os.O_RDWR)
    try:
        alloc_data = struct.pack('<QIIQ', size, 0, os.O_RDWR | os.O_CLOEXEC, 0)
        result = fcntl.ioctl(heap_fd, DMA_HEAP_IOCTL_ALLOC, alloc_data)
        _, dmabuf_fd, _, _ = struct.unpack('<QIIQ', result)
    finally:
        os.close(heap_fd)

    mem = mmap.mmap(dmabuf_fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    return dmabuf_fd, mem


KERF_DAXFS_MNT_DIR = "/var/lib/kerf/daxfs"

# Syscall numbers (same on all architectures, added in kernel 5.2+)
SYS_MOVE_MOUNT = 429
SYS_FSOPEN = 430
SYS_FSCONFIG = 431
SYS_FSMOUNT = 432

# fsconfig command constants
FSCONFIG_SET_STRING = 1
FSCONFIG_SET_FD = 5
FSCONFIG_CMD_CREATE = 6

# move_mount flags
MOVE_MOUNT_F_EMPTY_PATH = 0x00000004

AT_FDCWD = -100


def inject_kerf_init(rootfs_path: str) -> None:
    """Inject /init binary into rootfs.

    The entrypoint is passed via kernel cmdline as kerf.entrypoint=<path>.
    """
    rootfs = Path(rootfs_path)

    # Copy the pre-built init binary
    init_binary = get_init_binary_path()
    if not init_binary.exists():
        raise DaxfsError(
            f"Init binary not found at {init_binary}. "
            "Run 'make' to build it first."
        )

    init_path = rootfs / "init"
    shutil.copy2(init_binary, init_path)
    os.chmod(init_path, 0o755)


def _mount_daxfs(instance_name: str, dmabuf_fd: int) -> None:
    """
    Mount daxfs on the host kernel, passing the dmabuf fd directly.

    Uses fsopen/fsconfig/fsmount/move_mount syscalls. The daxfs filesystem
    takes a reference on the dma-buf, so the fd can be closed after mounting.

    Args:
        instance_name: Name of the multikernel instance
        dmabuf_fd: File descriptor for the dma-buf holding the filesystem image
    """
    mnt_dir = Path(KERF_DAXFS_MNT_DIR) / instance_name
    mnt_dir.mkdir(parents=True, exist_ok=True)

    libc = _get_libc()
    fs_fd = -1
    mnt_fd = -1

    try:
        # fsopen("daxfs", 0)
        fstype = b"daxfs"
        fs_fd = _syscall(libc, SYS_FSOPEN, ctypes.c_char_p(fstype), 0)

        # fsconfig(fs_fd, FSCONFIG_SET_FD, "dmabuf", NULL, dmabuf_fd)
        _syscall(libc, SYS_FSCONFIG, fs_fd, FSCONFIG_SET_FD,
                 ctypes.c_char_p(b"dmabuf"), 0, dmabuf_fd)

        # fsconfig(fs_fd, FSCONFIG_SET_STRING, "name", instance_name, 0)
        name_bytes = instance_name.encode('utf-8')
        _syscall(libc, SYS_FSCONFIG, fs_fd, FSCONFIG_SET_STRING,
                 ctypes.c_char_p(b"name"), ctypes.c_char_p(name_bytes), 0)

        # fsconfig(fs_fd, FSCONFIG_CMD_CREATE, NULL, NULL, 0)
        _syscall(libc, SYS_FSCONFIG, fs_fd, FSCONFIG_CMD_CREATE, 0, 0, 0)

        # fsmount(fs_fd, 0, 0)
        mnt_fd = _syscall(libc, SYS_FSMOUNT, fs_fd, 0, 0)

        # move_mount(mnt_fd, "", AT_FDCWD, mountpoint, MOVE_MOUNT_F_EMPTY_PATH)
        mountpoint = str(mnt_dir).encode('utf-8')
        _syscall(libc, SYS_MOVE_MOUNT, mnt_fd, ctypes.c_char_p(b""),
                 AT_FDCWD, ctypes.c_char_p(mountpoint), MOVE_MOUNT_F_EMPTY_PATH)
    except OSError as e:
        raise DaxfsError(f"Failed to mount daxfs: {e}") from e
    finally:
        if mnt_fd >= 0:
            os.close(mnt_fd)
        if fs_fd >= 0:
            os.close(fs_fd)


def create_daxfs_image(
    rootfs_path: str,
    instance_name: str,
    heap_path: str = "/dev/dma_heap/multikernel",
    size: Optional[int] = None,
) -> DaxfsImage:
    """
    Create a daxfs filesystem image from a directory.

    Args:
        rootfs_path: Path to the root filesystem directory
        instance_name: Name of the multikernel instance
        heap_path: Path to the DMA heap device
        size: Size to allocate (if None, calculated automatically with 10% padding)

    Returns:
        DaxfsImage with physical address and size

    Raises:
        DaxfsError: If image creation fails
    """
    if not os.path.isdir(rootfs_path):
        raise DaxfsError(f"Rootfs directory '{rootfs_path}' does not exist")

    builder = DaxfsBuilder(rootfs_path)
    builder.scan()
    builder.build_tree()
    builder.calculate_offsets()

    required_size = builder.calculate_total_size()

    if size is None:
        size = int(required_size * 1.1)
        size = (size + DAXFS_BLOCK_SIZE - 1) & ~(DAXFS_BLOCK_SIZE - 1)

    if required_size > size:
        raise DaxfsError(
            f"Required size {required_size} exceeds allocated size {size}"
        )

    try:
        dmabuf_fd, mem = _allocate_dma_heap(heap_path, size)
    except OSError as e:
        raise DaxfsError(f"Failed to allocate from DMA heap: {e}") from e

    try:
        builder.write_image(mem, size)
        mem.close()
    except Exception as e:
        os.close(dmabuf_fd)
        raise DaxfsError(f"Failed to write daxfs image: {e}") from e

    try:
        _mount_daxfs(instance_name, dmabuf_fd)
    except DaxfsError:
        os.close(dmabuf_fd)
        raise

    # daxfs now holds a reference to the dma-buf, safe to close
    os.close(dmabuf_fd)

    # Get the physical address from /proc/iomem for the spawn kernel rootflags
    phys_addr, actual_size = _get_daxfs_iomem()

    return DaxfsImage(
        phys_addr=phys_addr,
        size=actual_size,
    )
