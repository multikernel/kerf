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

"""Pool transaction overlays and target-path addressing of instance overlays."""

import copy
import struct

import libfdt

from kerf.dtc.overlay import OverlayGenerator
from kerf.models import GlobalDeviceTree, PoolMemoryRegion
from kerf.pool_diff import PoolDiff

GB = 1 << 30
MISSING = -libfdt.FDT_ERR_NOTFOUND


def _ov(dtbo):
    fdt = libfdt.Fdt(dtbo)
    return fdt, fdt.path_offset("/fragment@0/__overlay__")


def _walk(fdt, offset=0):
    yield offset
    child = fdt.first_subnode(offset, quiet=[libfdt.FDT_ERR_NOTFOUND])
    while child >= 0:
        yield from _walk(fdt, child)
        child = fdt.next_subnode(child, quiet=[libfdt.FDT_ERR_NOTFOUND])


def _grown(instance, cpus, memory_bytes, numa_nodes=None):
    new = copy.deepcopy(instance)
    new.resources.cpus = cpus
    new.resources.memory_bytes = memory_bytes
    new.resources.numa_nodes = numa_nodes
    return new


def test_pool_overlay_layout():
    diff = PoolDiff(
        cpus_to_pool=[8, 9], cpus_to_host=[4],
        devices_to_pool=["0000:04:00.0"], devices_to_host=["0000:03:00.0"],
        memory_to_pool=[(1, GB), (-1, GB // 2)],
        memory_to_host=[PoolMemoryRegion(0x2_0000_0000, GB, 0)],
    )
    fdt, ov = _ov(OverlayGenerator().generate_pool_overlay(diff))

    frag = fdt.path_offset("/fragment@0")
    assert fdt.getprop(frag, "target-path").as_str() == "/resources"
    grow = fdt.subnode_offset(ov, "memory-add")
    m0 = fdt.subnode_offset(grow, "memory@0")
    assert fdt.getprop(m0, "size").as_uint64() == GB
    assert fdt.getprop(m0, "numa-node-id").as_uint32() == 1
    m1 = fdt.subnode_offset(grow, "memory@1")
    assert fdt.getprop(m1, "size").as_uint64() == GB // 2
    assert fdt.getprop(m1, "numa-node-id", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING
    # The kernel picks the chunk, so a grow item never names an address.
    assert fdt.getprop(m0, "reg", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING
    assert fdt.getprop(m1, "reg", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING

    shrink = fdt.subnode_offset(ov, "memory-remove")
    reg = bytes(fdt.getprop(fdt.subnode_offset(shrink, "memory@0"), "reg"))
    assert struct.unpack(">QQ", reg) == (0x2_0000_0000, GB)

    assert fdt.getprop(fdt.subnode_offset(fdt.subnode_offset(ov, "cpu-add"), "cpu@8"),
                       "reg").as_uint64() == 8
    assert fdt.getprop(fdt.subnode_offset(fdt.subnode_offset(ov, "cpu-remove"), "cpu@4"),
                       "reg").as_uint64() == 4
    assert fdt.getprop(fdt.subnode_offset(fdt.subnode_offset(ov, "device-add"), "pci@0"),
                       "pci-id").as_str() == "0000:04:00.0"
    assert fdt.getprop(fdt.subnode_offset(fdt.subnode_offset(ov, "device-remove"), "pci@0"),
                       "pci-id").as_str() == "0000:03:00.0"


def test_pool_overlay_omits_empty_ops():
    fdt, ov = _ov(OverlayGenerator().generate_pool_overlay(PoolDiff(cpus_to_pool=[5])))
    assert fdt.subnode_offset(ov, "memory-remove", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING
    assert fdt.subnode_offset(ov, "cpu-add") >= 0


def test_update_overlay_targets_the_instance_path(sample_instances):
    old = sample_instances["database"]
    new = _grown(old, old.resources.cpus + [16], old.resources.memory_bytes + GB)
    fdt, ov = _ov(OverlayGenerator().generate_update_overlay("database", old, new))

    assert fdt.getprop(fdt.path_offset("/fragment@0"), "target-path").as_str() == "/instances/database"
    for offset in _walk(fdt):
        assert fdt.getprop(offset, "mk,instance", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING

    add = fdt.subnode_offset(ov, "memory-add")
    reg = bytes(fdt.getprop(fdt.subnode_offset(add, "memory@0"), "reg"))
    assert struct.unpack(">QQ", reg) == (old.resources.memory_base + old.resources.memory_bytes, GB)
    assert fdt.subnode_offset(add, "region@0", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING


def test_update_overlay_pins_new_resources_to_the_instance_node(sample_instances):
    old = sample_instances["database"]
    new = _grown(old, old.resources.cpus + [16], old.resources.memory_bytes + GB, numa_nodes=[1])
    fdt, ov = _ov(OverlayGenerator().generate_update_overlay("database", old, new))

    cpu = fdt.subnode_offset(fdt.subnode_offset(ov, "cpu-add"), "cpu@16")
    assert fdt.getprop(cpu, "numa-node-id").as_uint32() == 1
    assert fdt.getprop(cpu, "numa-node", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING

    mem = fdt.subnode_offset(fdt.subnode_offset(ov, "memory-add"), "memory@0")
    assert fdt.getprop(mem, "numa-node-id").as_uint32() == 1


def test_update_overlay_shrink_names_memory_items(sample_instances):
    old = sample_instances["database"]
    new = _grown(old, old.resources.cpus[:-1], old.resources.memory_bytes - GB)
    fdt, ov = _ov(OverlayGenerator().generate_update_overlay("database", old, new))

    remove = fdt.subnode_offset(ov, "memory-remove")
    reg = bytes(fdt.getprop(fdt.subnode_offset(remove, "memory@0"), "reg"))
    assert struct.unpack(">QQ", reg) == (
        old.resources.memory_base + new.resources.memory_bytes, GB)


def test_create_overlay_targets_the_instance_namespace(sample_hardware, sample_instances):
    current = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})
    modified = GlobalDeviceTree(
        hardware=sample_hardware,
        instances={"database": sample_instances["database"]},
        device_references={},
    )
    fdt, ov = _ov(OverlayGenerator().generate_overlay(current, modified))

    assert fdt.getprop(fdt.path_offset("/fragment@0"), "target-path").as_str() == "/instances"
    create = fdt.subnode_offset(ov, "instance-create")
    assert fdt.getprop(create, "instance-name").as_str() == "database"
    # The kernel places instance memory itself, so the request names no base.
    resources = fdt.subnode_offset(create, "resources")
    assert fdt.getprop(resources, "memory-base", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING
    assert fdt.getprop(resources, "memory-bytes").as_uint64() == \
        sample_instances["database"].resources.memory_bytes
    for offset in _walk(fdt):
        assert fdt.getprop(offset, "mk,instance", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING


def test_removal_overlay_targets_the_instance_namespace():
    fdt, ov = _ov(OverlayGenerator().generate_removal_overlay("database"))

    assert fdt.getprop(fdt.path_offset("/fragment@0"), "target-path").as_str() == "/instances"
    remove = fdt.subnode_offset(ov, "instance-remove")
    assert fdt.getprop(remove, "instance-name").as_str() == "database"


def _assign(sample_hardware, sample_instances, devices):
    current = GlobalDeviceTree(hardware=sample_hardware, instances={}, device_references={})
    instance = copy.deepcopy(sample_instances["database"])
    instance.resources.devices = devices
    modified = GlobalDeviceTree(
        hardware=sample_hardware, instances={"database": instance}, device_references={})
    return libfdt.Fdt(OverlayGenerator().generate_overlay(current, modified))


def test_create_overlay_assigns_devices_by_pci_id(sample_hardware, sample_instances):
    fdt = _assign(sample_hardware, sample_instances, ["eth0"])

    resources = fdt.path_offset("/fragment@0/__overlay__/instance-create/resources")
    assert fdt.getprop(resources, "device-names", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING

    frag = fdt.path_offset("/fragment@1")
    assert fdt.getprop(frag, "target-path").as_str() == "/instances/database"
    pci = fdt.path_offset("/fragment@1/__overlay__/device-add/pci@0")
    assert fdt.getprop(pci, "pci-id").as_str() == "0000:01:00.0"


def test_create_overlay_without_devices_has_one_fragment(sample_hardware, sample_instances):
    fdt = _assign(sample_hardware, sample_instances, [])
    assert fdt.path_offset("/fragment@1", quiet=[libfdt.FDT_ERR_NOTFOUND]) == MISSING
