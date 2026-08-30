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


def test_unrequested_node_memory_is_surplus():
    a = PoolMemoryRegion(0x1_0000_0000, GB, 0)
    cur = _tree([4], regions=[a])
    req = _tree([4], requested={1: GB})
    d = compute_pool_diff(cur, req)
    assert d.memory_to_pool == [(1, GB)]
    assert d.memory_to_host == [a]


def test_shrink_prefers_larger_idle_chunks():
    small = PoolMemoryRegion(0x1_0000_0000, GB // 2, 0)
    big = PoolMemoryRegion(0x2_0000_0000, GB, 0)
    cur = _tree([4], regions=[small, big])
    req = _tree([4], requested={0: GB // 2})
    d = compute_pool_diff(cur, req)
    assert d.memory_to_host == [big]
    assert d.memory_to_pool == []


def test_busy_chunks_are_never_offered_to_the_host():
    # The only chunk that would fit the surplus holds a running instance.
    idle = PoolMemoryRegion(0x1_0000_0000, GB, 0)
    busy = PoolMemoryRegion(0x2_0000_0000, GB // 2, 0)
    cur = _tree([4], regions=[idle, busy])
    req = _tree([4], requested={0: GB})
    d = compute_pool_diff(cur, req, busy_chunks={busy.base})
    assert d.memory_to_host == []
    assert d.is_empty()


def test_surplus_smaller_than_every_chunk_stays_in_the_pool():
    # Only whole chunks go back to the host, so a 1GB pool asked to shrink to
    # 512MB keeps its chunk and the caller has to be told.
    chunk = PoolMemoryRegion(0x1_0000_0000, GB, 0)
    cur = _tree([4], regions=[chunk])
    req = _tree([4], requested={0: GB // 2})
    d = compute_pool_diff(cur, req)
    assert d.memory_to_host == []
    assert d.memory_to_pool == []
    assert d.is_empty()


def test_device_aliases_follow_devices_joining_the_pool():
    cur = _tree([4], devices=["0000:03:00.0"])
    req = _tree([4], devices=["0000:03:00.0", "0000:04:00.0"])
    req.hardware.devices["0000:03:00.0"].alias = "nvme0"
    req.hardware.devices["0000:04:00.0"].alias = "nvme1"
    d = compute_pool_diff(cur, req)
    assert d.devices_to_pool == ["0000:04:00.0"]
    assert d.device_aliases == {"0000:04:00.0": "nvme1"}


def test_a_device_lent_to_an_instance_is_a_pool_member():
    """The pool tree lists only free devices; a lent one is still the pool's."""
    cur = _tree([4], devices=[])
    req = _tree([4], devices=["0000:09:00.0"])
    d = compute_pool_diff(cur, req, lent_devices={"0000:09:00.0"})
    assert d.devices_to_pool == []
    assert d.is_empty()


def test_a_lent_device_the_request_omits_stays_with_its_instance():
    cur = _tree([4], devices=["0000:03:00.0"])
    req = _tree([4], devices=[])
    d = compute_pool_diff(cur, req, lent_devices={"0000:09:00.0"})
    assert d.devices_to_host == ["0000:03:00.0"]
    assert d.devices_lent == ["0000:09:00.0"]
