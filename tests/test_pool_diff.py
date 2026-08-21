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
Tests for computing the difference between the live pool and a requested baseline.
"""

from kerf.models import (CPUAllocation, GlobalDeviceTree, HardwareInventory,
                         MemoryAllocation, PoolMemoryRegion, DeviceInfo)
from kerf.pool_diff import compute_pool_diff

GB = 1 << 30


def _tree(cpus, regions=None, requested=None, devices=()):
    hw = HardwareInventory(
        cpus=CPUAllocation(total=16, host_reserved=[0], available=list(cpus)),
        memory=MemoryAllocation(total_bytes=0, host_reserved_bytes=0,
                                regions=list(regions or []), requested=dict(requested or {})),
        devices={d: DeviceInfo(name=d, compatible="pci", pci_id=d) for d in devices},
    )
    return GlobalDeviceTree(hardware=hw, instances={}, device_references={})


def test_cpu_and_device_set_differences():
    cur = _tree([4, 5, 6], devices=["0000:03:00.0"])
    req = _tree([5, 6, 7], devices=["0000:04:00.0"])
    d = compute_pool_diff(cur, req)
    assert d.cpus_to_pool == [7]
    assert d.cpus_to_host == [4]
    assert d.devices_to_pool == ["0000:04:00.0"]
    assert d.devices_to_host == ["0000:03:00.0"]


def test_memory_grow_per_node():
    cur = _tree([4], regions=[PoolMemoryRegion(0x1_0000_0000, GB, 0)])
    req = _tree([4], requested={0: 2 * GB, 1: GB})
    d = compute_pool_diff(cur, req)
    assert sorted(d.memory_to_pool) == [(0, GB), (1, GB)]
    assert d.memory_to_host == []


def test_memory_shrink_prefers_idle_chunks():
    a = PoolMemoryRegion(0x1_0000_0000, GB, 0)
    b = PoolMemoryRegion(0x2_0000_0000, GB, 0)
    cur = _tree([4], regions=[a, b])
    req = _tree([4], requested={0: GB})
    d = compute_pool_diff(cur, req, busy_chunks={a.base})
    assert d.memory_to_host == [b]
    assert d.memory_to_pool == []


def test_any_node_request_matches_total():
    cur = _tree([4], regions=[PoolMemoryRegion(0x1_0000_0000, GB, 0), PoolMemoryRegion(0x2_0000_0000, GB, 1)])
    req = _tree([4], requested={-1: 2 * GB})
    assert compute_pool_diff(cur, req).is_empty()


def test_identical_state_is_empty():
    cur = _tree([4, 5], regions=[PoolMemoryRegion(0x1_0000_0000, GB, 0)])
    req = _tree([4, 5], requested={0: GB})
    assert compute_pool_diff(cur, req).is_empty()
