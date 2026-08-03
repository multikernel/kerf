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
Client for the lazy_cma runtime contiguous memory allocator.

kerf allocates the multikernel memory pool at runtime through
/dev/lazy_cma instead of requiring a boot-time reservation. Allocating
under the canonical resource name ("Multikernel Memory Pool") makes the
pool discoverable in /proc/iomem, which is where kerf and the kernel
tooling look it up.
"""

import fcntl
import os
import struct

from .exceptions import KernelInterfaceError

LAZY_CMA_DEVICE = "/dev/lazy_cma"

#: iomem resource name the multikernel stack discovers the pool by.
MULTIKERNEL_POOL_NAME = "Multikernel Memory Pool"

_LAZY_CMA_IOC_MAGIC = ord("H")
_LAZY_CMA_NAME_MAX = 64

# struct lazy_cma_allocation_data {
#     __u64 len;
#     __u64 phys_addr;   /* out */
#     __s32 node;        /* NUMA node; -1 = any */
#     __u32 pad;
#     char name[64];     /* iomem resource name */
# };
_ALLOC_FORMAT = f"<QQiI{_LAZY_CMA_NAME_MAX}s"
_ALLOC_SIZE = struct.calcsize(_ALLOC_FORMAT)

_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, nr: int, size: int) -> int:
    """Encode an ioctl request number (matches the kernel's _IOC macro)."""
    return (direction << 30) | (size << 16) | (_LAZY_CMA_IOC_MAGIC << 8) | nr


LAZY_CMA_IOCTL_ALLOC = _ioc(_IOC_READ | _IOC_WRITE, 0x0, _ALLOC_SIZE)


def allocate(size_bytes: int, name: str, node: int = -1) -> int:
    """
    Allocate physically contiguous memory via /dev/lazy_cma.

    Args:
        size_bytes: Allocation size in bytes (page granularity in the kernel)
        name: iomem resource name for the allocation
        node: NUMA node to allocate from, or -1 for any node

    Returns:
        Physical base address of the allocation

    Raises:
        KernelInterfaceError: If the device is unavailable or allocation fails
        ValueError: If the arguments are invalid
    """
    if size_bytes <= 0:
        raise ValueError(f"Allocation size must be positive, got {size_bytes}")

    encoded_name = name.encode("utf-8")
    if len(encoded_name) >= _LAZY_CMA_NAME_MAX:
        raise ValueError(
            f"Resource name too long ({len(encoded_name)} bytes, "
            f"max {_LAZY_CMA_NAME_MAX - 1})"
        )

    request = bytearray(
        struct.pack(_ALLOC_FORMAT, size_bytes, 0, node, 0, encoded_name)
    )

    try:
        fd = os.open(LAZY_CMA_DEVICE, os.O_RDWR)
    except FileNotFoundError as exc:
        raise KernelInterfaceError(
            f"{LAZY_CMA_DEVICE} not found. Load the lazy_cma kernel module "
            "first (e.g. insmod lazy_cma.ko)."
        ) from exc
    except PermissionError as exc:
        raise KernelInterfaceError(
            f"Permission denied opening {LAZY_CMA_DEVICE}. kerf must run "
            "as root to allocate pool memory."
        ) from exc

    try:
        fcntl.ioctl(fd, LAZY_CMA_IOCTL_ALLOC, request)
    except OSError as exc:
        raise KernelInterfaceError(
            f"lazy_cma allocation of {size_bytes} bytes failed: "
            f"{os.strerror(exc.errno)} (errno {exc.errno}). "
            "The system may not have enough contiguous free memory; "
            "try a smaller pool size."
        ) from exc
    finally:
        os.close(fd)

    _, phys_addr, _, _, _ = struct.unpack(_ALLOC_FORMAT, bytes(request))
    if phys_addr == 0:
        raise KernelInterfaceError(
            f"lazy_cma returned physical address 0 for a {size_bytes} byte "
            "allocation"
        )

    return phys_addr


def allocate_multikernel_pool(size_bytes: int, node: int = -1) -> int:
    """
    Allocate the multikernel memory pool under its canonical iomem name.

    Returns the physical base address of the pool.
    """
    return allocate(size_bytes, MULTIKERNEL_POOL_NAME, node)
