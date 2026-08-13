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
Per-instance load metadata recorded by kerf load.

The kernel's /proc/kimage table only knows about loaded segments, not the
source image file, so kerf load records the kernel image provenance here
for kerf show to display.
"""

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Optional

from .vmlinuz import ELF_MAGIC, VmlinuzError, is_bzimage, payload_compression

KERF_INSTANCES_DIR = "/var/lib/kerf/instances"

# Setup header field holding a pointer to the version string, less 0x200
BZIMAGE_KERNEL_VERSION = 0x20E


def _metadata_path(name: str) -> Path:
    return Path(KERF_INSTANCES_DIR) / f"{name}.json"


def save_instance_metadata(name: str, metadata: dict) -> None:
    """Persist load metadata for an instance, replacing any previous record."""
    path = _metadata_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_instance_metadata(name: str) -> Optional[dict]:
    """Read load metadata for an instance, or None if absent or unreadable."""
    try:
        return json.loads(_metadata_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def delete_instance_metadata(name: str) -> None:
    _metadata_path(name).unlink(missing_ok=True)


def _bzimage_version(data: bytes) -> Optional[str]:
    ptr = struct.unpack_from("<H", data, BZIMAGE_KERNEL_VERSION)[0]
    if not ptr:
        return None
    start = ptr + 0x200
    end = data.find(b"\x00", start)
    if end <= start:
        return None
    return data[start:end].decode("ascii", errors="replace")


def _vmlinux_version(data: bytes) -> Optional[str]:
    banner = b"Linux version "
    idx = data.find(banner)
    if idx == -1:
        return None
    start = idx + len(banner)
    end = len(data)
    for terminator in (b"\n", b"\x00"):
        pos = data.find(terminator, start)
        if pos != -1:
            end = min(end, pos)
    return data[start:end].decode("ascii", errors="replace") or None


def inspect_kernel_image(path) -> dict:
    """Describe a kernel image file for the instance metadata record."""
    data = Path(path).read_bytes()
    info = {
        "path": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "format": "unknown",
        "compression": None,
        "version": None,
    }
    if is_bzimage(data):
        info["format"] = "bzImage"
        try:
            info["compression"] = payload_compression(data)
        except VmlinuzError:
            pass
        info["version"] = _bzimage_version(data)
    elif data.startswith(ELF_MAGIC):
        info["format"] = "vmlinux"
        info["version"] = _vmlinux_version(data)
    return info
