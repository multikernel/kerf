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
DAXFS filesystem image creation (on-disk format v8).

Creates a daxfs image from a directory and writes it to DMA heap memory.
The image layout mirrors daxfs tools/mkdaxfs.c:

    [ Superblock (1 block) | Base image | Overlay region ]

The base image holds the read-only snapshot (inode table, flat dirent
arrays, file data); the overlay region makes the mount writable. The
daxfs filesystem holds a reference to the dma-buf, so the memory
persists even after the dmabuf fd is closed.
"""

import ctypes
import fcntl
import mmap
import os
import shutil
import stat as stat_mod
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from kerf.data import get_init_binary_path

DAXFS_SUPER_MAGIC = 0x64617835  # "dax5"
DAXFS_VERSION = 8
DAXFS_MIN_BLOCK_SIZE = 4096
DAXFS_INODE_SIZE = 64
DAXFS_NAME_MAX = 255
# The inode size field counts DAXFS_DIRENT_SIZE per entry, but the kernel
# indexes the dirent array as struct daxfs_dirent[], which the compiler
# pads to 4-byte alignment. Entries must be laid out at the padded stride.
DAXFS_DIRENT_SIZE = 16 + DAXFS_NAME_MAX
DAXFS_DIRENT_STRIDE = (DAXFS_DIRENT_SIZE + 3) & ~3
DAXFS_ROOT_INO = 1

DAXFS_OVERLAY_MAGIC = 0x6F766C79  # "ovly"
DAXFS_OVERLAY_VERSION = 2
DAXFS_OVL_FREE_END = 0xFFFFFFFFFFFFFFFF
DAXFS_OVERLAY_BUCKET_SIZE = 16
DAXFS_DEFAULT_OVERLAY_POOL = 64 * 1024 * 1024
DAXFS_DEFAULT_BUCKET_COUNT = 65536

# Block size = native page size, validated by the kernel at mount time
DAXFS_BLOCK_SIZE = max(mmap.PAGESIZE, DAXFS_MIN_BLOCK_SIZE)

SUPERBLOCK_FORMAT = '<IIIIQQQQIIQQQIIQQII'
INODE_FORMAT = '<IIIIQQI28x'
DIRENT_HEADER_FORMAT = '<IIH6x'
OVERLAY_HEADER_FORMAT = '<IIQQQQQQQQ'

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
    data_offset: int = 0
    child_count: int = 0
    is_hardlink: bool = False


class DaxfsBuilder:
    """Builds a daxfs v8 filesystem image: base snapshot plus write overlay."""

    def __init__(self, src_dir: str,
                 overlay_pool_size: int = DAXFS_DEFAULT_OVERLAY_POOL):
        self.src_dir = Path(src_dir)
        self.files: List[FileEntry] = []
        self.files_by_path: Dict[str, FileEntry] = {}
        # First entry seen for each hardlinked (st_dev, st_ino)
        self.hardlinks: Dict[Tuple[int, int], FileEntry] = {}
        self.next_ino = 1
        self.base_size = 0
        self.overlay_pool_size = overlay_pool_size
        self.overlay_buckets = self._auto_bucket_count(overlay_pool_size)

    @staticmethod
    def _align(value: int, alignment: int) -> int:
        return (value + alignment - 1) & ~(alignment - 1)

    @staticmethod
    def _auto_bucket_count(pool_size: int) -> int:
        """Size the overlay bucket count from the pool.

        Target ~2x pool pages so the open-addressed table stays near 0.5
        load, floored at the mkdaxfs default (matches tools/mkdaxfs.c).
        """
        pool_pages = (pool_size + DAXFS_BLOCK_SIZE - 1) // DAXFS_BLOCK_SIZE
        buckets = 1
        while buckets < pool_pages * 2 and buckets < (1 << 31):
            buckets <<= 1
        return max(buckets, DAXFS_DEFAULT_BUCKET_COUNT)

    def _add_file(self, relpath: str, stat_result: os.stat_result) -> FileEntry:
        entry = FileEntry(
            path=relpath,
            name=os.path.basename(relpath)[:DAXFS_NAME_MAX] if relpath else "",
            stat=stat_result,
        )

        # Hardlinked regular files share one inode and one data extent
        if stat_mod.S_ISREG(stat_result.st_mode) and stat_result.st_nlink > 1:
            key = (stat_result.st_dev, stat_result.st_ino)
            owner = self.hardlinks.get(key)
            if owner is not None:
                entry.ino = owner.ino
                entry.is_hardlink = True
            else:
                entry.ino = self.next_ino
                self.next_ino += 1
                self.hardlinks[key] = entry
        else:
            entry.ino = self.next_ino
            self.next_ino += 1

        self.files.append(entry)
        self.files_by_path[relpath] = entry
        return entry

    def _scan_directory_recursive(self, relpath: str) -> None:
        fullpath = self.src_dir / relpath if relpath else self.src_dir

        try:
            entries = list(fullpath.iterdir())
        except PermissionError:
            return

        for entry in sorted(entries, key=lambda e: e.name):
            newrel = f"{relpath}/{entry.name}" if relpath else entry.name

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

    def build_tree(self) -> None:
        """Assign parent inodes and count directory children."""
        for e in self.files:
            if not e.path:
                e.parent_ino = 0
                continue

            parent = self.files_by_path.get(os.path.dirname(e.path))
            if parent:
                e.parent_ino = parent.ino
                parent.child_count += 1

    def calculate_offsets(self) -> None:
        """Assign base-relative data offsets and compute the base size."""
        data_offset = self._align(len(self.files) * DAXFS_INODE_SIZE,
                                  DAXFS_BLOCK_SIZE)

        for e in self.files:
            mode = e.stat.st_mode
            if stat_mod.S_ISREG(mode):
                if e.is_hardlink:
                    owner = self.hardlinks[(e.stat.st_dev, e.stat.st_ino)]
                    e.data_offset = owner.data_offset
                else:
                    e.data_offset = data_offset
                    data_offset += self._align(e.stat.st_size, DAXFS_BLOCK_SIZE)
            elif stat_mod.S_ISLNK(mode):
                e.data_offset = data_offset
                data_offset += self._align(e.stat.st_size + 1, DAXFS_BLOCK_SIZE)
            elif stat_mod.S_ISDIR(mode) and e.child_count > 0:
                e.data_offset = data_offset
                data_offset += self._align(e.child_count * DAXFS_DIRENT_STRIDE,
                                           DAXFS_BLOCK_SIZE)

        self.base_size = data_offset

    def _overlay_region_size(self) -> int:
        bucket_array = self._align(
            self.overlay_buckets * DAXFS_OVERLAY_BUCKET_SIZE, DAXFS_BLOCK_SIZE)
        return DAXFS_BLOCK_SIZE + bucket_array + self.overlay_pool_size

    def _overlay_offset(self) -> int:
        return self._align(DAXFS_BLOCK_SIZE + self.base_size, DAXFS_BLOCK_SIZE)

    def calculate_total_size(self) -> int:
        """Calculate total image size: superblock + base + overlay."""
        return self._overlay_offset() + self._overlay_region_size()

    def write_image(self, mem: mmap.mmap, mem_size: int) -> None:
        """Write the daxfs image to memory."""
        total_size = self.calculate_total_size()
        if total_size > mem_size:
            raise DaxfsError(
                f"Image size {total_size} exceeds allocated size {mem_size}"
            )

        zero_chunk = b'\x00' * (4 * 1024 * 1024)
        mem.seek(0)
        remaining = mem_size
        while remaining > 0:
            mem.write(zero_chunk[:min(remaining, len(zero_chunk))])
            remaining -= min(remaining, len(zero_chunk))

        base_offset = DAXFS_BLOCK_SIZE
        base_data_offset = self._align(len(self.files) * DAXFS_INODE_SIZE,
                                       DAXFS_BLOCK_SIZE)
        overlay_offset = self._overlay_offset()
        bucket_array = self._align(
            self.overlay_buckets * DAXFS_OVERLAY_BUCKET_SIZE, DAXFS_BLOCK_SIZE)

        mem.seek(0)
        mem.write(struct.pack(
            SUPERBLOCK_FORMAT,
            DAXFS_SUPER_MAGIC,
            DAXFS_VERSION,
            DAXFS_BLOCK_SIZE,
            0,                          # reserved0
            total_size,
            base_offset,
            self.base_size,
            0,                          # inode_offset (relative to base)
            len(self.files),
            DAXFS_ROOT_INO,
            base_data_offset,           # data_offset (relative to base)
            overlay_offset,
            self._overlay_region_size(),
            self.overlay_buckets,
            self.overlay_buckets.bit_length() - 1,
            0, 0, 0, 0,                 # no pcache
        ))

        self._write_base(mem, base_offset)
        self._write_overlay(mem, overlay_offset, bucket_array)

    def _write_base(self, mem: mmap.mmap, base_offset: int) -> None:
        children: Dict[int, List[FileEntry]] = {}
        for e in self.files:
            if e.path:
                children.setdefault(e.parent_ino, []).append(e)

        for e in self.files:
            mode = e.stat.st_mode

            if stat_mod.S_ISDIR(mode):
                size = e.child_count * DAXFS_DIRENT_SIZE
            elif stat_mod.S_ISREG(mode) or stat_mod.S_ISLNK(mode):
                size = e.stat.st_size
            else:
                size = 0

            mem.seek(base_offset + (e.ino - 1) * DAXFS_INODE_SIZE)
            mem.write(struct.pack(
                INODE_FORMAT,
                e.ino,
                mode,
                e.stat.st_uid,
                e.stat.st_gid,
                size,
                e.data_offset,
                e.stat.st_nlink,
            ))

            if stat_mod.S_ISREG(mode) and not e.is_hardlink:
                self._write_file_data(mem, base_offset + e.data_offset, e)
            elif stat_mod.S_ISLNK(mode):
                try:
                    target = os.readlink(self.src_dir / e.path)
                    mem.seek(base_offset + e.data_offset)
                    mem.write(target.encode('utf-8') + b'\x00')
                except (PermissionError, FileNotFoundError):
                    pass
            elif stat_mod.S_ISDIR(mode) and e.child_count > 0:
                for i, child in enumerate(children.get(e.ino, [])):
                    name = child.name.encode('utf-8')[:DAXFS_NAME_MAX]
                    mem.seek(base_offset + e.data_offset
                             + i * DAXFS_DIRENT_STRIDE)
                    mem.write(struct.pack(DIRENT_HEADER_FORMAT,
                                          child.ino,
                                          child.stat.st_mode,
                                          len(name)))
                    mem.write(name)

    def _write_file_data(self, mem: mmap.mmap, offset: int,
                         entry: FileEntry) -> None:
        try:
            with open(self.src_dir / entry.path, 'rb') as f:
                mem.seek(offset)
                shutil.copyfileobj(f, mem)
        except (PermissionError, FileNotFoundError, IsADirectoryError):
            pass

    def _write_overlay(self, mem: mmap.mmap, overlay_offset: int,
                       bucket_array: int) -> None:
        mem.seek(overlay_offset)
        mem.write(struct.pack(
            OVERLAY_HEADER_FORMAT,
            DAXFS_OVERLAY_MAGIC,
            DAXFS_OVERLAY_VERSION,
            DAXFS_BLOCK_SIZE,                   # bucket_offset
            DAXFS_BLOCK_SIZE + bucket_array,    # pool_offset
            self.overlay_pool_size,
            0,                                  # pool_alloc
            self.next_ino,
            DAXFS_OVL_FREE_END,                 # free_inode
            DAXFS_OVL_FREE_END,                 # free_data
            DAXFS_OVL_FREE_END,                 # free_dirent
        ))


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


def _daxfs_iomem_regions() -> set:
    """Return the set of (phys_addr, size) daxfs regions in /proc/iomem."""
    regions = set()
    try:
        with open('/proc/iomem', 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(' : ')
                if len(parts) == 2 and parts[1] == 'daxfs':
                    start_str, end_str = parts[0].strip().split('-')
                    start = int(start_str, 16)
                    end = int(end_str, 16)
                    regions.add((start, end - start + 1))
    except (FileNotFoundError, PermissionError, ValueError):
        pass
    return regions


def _umount_all(path) -> None:
    """Unmount any mounts stacked on path (no-op if not mounted)."""
    libc = _get_libc()
    path_bytes = str(path).encode('utf-8')
    while os.path.ismount(path):
        if libc.umount2(ctypes.c_char_p(path_bytes), 0) < 0:
            errno_val = ctypes.get_errno()
            raise DaxfsError(
                f"Failed to unmount {path}: {os.strerror(errno_val)}"
            )


def unmount_daxfs(instance_name: str) -> None:
    """Unmount an instance's daxfs image and remove its mountpoint.

    Unmounting drops the filesystem's dma-buf reference, freeing the
    image memory back to the multikernel pool.
    """
    mnt_dir = Path(KERF_DAXFS_MNT_DIR) / instance_name
    _umount_all(mnt_dir)
    try:
        mnt_dir.rmdir()
    except OSError:
        pass


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
        size: Size to allocate (if None, calculated automatically)

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
        size = required_size

    if required_size > size:
        raise DaxfsError(
            f"Required size {required_size} exceeds allocated size {size}"
        )

    # Unmount any previous image for this instance first, releasing its
    # memory back to the pool before the new allocation.
    _umount_all(Path(KERF_DAXFS_MNT_DIR) / instance_name)

    # The heap allocation registers a 'daxfs' iomem region; snapshot
    # existing regions (other instances) so ours can be identified after.
    regions_before = _daxfs_iomem_regions()

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

    new_regions = _daxfs_iomem_regions() - regions_before
    if len(new_regions) != 1:
        _umount_all(Path(KERF_DAXFS_MNT_DIR) / instance_name)
        raise DaxfsError(
            "Could not identify this image's 'daxfs' region in /proc/iomem "
            f"(found {len(new_regions)} new entries)"
        )
    phys_addr, actual_size = new_regions.pop()

    return DaxfsImage(
        phys_addr=phys_addr,
        size=actual_size,
    )
