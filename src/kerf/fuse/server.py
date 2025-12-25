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
Multikernel FUSE server implementation.

A passthrough FUSE server for multikernel filesystem sharing over vsock.
"""

import errno
import os
import socket
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

AF_VSOCK = 40
VMADDR_CID_ANY = -1
SO_VM_SOCKETS_TRANSPORT = 9
VSOCK_TRANSPORT_MULTIKERNEL = 1

FUSE_KERNEL_VERSION = 7
FUSE_KERNEL_MINOR_VERSION = 40
FUSE_ROOT_ID = 1

FUSE_LOOKUP = 1
FUSE_FORGET = 2
FUSE_GETATTR = 3
FUSE_SETATTR = 4
FUSE_READLINK = 5
FUSE_SYMLINK = 6
FUSE_MKNOD = 8
FUSE_MKDIR = 9
FUSE_UNLINK = 10
FUSE_RMDIR = 11
FUSE_RENAME = 12
FUSE_LINK = 13
FUSE_OPEN = 14
FUSE_READ = 15
FUSE_WRITE = 16
FUSE_STATFS = 17
FUSE_RELEASE = 18
FUSE_FSYNC = 20
FUSE_SETXATTR = 21
FUSE_GETXATTR = 22
FUSE_LISTXATTR = 23
FUSE_REMOVEXATTR = 24
FUSE_FLUSH = 25
FUSE_INIT = 26
FUSE_OPENDIR = 27
FUSE_READDIR = 28
FUSE_RELEASEDIR = 29
FUSE_FSYNCDIR = 30
FUSE_ACCESS = 34
FUSE_CREATE = 35
FUSE_DESTROY = 38
FUSE_READDIRPLUS = 44
FUSE_SETUPMAPPING = 48
FUSE_REMOVEMAPPING = 49

FUSE_BIG_WRITES = 1 << 5
FUSE_DO_READDIRPLUS = 1 << 13
FUSE_READDIRPLUS_AUTO = 1 << 14
FUSE_WRITEBACK_CACHE = 1 << 16
FUSE_MAP_ALIGNMENT = 1 << 29

FOPEN_KEEP_CACHE = 1 << 1

FATTR_MODE = 1 << 0
FATTR_UID = 1 << 1
FATTR_GID = 1 << 2
FATTR_SIZE = 1 << 3
FATTR_ATIME = 1 << 4
FATTR_MTIME = 1 << 5
FATTR_FH = 1 << 6

CACHE_TIMEOUT = 60

MK_FUSE_MAGIC = 0x4D4B4653
MK_FUSE_VERSION = 1

DEFAULT_PORT = 6789


class FuseServerError(Exception):
    """Exception raised for FUSE server errors."""


class InodeEntry:
    """Tracks inode information."""

    def __init__(self, nodeid: int, parent: int, name: str, path: str):
        self.nodeid = nodeid
        self.parent = parent
        self.name = name
        self.path = path


class FuseServer:
    """FUSE passthrough server for multikernel."""

    def __init__(self, rootfs_path: str, port: int = DEFAULT_PORT):
        self.rootfs_path = rootfs_path
        self.port = port
        self.running = True
        self.client_fd: Optional[socket.socket] = None
        self.listen_fd: Optional[socket.socket] = None

        self.inodes: Dict[int, InodeEntry] = {}
        self.next_nodeid = FUSE_ROOT_ID + 1

        self.file_handles: Dict[int, int] = {}
        self.dir_handles: Dict[int, Tuple[str, List]] = {}
        self.next_fh = 1

    def inode_path(self, nodeid: int) -> Optional[str]:
        """Get filesystem path for a nodeid."""
        if nodeid == FUSE_ROOT_ID:
            return self.rootfs_path
        entry = self.inodes.get(nodeid)
        return entry.path if entry else None

    def create_inode(self, parent: int, name: str, path: str) -> InodeEntry:
        """Create a new inode entry."""
        entry = InodeEntry(self.next_nodeid, parent, name, path)
        self.inodes[self.next_nodeid] = entry
        self.next_nodeid += 1
        return entry

    def create_fh(self, fd: int) -> int:
        """Create a file handle for an open file descriptor."""
        handle = self.next_fh
        self.next_fh += 1
        self.file_handles[handle] = fd
        return handle

    def create_dh(self, path: str, entries: List) -> int:
        """Create a directory handle."""
        handle = self.next_fh
        self.next_fh += 1
        self.dir_handles[handle] = (path, entries)
        return handle

    @staticmethod
    def stat_to_fuse_attr(stat_result: os.stat_result, nodeid: int) -> bytes:
        """Convert stat result to FUSE attr structure."""
        return struct.pack(
            '<QQQQQQIIIIIIIIiI',
            nodeid,
            stat_result.st_size,
            stat_result.st_blocks,
            int(stat_result.st_atime),
            int(stat_result.st_mtime),
            int(stat_result.st_ctime),
            0, 0, 0,
            stat_result.st_mode,
            stat_result.st_nlink,
            stat_result.st_uid,
            stat_result.st_gid,
            stat_result.st_rdev,
            stat_result.st_blksize,
            0,
        )

    def recv_all(self, size: int) -> bytes:
        """Receive exactly size bytes from client."""
        data = b''
        while len(data) < size:
            chunk = self.client_fd.recv(size - len(data))
            if not chunk:
                raise ConnectionError("Client disconnected")
            data += chunk
        return data

    def send_all(self, data: bytes) -> None:
        """Send all data to client."""
        self.client_fd.sendall(data)

    def send_reply(self, unique: int, error: int, data: bytes = b'') -> None:
        """Send FUSE reply message."""
        if error:
            out_len = 16
            header = struct.pack('<IiQ', out_len, -error, unique)
            self.send_all(header)
        else:
            out_len = 16 + len(data)
            header = struct.pack('<IiQ', out_len, 0, unique)
            self.send_all(header + data)

    def send_error(self, unique: int, error: int) -> None:
        """Send error reply."""
        self.send_reply(unique, error)

    def handle_init(self, unique: int, body: bytes) -> None:
        """Handle FUSE_INIT request."""
        _major, _minor, _max_readahead, _flags = struct.unpack('<IIII', body[:16])

        out = struct.pack(
            '<IIIIHHIIHHII24x',
            FUSE_KERNEL_VERSION,
            FUSE_KERNEL_MINOR_VERSION,
            128 * 1024,
            FUSE_BIG_WRITES | FUSE_WRITEBACK_CACHE | FUSE_DO_READDIRPLUS | FUSE_READDIRPLUS_AUTO,
            16,
            12,
            128 * 1024,
            1,
            32,
            0,
            0,
            0,
        )
        self.send_reply(unique, 0, out)

    def handle_getattr(self, unique: int, nodeid: int, _body: bytes) -> None:
        """Handle FUSE_GETATTR request."""
        path = self.inode_path(nodeid)
        if not path:
            self.send_error(unique, errno.ENOENT)
            return

        try:
            stat_result = os.lstat(path)
            attr = self.stat_to_fuse_attr(stat_result, nodeid)
            out = struct.pack('<QI4x', CACHE_TIMEOUT, 0) + attr
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_lookup(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_LOOKUP request."""
        parent_path = self.inode_path(nodeid)
        if not parent_path:
            self.send_error(unique, errno.ENOENT)
            return

        name = body.rstrip(b'\x00').decode('utf-8')
        path = os.path.join(parent_path, name)

        try:
            stat_result = os.lstat(path)
            entry = self.create_inode(nodeid, name, path)
            attr = self.stat_to_fuse_attr(stat_result, entry.nodeid)
            out = struct.pack('<QQQQII', entry.nodeid, 1, CACHE_TIMEOUT, CACHE_TIMEOUT, 0, 0) + attr
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_readlink(self, unique: int, nodeid: int, _body: bytes) -> None:
        """Handle FUSE_READLINK request."""
        path = self.inode_path(nodeid)
        if not path:
            self.send_error(unique, errno.ENOENT)
            return

        try:
            target = os.readlink(path)
            self.send_reply(unique, 0, target.encode('utf-8'))
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_open(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_OPEN request."""
        path = self.inode_path(nodeid)
        if not path:
            self.send_error(unique, errno.ENOENT)
            return

        flags, = struct.unpack('<I', body[:4])

        try:
            fd = os.open(path, flags & ~os.O_CREAT)
            handle = self.create_fh(fd)
            out = struct.pack('<QIi', handle, FOPEN_KEEP_CACHE, -1)
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_release(self, unique: int, _nodeid: int, body: bytes) -> None:
        """Handle FUSE_RELEASE request."""
        handle, = struct.unpack('<Q', body[:8])

        fd = self.file_handles.pop(handle, None)
        if fd is not None:
            os.close(fd)

        self.send_reply(unique, 0)

    def handle_read(self, unique: int, _nodeid: int, body: bytes) -> None:
        """Handle FUSE_READ request."""
        handle, offset, size = struct.unpack('<QQI', body[:20])

        fd = self.file_handles.get(handle)
        if fd is None:
            self.send_error(unique, errno.EBADF)
            return

        try:
            data = os.pread(fd, size, offset)
            self.send_reply(unique, 0, data)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_write(self, unique: int, _nodeid: int, body: bytes) -> None:
        """Handle FUSE_WRITE request."""
        handle, offset, _size = struct.unpack('<QQI', body[:20])
        data = body[40:]

        fd = self.file_handles.get(handle)
        if fd is None:
            self.send_error(unique, errno.EBADF)
            return

        try:
            written = os.pwrite(fd, data, offset)
            out = struct.pack('<I4x', written)
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_opendir(self, unique: int, nodeid: int, _body: bytes) -> None:
        """Handle FUSE_OPENDIR request."""
        path = self.inode_path(nodeid)
        if not path:
            self.send_error(unique, errno.ENOENT)
            return

        try:
            entries = list(os.scandir(path))
            handle = self.create_dh(path, entries)
            out = struct.pack('<QIi', handle, 0, -1)
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_releasedir(self, unique: int, _nodeid: int, body: bytes) -> None:
        """Handle FUSE_RELEASEDIR request."""
        handle, = struct.unpack('<Q', body[:8])
        self.dir_handles.pop(handle, None)
        self.send_reply(unique, 0)

    def handle_readdir(self, unique: int, _nodeid: int, body: bytes) -> None:
        """Handle FUSE_READDIR request."""
        handle, offset, size = struct.unpack('<QQI', body[:20])

        dir_handle = self.dir_handles.get(handle)
        if not dir_handle:
            self.send_error(unique, errno.EBADF)
            return

        _path, entries = dir_handle
        result = b''
        idx = int(offset)

        while idx < len(entries):
            entry = entries[idx]
            name = entry.name.encode('utf-8')
            namelen = len(name)
            entsize = ((24 + namelen + 7) // 8) * 8

            if len(result) + entsize > size:
                break

            dirent = struct.pack('<QQII', entry.inode(), idx + 1, namelen, entry.stat().st_mode >> 12)
            dirent += name
            dirent += b'\x00' * (entsize - len(dirent))
            result += dirent
            idx += 1

        self.send_reply(unique, 0, result)

    def handle_readdirplus(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_READDIRPLUS request."""
        handle, offset, size = struct.unpack('<QQI', body[:20])

        dir_handle = self.dir_handles.get(handle)
        if not dir_handle:
            self.send_error(unique, errno.EBADF)
            return

        dir_path, entries = dir_handle
        result = b''
        idx = int(offset)

        while idx < len(entries):
            entry = entries[idx]
            name = entry.name.encode('utf-8')
            namelen = len(name)
            # fuse_entry_out (128) + fuse_dirent header (24) + name, aligned to 8 bytes
            entsize = ((152 + namelen + 7) // 8) * 8

            if len(result) + entsize > size:
                break

            try:
                stat_result = entry.stat(follow_symlinks=False)
                filepath = os.path.join(dir_path, entry.name)
                inode = self.create_inode(nodeid, entry.name, filepath)

                # fuse_entry_out: nodeid, generation, entry_valid, attr_valid, entry_valid_nsec, attr_valid_nsec + attr
                attr = self.stat_to_fuse_attr(stat_result, inode.nodeid)
                entry_out = struct.pack('<QQQQII', inode.nodeid, 1, CACHE_TIMEOUT, CACHE_TIMEOUT, 0, 0) + attr

                # fuse_dirent: ino, off, namelen, type + name
                dirent = struct.pack('<QQII', inode.nodeid, idx + 1, namelen, stat_result.st_mode >> 12)
                dirent += name

                padding = entsize - len(entry_out) - len(dirent)
                result += entry_out + dirent + (b'\x00' * padding)
            except OSError:
                pass

            idx += 1

        self.send_reply(unique, 0, result)

    def handle_create(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_CREATE request."""
        flags, mode, _umask = struct.unpack('<III', body[:12])
        name = body[16:].rstrip(b'\x00').decode('utf-8')

        parent_path = self.inode_path(nodeid)
        if not parent_path:
            self.send_error(unique, errno.ENOENT)
            return

        filepath = os.path.join(parent_path, name)

        try:
            fd = os.open(filepath, flags | os.O_CREAT, mode)
            stat_result = os.fstat(fd)
            inode = self.create_inode(nodeid, name, filepath)
            handle = self.create_fh(fd)

            attr = self.stat_to_fuse_attr(stat_result, inode.nodeid)
            entry_out = struct.pack('<QQQQII', inode.nodeid, 1, CACHE_TIMEOUT, CACHE_TIMEOUT, 0, 0) + attr
            open_out = struct.pack('<QIi', handle, FOPEN_KEEP_CACHE, -1)
            self.send_reply(unique, 0, entry_out + open_out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_mkdir(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_MKDIR request."""
        mode, = struct.unpack('<I', body[:4])
        name = body[8:].rstrip(b'\x00').decode('utf-8')

        parent_path = self.inode_path(nodeid)
        if not parent_path:
            self.send_error(unique, errno.ENOENT)
            return

        path = os.path.join(parent_path, name)

        try:
            os.mkdir(path, mode)
            stat_result = os.lstat(path)
            inode = self.create_inode(nodeid, name, path)
            attr = self.stat_to_fuse_attr(stat_result, inode.nodeid)
            out = struct.pack('<QQQQII', inode.nodeid, 1, CACHE_TIMEOUT, CACHE_TIMEOUT, 0, 0) + attr
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_unlink(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_UNLINK request."""
        name = body.rstrip(b'\x00').decode('utf-8')
        parent_path = self.inode_path(nodeid)
        if not parent_path:
            self.send_error(unique, errno.ENOENT)
            return

        path = os.path.join(parent_path, name)

        try:
            os.unlink(path)
            self.send_reply(unique, 0)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_rmdir(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_RMDIR request."""
        name = body.rstrip(b'\x00').decode('utf-8')
        parent_path = self.inode_path(nodeid)
        if not parent_path:
            self.send_error(unique, errno.ENOENT)
            return

        path = os.path.join(parent_path, name)

        try:
            os.rmdir(path)
            self.send_reply(unique, 0)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_access(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_ACCESS request."""
        mask, = struct.unpack('<I', body[:4])
        path = self.inode_path(nodeid)
        if not path:
            self.send_error(unique, errno.ENOENT)
            return

        try:
            os.access(path, mask)
            self.send_reply(unique, 0)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_setattr(self, unique: int, nodeid: int, body: bytes) -> None:
        """Handle FUSE_SETATTR request."""
        valid, = struct.unpack('<I', body[:4])
        path = self.inode_path(nodeid)
        if not path:
            self.send_error(unique, errno.ENOENT)
            return

        try:
            handle, size, _lock_owner, _atime, _mtime = struct.unpack('<QQQQQ', body[8:48])
            mode, _, uid, gid = struct.unpack('<IIII', body[56:72])

            if valid & FATTR_MODE:
                os.chmod(path, mode)

            if valid & (FATTR_UID | FATTR_GID):
                new_uid = uid if valid & FATTR_UID else -1
                new_gid = gid if valid & FATTR_GID else -1
                os.lchown(path, new_uid, new_gid)

            if valid & FATTR_SIZE:
                fd = self.file_handles.get(handle) if valid & FATTR_FH else None
                if fd is not None:
                    os.ftruncate(fd, size)
                else:
                    os.truncate(path, size)

            stat_result = os.lstat(path)
            attr = self.stat_to_fuse_attr(stat_result, nodeid)
            out = struct.pack('<QI4x', CACHE_TIMEOUT, 0) + attr
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def handle_statfs(self, unique: int, _nodeid: int, _body: bytes) -> None:
        """Handle FUSE_STATFS request."""
        try:
            statvfs_result = os.statvfs(self.rootfs_path)
            out = struct.pack(
                '<QQQQQIIII24x',
                statvfs_result.f_blocks,
                statvfs_result.f_bfree,
                statvfs_result.f_bavail,
                statvfs_result.f_files,
                statvfs_result.f_ffree,
                statvfs_result.f_bsize,
                statvfs_result.f_namemax,
                statvfs_result.f_frsize,
                0,
            )
            self.send_reply(unique, 0, out)
        except OSError as e:
            self.send_error(unique, e.errno)

    def send_init_message(self) -> None:
        """Send multikernel FUSE init message (40 bytes)."""
        msg = struct.pack(
            '<IIQQIIII',
            MK_FUSE_MAGIC,
            MK_FUSE_VERSION,
            0,  # dax_window_phys
            0,  # dax_window_size
            0,  # flags
            0, 0, 0,  # reserved[3]
        )
        self.send_all(msg)

    def process_request(self) -> bool:
        """Process a single FUSE request. Returns False if connection closed."""
        try:
            header = self.recv_all(40)
        except ConnectionError:
            return False

        length, opcode, unique, nodeid = struct.unpack('<IIQQ', header[:24])

        body = b''
        if length > 40:
            body = self.recv_all(length - 40)

        handlers = {
            FUSE_INIT: lambda: self.handle_init(unique, body),
            FUSE_GETATTR: lambda: self.handle_getattr(unique, nodeid, body),
            FUSE_LOOKUP: lambda: self.handle_lookup(unique, nodeid, body),
            FUSE_READLINK: lambda: self.handle_readlink(unique, nodeid, body),
            FUSE_OPEN: lambda: self.handle_open(unique, nodeid, body),
            FUSE_RELEASE: lambda: self.handle_release(unique, nodeid, body),
            FUSE_READ: lambda: self.handle_read(unique, nodeid, body),
            FUSE_WRITE: lambda: self.handle_write(unique, nodeid, body),
            FUSE_OPENDIR: lambda: self.handle_opendir(unique, nodeid, body),
            FUSE_RELEASEDIR: lambda: self.handle_releasedir(unique, nodeid, body),
            FUSE_READDIR: lambda: self.handle_readdir(unique, nodeid, body),
            FUSE_READDIRPLUS: lambda: self.handle_readdirplus(unique, nodeid, body),
            FUSE_CREATE: lambda: self.handle_create(unique, nodeid, body),
            FUSE_MKDIR: lambda: self.handle_mkdir(unique, nodeid, body),
            FUSE_UNLINK: lambda: self.handle_unlink(unique, nodeid, body),
            FUSE_RMDIR: lambda: self.handle_rmdir(unique, nodeid, body),
            FUSE_ACCESS: lambda: self.handle_access(unique, nodeid, body),
            FUSE_SETATTR: lambda: self.handle_setattr(unique, nodeid, body),
            FUSE_STATFS: lambda: self.handle_statfs(unique, nodeid, body),
            FUSE_FLUSH: lambda: self.send_reply(unique, 0),
            FUSE_FSYNC: lambda: self.send_reply(unique, 0),
            FUSE_FSYNCDIR: lambda: self.send_reply(unique, 0),
            FUSE_FORGET: lambda: None,
            FUSE_DESTROY: lambda: self._handle_destroy(unique),
        }

        handler = handlers.get(opcode)
        if handler:
            handler()
        else:
            self.send_error(unique, errno.ENOSYS)

        return self.running

    def _handle_destroy(self, unique: int) -> None:
        """Handle FUSE_DESTROY request."""
        self.send_reply(unique, 0)
        self.running = False

    def run(self, log_file=None) -> None:
        """Run the FUSE server."""
        def log(msg):
            if log_file:
                log_file.write(f"{msg}\n")
                log_file.flush()

        self.listen_fd = socket.socket(AF_VSOCK, socket.SOCK_STREAM)
        self.listen_fd.setsockopt(AF_VSOCK, SO_VM_SOCKETS_TRANSPORT, VSOCK_TRANSPORT_MULTIKERNEL)

        self.listen_fd.bind((VMADDR_CID_ANY, self.port))
        self.listen_fd.listen(1)
        log(f"Listening on vsock port {self.port}")

        while self.running:
            try:
                log("Waiting for connection...")
                self.client_fd, addr = self.listen_fd.accept()
                log(f"Client connected from {addr}")

                self.send_init_message()
                log("Sent init message")

                while self.running:
                    if not self.process_request():
                        break

                self.client_fd.close()
                self.client_fd = None
                self.running = True

            except KeyboardInterrupt:
                break
            except Exception as e:
                log(f"Exception: {e}")

    def close(self) -> None:
        """Close all sockets."""
        if self.client_fd:
            self.client_fd.close()
        if self.listen_fd:
            self.listen_fd.close()


def _run_fuse_server(rootfs_path: str, port: int) -> None:
    """Run FUSE server (called after daemonization)."""
    import traceback
    log_path = "/var/lib/kerf/fuse-server.log"
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"Starting FUSE server: rootfs={rootfs_path}, port={port}\n")
            log.flush()
            try:
                server = FuseServer(rootfs_path, port)
                log.write("FuseServer created, starting run()\n")
                log.flush()
                server.run(log_file=log)
            except Exception as e:
                log.write(f"Error: {e}\n")
                log.write(traceback.format_exc())
            finally:
                if 'server' in locals():
                    server.close()
                log.write("Server stopped\n")
    except Exception:
        pass


def start_fuse_server(
    _instance_id: int,
    rootfs_path: str,
    port: int = DEFAULT_PORT
) -> int:
    """
    Start FUSE server as a fully daemonized background process.

    Uses double-fork to ensure the server survives parent exit.

    Args:
        instance_id: Multikernel instance ID (used for unique port calculation)
        rootfs_path: Path to rootfs directory to export
        port: vsock port to listen on (default: 6789)

    Returns:
        The port number the server is listening on

    Raises:
        FuseServerError: If server startup fails
        FileNotFoundError: If rootfs_path doesn't exist
    """
    rootfs = Path(rootfs_path)
    if not rootfs.is_dir():
        raise FileNotFoundError(f"Rootfs directory not found: {rootfs_path}")

    pid = os.fork()
    if pid > 0:
        os.waitpid(pid, 0)
        return port

    os.setsid()

    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)  # pylint: disable=protected-access

    sys.stdin.close()
    sys.stdout.close()
    sys.stderr.close()

    null_fd = os.open('/dev/null', os.O_RDWR)
    os.dup2(null_fd, 0)
    os.dup2(null_fd, 1)
    os.dup2(null_fd, 2)
    if null_fd > 2:
        os.close(null_fd)

    _run_fuse_server(str(rootfs), port)
    os._exit(0)  # pylint: disable=protected-access


def stop_fuse_server(_instance_id: int) -> None:
    """
    Stop FUSE server for instance.

    Note: With daemonized servers, we can't easily track the PID.
    The server will terminate when the client disconnects.

    Args:
        _instance_id: Multikernel instance ID (unused, for API compatibility)
    """
