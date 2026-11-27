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
The kernel's handle on a multikernel instance.

kexec_file_load() returns a file descriptor for the instance it loads an
image for, and with KEXEC_FILE_MK_OPEN for any instance that exists. The
instance is started, halted and inspected through ioctls on it. Closing the
descriptor does nothing to the instance.

Mirrors include/uapi/linux/kexec.h.
"""

import ctypes
import errno
import fcntl
import os
import platform
import struct
from dataclasses import dataclass
from typing import Optional

KEXEC_FILE_UNLOAD = 0x00000001
KEXEC_FILE_NO_INITRAMFS = 0x00000004
KEXEC_FILE_DEBUG = 0x00000008
KEXEC_FILE_MK_OPEN = 0x00000040
KEXEC_FILE_MK_ID_SHIFT = 16
KEXEC_FILE_MK_ID_MASK = 0x7FFF0000
KEXEC_FILE_MULTIKERNEL = 0x80000000

KEXEC_MK_HALT_FORCE = 0x00000001

KEXEC_MK_STATE_READY = 0
KEXEC_MK_STATE_LOADED = 1
KEXEC_MK_STATE_STARTING = 2
KEXEC_MK_STATE_ACTIVE = 3

# struct kexec_mk_start: flags, timeout_ms, cmdline, cmdline_len, pad
KEXEC_MK_START = struct.Struct("=IIQII")
# struct kexec_mk_halt: flags, timeout_ms
KEXEC_MK_HALT = struct.Struct("=II")
# struct kexec_mk_info: size, state, run, stop_reason, exit_status, flags, pad
KEXEC_MK_INFO = struct.Struct("=IIQIiII")
# struct kexec_mk_cmdline: buf, len, flags
KEXEC_MK_CMDLINE = struct.Struct("=QII")

_IOC_WRITE = 1
_IOC_READ = 2
_IOC_MAGIC = 0xB9


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (_IOC_MAGIC << 8) | number


KEXEC_MK_IOC_START = _ioc(_IOC_WRITE, 1, KEXEC_MK_START.size)
KEXEC_MK_IOC_HALT = _ioc(_IOC_WRITE, 2, KEXEC_MK_HALT.size)
KEXEC_MK_IOC_INFO = _ioc(_IOC_READ | _IOC_WRITE, 3, KEXEC_MK_INFO.size)
KEXEC_MK_IOC_CMDLINE = _ioc(_IOC_READ | _IOC_WRITE, 5, KEXEC_MK_CMDLINE.size)

# x86_64, and the generic table arm64 and riscv64 share
_SYS_KEXEC_FILE_LOAD = {
    "x86_64": 320,
    "amd64": 320,
    "aarch64": 294,
    "arm64": 294,
    "riscv64": 294,
}

# Every architecture a spawn runs on allows at most this much
_MAX_CMDLINE = 4096


def KEXEC_FILE_MK_ID(instance_id: int) -> int:  # pylint: disable=invalid-name
    """The instance id as it goes into the kexec_file_load flags."""
    return (instance_id << KEXEC_FILE_MK_ID_SHIFT) & KEXEC_FILE_MK_ID_MASK


def kexec_file_load(kernel_fd: int, initrd_fd: int, cmdline: Optional[str], flags: int) -> int:
    """
    Call kexec_file_load().

    Returns:
        What the kernel returned: a file descriptor for the instance when an
        image is loaded or an instance opened, 0 for an unload

    Raises:
        OSError: If the syscall fails
    """
    arch = platform.machine().lower()
    if arch not in _SYS_KEXEC_FILE_LOAD:
        raise OSError(errno.ENOSYS, f"kexec_file_load is not known on {arch}")

    syscall_fn = ctypes.CDLL(None, use_errno=True).syscall
    syscall_fn.argtypes = [
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_char_p,
        ctypes.c_ulong,
    ]
    syscall_fn.restype = ctypes.c_long

    # The length counts the terminating NUL
    cmdline_buf = cmdline.encode("utf-8") if cmdline else None
    cmdline_len = len(cmdline_buf) + 1 if cmdline_buf else 0

    result = syscall_fn(
        _SYS_KEXEC_FILE_LOAD[arch], kernel_fd, initrd_fd, cmdline_len, cmdline_buf, flags
    )
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return result


def load_image(
    instance_id: int, kernel_fd: int, initrd_fd: int, cmdline: Optional[str], flags: int = 0
) -> int:
    """Load a kernel image for an instance and return the instance's descriptor."""
    flags |= KEXEC_FILE_MULTIKERNEL | KEXEC_FILE_MK_ID(instance_id)
    if initrd_fd < 0:
        flags |= KEXEC_FILE_NO_INITRAMFS
    return kexec_file_load(kernel_fd, initrd_fd, cmdline, flags)


def unload_image(instance_id: int) -> None:
    """Unload the kernel image of an instance that is not running."""
    flags = KEXEC_FILE_UNLOAD | KEXEC_FILE_MULTIKERNEL | KEXEC_FILE_MK_ID(instance_id)
    kexec_file_load(-1, -1, None, flags)


def open_instance(instance_id: int) -> int:
    """Return a descriptor for an existing instance, with or without an image."""
    flags = KEXEC_FILE_MULTIKERNEL | KEXEC_FILE_MK_OPEN | KEXEC_FILE_MK_ID(instance_id)
    return kexec_file_load(-1, -1, None, flags)


@dataclass
class InstanceInfo:
    """struct kexec_mk_info."""

    state: int
    run: int
    stop_reason: int
    exit_status: int
    flags: int

    @property
    def loaded(self) -> bool:
        """Whether the instance has a kernel image."""
        return self.state != KEXEC_MK_STATE_READY

    @property
    def running(self) -> bool:
        """Whether its kernel was started and has not stopped."""
        return self.state in (KEXEC_MK_STATE_STARTING, KEXEC_MK_STATE_ACTIVE)


def start(fd: int, cmdline: Optional[str] = None, timeout_ms: int = 0) -> None:
    """
    Boot the loaded image.

    Args:
        cmdline: Replaces the load-time command line, for this run only
        timeout_ms: How long to wait for the boot, 0 to not wait
    """
    if cmdline is None:
        fcntl.ioctl(fd, KEXEC_MK_IOC_START, KEXEC_MK_START.pack(0, timeout_ms, 0, 0, 0))
        return

    buf = ctypes.create_string_buffer(cmdline.encode("utf-8"))
    arg = KEXEC_MK_START.pack(0, timeout_ms, ctypes.addressof(buf), len(buf), 0)
    fcntl.ioctl(fd, KEXEC_MK_IOC_START, arg)


def halt(fd: int, force: bool = False, timeout_ms: int = 0) -> None:
    """
    Ask the spawn to shut down, or with force stop its CPUs without asking.

    Args:
        timeout_ms: How long to wait for a graceful shutdown, 0 to only ask.
            ETIMEDOUT leaves the instance running and the request in effect.
    """
    flags = KEXEC_MK_HALT_FORCE if force else 0
    fcntl.ioctl(fd, KEXEC_MK_IOC_HALT, KEXEC_MK_HALT.pack(flags, timeout_ms))


def info(fd: int) -> InstanceInfo:
    """Read the state of the instance."""
    arg = bytearray(KEXEC_MK_INFO.pack(KEXEC_MK_INFO.size, 0, 0, 0, 0, 0, 0))
    fcntl.ioctl(fd, KEXEC_MK_IOC_INFO, arg)
    _, state, run, stop_reason, exit_status, flags, _ = KEXEC_MK_INFO.unpack(arg)
    return InstanceInfo(state, run, stop_reason, exit_status, flags)


def loaded_cmdline(fd: int) -> str:
    """
    Read the command line the image was loaded with.

    Raises:
        OSError: ENOEXEC if the instance has no image
    """
    buf = ctypes.create_string_buffer(_MAX_CMDLINE)
    arg = bytearray(KEXEC_MK_CMDLINE.pack(ctypes.addressof(buf), len(buf), 0))
    fcntl.ioctl(fd, KEXEC_MK_IOC_CMDLINE, arg)
    return buf.value.decode("utf-8", errors="replace")
