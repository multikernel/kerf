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

"""The decision 'kerf init' makes between writing a baseline and applying a diff."""

import contextlib

import libfdt

from kerf.dtc.overlay import OverlayGenerator
from kerf.init import main
from kerf.init.main import reconcile_pool
from kerf.models import (
    CPUAllocation,
    GlobalDeviceTree,
    HardwareInventory,
    MemoryAllocation,
    PoolMemoryRegion,
)

GB = 1 << 30


class FakeManager:
    """Stands in for DeviceTreeManager: records the blob, never touches sysfs."""

    def __init__(self):
        self.overlay_gen = OverlayGenerator()
        self.applied = []

    @contextlib.contextmanager
    def _acquire_lock(self):
        yield

    def apply_dtbo(self, dtbo_data):
        self.applied.append(bytes(dtbo_data))
        return "1"


class FakeBaselineManager:
    def __init__(self, live=None):
        self.written = []
        self.live = live

    def write_baseline(self, tree):
        self.written.append(tree)

    def read_baseline(self):
        return self.live


def _tree(cpus, regions, requested):
    return GlobalDeviceTree(
        hardware=HardwareInventory(
            cpus=CPUAllocation(total=16, host_reserved=[0], available=cpus),
            memory=MemoryAllocation(
                total_bytes=8 * GB,
                host_reserved_bytes=0,
                regions=regions,
                requested=requested,
            ),
            devices={},
        ),
        instances={},
        device_references={},
    )


def test_first_init_writes_the_baseline():
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    requested = _tree([4, 5], [], {-1: 2 * GB})

    assert reconcile_pool(None, requested, set(), False, manager, baseline_mgr) is None
    assert baseline_mgr.written == [requested]
    assert not manager.applied


def test_first_init_dry_run_writes_nothing():
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    empty = _tree([], [], {})

    assert reconcile_pool(empty, _tree([4], [], {-1: GB}), set(), True, manager, baseline_mgr) is None
    assert not baseline_mgr.written
    assert not manager.applied


def test_matching_pool_is_a_no_op():
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    current = _tree([4, 5], [PoolMemoryRegion(0x1_0000_0000, 2 * GB, 0)], {})
    requested = _tree([4, 5], [], {0: 2 * GB})

    diff = reconcile_pool(current, requested, set(), False, manager, baseline_mgr)

    assert diff.is_empty()
    assert not manager.applied
    assert not baseline_mgr.written


def test_dry_run_reports_without_applying():
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    current = _tree([4, 5], [], {})
    requested = _tree([4, 5, 6], [], {0: GB})

    diff = reconcile_pool(current, requested, set(), True, manager, baseline_mgr)

    assert diff.cpus_to_pool == [6]
    assert diff.memory_to_pool == [(0, GB)]
    assert not manager.applied


def test_apply_writes_a_pool_overlay():
    manager = FakeManager()
    current = _tree([4, 5], [PoolMemoryRegion(0x1_0000_0000, GB, 1)], {})
    requested = _tree([4], [], {0: GB})
    baseline_mgr = FakeBaselineManager(live=_tree([4], [PoolMemoryRegion(0x2_0000_0000, GB, 0)], {}))

    diff = reconcile_pool(current, requested, set(), False, manager, baseline_mgr)

    assert diff.cpus_to_host == [5]
    assert len(manager.applied) == 1
    fdt = libfdt.Fdt(manager.applied[0])
    overlay = fdt.path_offset("/fragment@0/__overlay__")
    assert fdt.getprop(fdt.path_offset("/fragment@0"), "target-path").as_str() == "/resources"
    assert fdt.subnode_offset(overlay, "cpu-remove") >= 0
    assert fdt.subnode_offset(overlay, "memory-add") >= 0
    assert fdt.subnode_offset(overlay, "memory-remove") >= 0
    assert not baseline_mgr.written


def test_teardown_returns_everything(monkeypatch):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 1, 4, 5})
    manager, baseline_mgr = FakeManager(), FakeBaselineManager(live=_tree([], [], {}))
    current = _tree([4, 5], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})

    diff = main.reconcile_pool(current, main.build_teardown_tree(), set(), False,
                               manager, baseline_mgr)

    assert diff.cpus_to_host == [4, 5]
    assert [r.base for r in diff.memory_to_host] == [0x1_0000_0000]
    assert diff.memory_to_pool == []
    assert len(manager.applied) == 1
