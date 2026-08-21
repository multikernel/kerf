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
Tests for `kerf show` baseline hardware display.
"""

from kerf.models import (
    CPUAllocation,
    GlobalDeviceTree,
    HardwareInventory,
    MemoryAllocation,
    PoolMemoryRegion,
)
from kerf.show.main import display_baseline_info


def _tree(cpus, memory):
    hardware = HardwareInventory(cpus=cpus, memory=memory, devices={})
    return GlobalDeviceTree(hardware=hardware, instances={}, device_references={})


def _cpus(available_free=None):
    return CPUAllocation(
        total=32,
        host_reserved=[0, 1, 2, 3],
        available=list(range(4, 32)),
        available_free=available_free,
    )


def test_lists_every_pool_chunk_with_its_node(capsys, monkeypatch):
    memory = MemoryAllocation(
        total_bytes=16 * 1024**3,
        host_reserved_bytes=2 * 1024**3,
        regions=[
            PoolMemoryRegion(base=0x100000000, size=4 * 1024**3, node=0),
            PoolMemoryRegion(base=0x200000000, size=6 * 1024**3, node=1),
            PoolMemoryRegion(base=0x300000000, size=1 * 1024**3, node=-1),
        ],
    )
    monkeypatch.setattr(
        "kerf.show.main.get_pool_allocated_bytes",
        lambda: (0x100000000, 11 * 1024**3, 3 * 1024**3),
    )

    display_baseline_info(_tree(_cpus(), memory))
    out = capsys.readouterr().out

    assert "Chunk:           0x100000000  4.00 GB node 0" in out
    assert "Chunk:           0x200000000  6.00 GB node 1" in out
    assert "Chunk:           0x300000000  1.00 GB" in out
    assert "0x300000000  1.00 GB node" not in out
    assert "Pool Allocated:  3.00 GB" in out
    assert "Pool Available:  8.00 GB" in out


def test_no_allocation_summary_without_iomem(capsys, monkeypatch):
    memory = MemoryAllocation(
        total_bytes=0,
        host_reserved_bytes=0,
        regions=[PoolMemoryRegion(base=0x100000000, size=4 * 1024**3, node=0)],
    )
    monkeypatch.setattr("kerf.show.main.get_pool_allocated_bytes", lambda: None)

    display_baseline_info(_tree(_cpus(), memory))
    out = capsys.readouterr().out

    assert "Pool Allocated" not in out
    assert "Pool Available" not in out


def test_memory_total_and_host_reserved_hidden_when_zero(capsys, monkeypatch):
    memory = MemoryAllocation(
        total_bytes=0,
        host_reserved_bytes=0,
        regions=[PoolMemoryRegion(base=0x100000000, size=4 * 1024**3, node=0)],
    )
    monkeypatch.setattr("kerf.show.main.get_pool_allocated_bytes", lambda: None)

    display_baseline_info(_tree(_cpus(), memory))
    out = capsys.readouterr().out

    # The CPU section always prints its own Total/Host Reserved; only the
    # memory ones (GB-suffixed) are suppressed when the value is 0.
    assert "GB) " not in out
    for line in out.splitlines():
        assert not line.strip().startswith("Total:") or "GB" not in line
        assert not line.strip().startswith("Host Reserved:") or "GB" not in line


def test_memory_total_and_host_reserved_shown_when_nonzero(capsys, monkeypatch):
    memory = MemoryAllocation(
        total_bytes=16 * 1024**3,
        host_reserved_bytes=2 * 1024**3,
        regions=[PoolMemoryRegion(base=0x100000000, size=4 * 1024**3, node=0)],
    )
    monkeypatch.setattr("kerf.show.main.get_pool_allocated_bytes", lambda: None)

    display_baseline_info(_tree(_cpus(), memory))
    out = capsys.readouterr().out

    assert "Total:           16.00 GB" in out
    assert "Host Reserved:   2.00 GB" in out


def test_cpu_pool_and_available_split_when_free_subset_known(capsys, monkeypatch):
    memory = MemoryAllocation(total_bytes=0, host_reserved_bytes=0, regions=[])
    monkeypatch.setattr("kerf.show.main.get_pool_allocated_bytes", lambda: None)

    display_baseline_info(_tree(_cpus(available_free=list(range(4, 20))), memory))
    out = capsys.readouterr().out

    assert "Pool CPUs:       28 cpus" in out
    assert "Available CPUs:  16 cpus" in out


def test_cpu_available_line_kept_when_free_subset_unknown(capsys, monkeypatch):
    memory = MemoryAllocation(total_bytes=0, host_reserved_bytes=0, regions=[])
    monkeypatch.setattr("kerf.show.main.get_pool_allocated_bytes", lambda: None)

    display_baseline_info(_tree(_cpus(available_free=None), memory))
    out = capsys.readouterr().out

    assert "Available:       28 cpus" in out
    assert "Pool CPUs" not in out
    assert "Available CPUs" not in out
