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
Device tree cell encoding for physical CPU IDs.

The kernel's multikernel core identifies CPUs by their physical ID (APIC
ID on x86, MPIDR on arm64, hartid on riscv), which is a sparse 64-bit
value. All "cpus" properties and cpu@N reg cells therefore use 64-bit
cells; 32-bit cells are still accepted on the parse side for device
trees written before the widening.
"""

import struct
from typing import List, Sequence


def pack_cpu_ids(cpu_ids: Sequence[int]) -> bytes:
    """Encode physical CPU IDs as an array of 64-bit device tree cells."""
    return struct.pack(">" + "Q" * len(cpu_ids), *cpu_ids)


def pack_cpu_id(cpu_id: int) -> bytes:
    """Encode a single physical CPU ID as one 64-bit device tree cell."""
    return struct.pack(">Q", cpu_id)


def unpack_cpu_ids(data: bytes) -> List[int]:
    """Decode a "cpus" property into a list of physical CPU IDs.

    Arrays whose length is a multiple of 8 are decoded as 64-bit cells
    (the current format); otherwise a multiple of 4 is decoded as legacy
    32-bit cells.
    """
    raw = bytes(data)
    if len(raw) % 8 == 0:
        return list(struct.unpack(f">{len(raw) // 8}Q", raw))
    if len(raw) % 4 == 0:
        return list(struct.unpack(f">{len(raw) // 4}I", raw))
    raise ValueError(f"Invalid cpus property length: {len(raw)} bytes")
