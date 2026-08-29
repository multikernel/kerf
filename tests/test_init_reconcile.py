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
import pytest

from kerf.dtc.overlay import OverlayGenerator
from kerf.exceptions import KernelInterfaceError, ParseError, ValidationError
from kerf.init import main
from kerf.init.main import reconcile_pool
from kerf.models import (
    DeviceInfo,
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
    def lock(self):
        yield

    def apply_dtbo(self, dtbo_data):
        self.applied.append(bytes(dtbo_data))
        return "1"


class FakeBaselineManager:
    """Stands in for BaselineManager: records writes, replays one read-back."""

    def __init__(self, live=None, read_error=None):
        self.written = []
        self.live = live
        self.read_error = read_error

    def write_baseline(self, tree):
        self.written.append(tree)

    def read_baseline(self):
        if self.read_error is not None:
            raise self.read_error
        return self.live


def _tree(cpus, regions, requested, available_free=None):
    return GlobalDeviceTree(
        hardware=HardwareInventory(
            cpus=CPUAllocation(total=16, host_reserved=[0], available=cpus,
                               available_free=available_free),
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
    current = _tree([4, 5], [], {}, available_free=[4, 5])
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


def test_an_empty_request_returns_everything(monkeypatch):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 1, 4, 5})
    monkeypatch.setattr(main, "list_instance_names", lambda: [])
    manager, baseline_mgr = FakeManager(), FakeBaselineManager(live=_tree([], [], {}))
    current = _tree([4, 5], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})
    requested = main.build_baseline_from_cmdline("none", memory="none")

    diff = main.reconcile_pool(current, requested, set(), False, manager, baseline_mgr)

    assert diff.cpus_to_host == [4, 5]
    assert [r.base for r in diff.memory_to_host] == [0x1_0000_0000]
    assert diff.memory_to_pool == []
    assert len(manager.applied) == 1


def test_an_empty_request_dry_run_shows_the_releases(monkeypatch, capsys):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 4, 5})
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    current = _tree([4, 5], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})
    requested = main.build_baseline_from_cmdline("none", memory="none")

    main.reconcile_pool(current, requested, set(), True, manager, baseline_mgr)
    out = capsys.readouterr().out

    assert "CPUs to host: 4, 5" in out
    assert "Memory to host: 0x100000000 (1024 MB)" in out
    assert not manager.applied
    assert not baseline_mgr.written


def test_unparseable_read_back_is_a_first_init():
    manager = FakeManager()
    baseline_mgr = FakeBaselineManager(
        read_error=ParseError("No memory description in /resources"))
    requested = _tree([4, 5], [], {-1: GB})

    current = main.read_current_pool(baseline_mgr)

    assert current is None
    assert reconcile_pool(current, requested, set(), False, manager, baseline_mgr) is None
    assert baseline_mgr.written == [requested]
    assert not manager.applied


def test_host_cpu_list_alone_is_not_a_live_pool():
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    # What the kernel publishes before a pool exists: its own online CPUs,
    # no cpus-available, no memory@N.
    current = _tree([0, 1, 2, 3], [], {})
    requested = _tree([4, 5], [], {-1: GB})

    assert not main.pool_is_live(current)
    assert reconcile_pool(current, requested, set(), False, manager, baseline_mgr) is None
    assert baseline_mgr.written == [requested]
    assert not manager.applied


def test_an_empty_request_against_an_empty_pool_writes_nothing(monkeypatch):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 1})
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    requested = main.build_baseline_from_cmdline("none", memory="none")

    assert reconcile_pool(_tree([0, 1], [], {}), requested, set(), False,
                          manager, baseline_mgr) is None
    assert not baseline_mgr.written
    assert not manager.applied


def test_an_empty_request_refuses_while_instances_exist(monkeypatch):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 4})
    monkeypatch.setattr(main, "list_instance_names", lambda: ["db", "web"])
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    current = _tree([4], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})
    requested = main.build_baseline_from_cmdline("none", memory="none")

    with pytest.raises(ValidationError, match="delete instances db, web first"):
        reconcile_pool(current, requested, set(), False, manager, baseline_mgr)
    assert not manager.applied


def test_unshrinkable_surplus_is_reported(capsys):
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    current = _tree([4, 5], [PoolMemoryRegion(0x1_0000_0000, 2 * GB, 0)], {})
    requested = _tree([4, 5], [], {0: GB})

    diff = reconcile_pool(current, requested, set(), False, manager, baseline_mgr)

    assert diff.is_empty()
    assert not manager.applied
    err = capsys.readouterr().err
    assert "node 0 still holds 1024 MB more than requested" in err
    assert "only whole idle chunks can be returned" in err


def test_dry_run_reports_the_surplus_too(capsys):
    manager, baseline_mgr = FakeManager(), FakeBaselineManager()
    current = _tree([4], [PoolMemoryRegion(0x1_0000_0000, 2 * GB, 0)], {})
    requested = _tree([4, 5], [], {0: GB})

    reconcile_pool(current, requested, set(), True, manager, baseline_mgr)

    assert "still holds 1024 MB more than requested" in capsys.readouterr().err


def test_cpu_only_pool_grows_memory():
    # A pool that kept its CPUs but gave back every chunk is live, and the
    # request only has to add the memory back.
    manager = FakeManager()
    current = _tree([1, 2, 3], [], {}, available_free=[1, 2, 3])
    requested = _tree([1, 2, 3], [], {-1: GB // 2})
    baseline_mgr = FakeBaselineManager(
        live=_tree([1, 2, 3], [PoolMemoryRegion(0x1_0000_0000, GB // 2, 0)], {}))

    diff = reconcile_pool(current, requested, set(), False, manager, baseline_mgr)

    assert diff.memory_to_pool == [(-1, GB // 2)]
    assert diff.cpus_to_pool == [] and diff.cpus_to_host == []
    assert diff.memory_to_host == []
    assert len(manager.applied) == 1
    assert not baseline_mgr.written


def test_pool_cpus_stay_valid_apic_ids(monkeypatch):
    # A CPU in the pool is gone from /proc/cpuinfo, but re-init must still name it.
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0})
    live = _tree([1, 2, 3], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})

    tree = main.build_baseline_from_cmdline(
        "1-3", memory="512MB", pool_cpus=main.pool_apic_ids(live))

    assert tree.hardware.cpus.available == [1, 2, 3]
    assert tree.hardware.cpus.host_reserved == [0]
    assert tree.hardware.cpus.total == 4


def test_apic_id_outside_system_and_pool_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0})
    live = _tree([1], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})

    with pytest.raises(ValueError, match=r"Invalid APIC ID\(s\) specified: \[9\]"):
        main.build_baseline_from_cmdline("1,9", memory="512MB",
                                         pool_cpus=main.pool_apic_ids(live))


def test_pool_apic_ids_ignores_a_host_only_read_back():
    assert main.pool_apic_ids(None) == set()
    assert main.pool_apic_ids(_tree([0, 1, 2, 3], [], {})) == set()


def test_an_empty_request_reserves_pool_cpus(monkeypatch):
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0})

    tree = main.build_baseline_from_cmdline("none", memory="none", pool_cpus={1, 2, 3})

    assert tree.hardware.cpus.host_reserved == [0, 1, 2, 3]
    assert not tree.hardware.cpus.available
    assert tree.hardware.cpus.total == 4
    assert not tree.hardware.memory.requested
    assert tree.hardware.memory.total_bytes == 0


def test_an_empty_request_does_not_read_the_pool_back(monkeypatch, capsys):
    # Once the pool is gone /resources has no memory@N, so a read-back
    # always fails; complaining about it makes a clean exit look broken.
    monkeypatch.setattr(main, "get_valid_apic_ids_from_system", lambda: {0, 1})
    monkeypatch.setattr(main, "list_instance_names", lambda: [])
    manager = FakeManager()
    baseline_mgr = FakeBaselineManager(
        read_error=ParseError("No memory description in /resources"))
    current = _tree([1], [PoolMemoryRegion(0x1_0000_0000, GB, 0)], {})
    requested = main.build_baseline_from_cmdline("none", memory="none", pool_cpus={1})

    main.reconcile_pool(current, requested, set(), False, manager, baseline_mgr)

    assert "could not read the pool back" not in capsys.readouterr().err


def _pooled_nic():
    return {"pci_0000_09_00_0": DeviceInfo(
        name="pci_0000_09_00_0", compatible="", device_type="pci",
        pci_id="0000:09:00.0", vendor_id=0x1AF4, device_id=0x1041, alias="enp9s0")}


@pytest.mark.parametrize("ref", ["enp9s0", "0000:09:00.0", "pci_0000_09_00_0"])
def test_a_pooled_device_is_resolved_from_the_pool(monkeypatch, ref):
    """A NIC in the pool no longer exists on the host, so init takes it
    from the live tree instead of failing to detect it."""
    monkeypatch.setattr(main, "detect_device_from_system", lambda name: None)

    requested = main.build_baseline_from_cmdline("1-3", memory="512MB", devices=ref,
                                                 pool_devices=_pooled_nic())

    nic = requested.hardware.devices["pci_0000_09_00_0"]
    assert nic.pci_id == "0000:09:00.0"
    assert nic.alias == "enp9s0"


def test_a_pooled_device_keeps_a_new_alias(monkeypatch):
    monkeypatch.setattr(main, "detect_device_from_system", lambda name: None)

    requested = main.build_baseline_from_cmdline("1-3", memory="512MB", devices="0000:09:00.0=uplink",
                                                 pool_devices=_pooled_nic())

    assert requested.hardware.devices["pci_0000_09_00_0"].alias == "uplink"


def test_an_unknown_device_is_still_an_error(monkeypatch):
    monkeypatch.setattr(main, "detect_device_from_system", lambda name: None)

    with pytest.raises(KernelInterfaceError, match="enp7s0"):
        main.build_baseline_from_cmdline("1-3", memory="512MB", devices="enp7s0",
                                         pool_devices=_pooled_nic())
