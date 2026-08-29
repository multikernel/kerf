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

"""Pool devices described under their host bridge in the PCI bus binding."""

import struct

import libfdt

from kerf.dtc.parser import DeviceTreeParser


def _phys_hi(bus, slot, func):
    return bus << 16 | slot << 11 | func << 8


def _pci_tree(sw, domain=0):
    """A root bus with a root port leading to bus 9, where the NIC sits."""
    sw.begin_node("pci@0")
    sw.property_string("compatible", "multikernel,pci-host-bridge")
    sw.property_string("device_type", "pci")
    sw.property_u32("#address-cells", 3)
    sw.property_u32("#size-cells", 2)
    sw.property_u32("linux,pci-domain", domain)
    sw.property("bus-range", struct.pack(">II", 0, 0xFF))
    sw.begin_node("pci@3,0")
    sw.property_string("device_type", "pci")
    sw.property_u32("#address-cells", 3)
    sw.property_u32("#size-cells", 2)
    sw.property("reg", struct.pack(">5I", _phys_hi(0, 3, 0), 0, 0, 0, 0))
    sw.property("bus-range", struct.pack(">II", 9, 9))
    sw.begin_node("pci@0,0")
    sw.property("reg", struct.pack(">5I", _phys_hi(9, 0, 0), 0, 0, 0, 0))
    sw.property_u32("vendor-id", 0x1AF4)
    sw.property_u32("device-id", 0x1041)
    sw.end_node()
    sw.end_node()
    sw.begin_node("pci@1f,2")
    sw.property("reg", struct.pack(">5I", _phys_hi(0, 0x1F, 2), 0, 0, 0, 0))
    sw.property_u32("vendor-id", 0x8086)
    sw.property_u32("device-id", 0x2922)
    sw.end_node()
    sw.end_node()


def _finish(sw):
    fdt = sw.as_fdt()
    fdt.pack()
    return bytes(fdt.as_bytearray())


def _pool_dtb():
    sw = libfdt.FdtSw()
    sw.finish_reservemap()
    sw.begin_node("")
    sw.property_string("compatible", "multikernel-v1")
    sw.property_u32("id", 0)
    sw.begin_node("resources")
    sw.property("cpus", struct.pack(">2Q", 1, 2))
    sw.property("cpus-available", struct.pack(">2Q", 1, 2))
    sw.begin_node("memory@59a00000")
    sw.property_string("device_type", "memory")
    sw.property("reg", struct.pack(">QQ", 0x59A00000, 0x20000000))
    sw.property_u32("numa-node-id", 0)
    sw.end_node()
    sw.end_node()
    sw.begin_node("aliases")
    sw.property_string("enp9s0", "/pci@0/pci@3,0/pci@0,0")
    sw.end_node()
    _pci_tree(sw)
    sw.end_node()
    return _finish(sw)


def _instance_dtb():
    sw = libfdt.FdtSw()
    sw.finish_reservemap()
    sw.begin_node("web")
    sw.property_string("compatible", "multikernel-v1")
    sw.property_u32("id", 2)
    sw.begin_node("resources")
    sw.property_u64("memory-base", 0x59A08000)
    sw.property_u64("memory-bytes", 128 << 20)
    sw.property("cpus", struct.pack(">Q", 2))
    sw.end_node()
    sw.begin_node("aliases")
    sw.property_string("enp9s0", "/pci@0/pci@3,0/pci@0,0")
    sw.end_node()
    _pci_tree(sw)
    sw.end_node()
    return _finish(sw)


def test_pool_devices_are_found_under_their_bridges():
    tree = DeviceTreeParser().parse_dtb_from_bytes(_pool_dtb())

    nic = tree.hardware.devices["pci_0000_09_00_0"]
    assert nic.pci_id == "0000:09:00.0"
    assert nic.device_type == "pci"
    assert nic.vendor_id == 0x1AF4
    assert nic.device_id == 0x1041
    assert nic.alias == "enp9s0"

    sata = tree.hardware.devices["pci_0000_00_1f_2"]
    assert sata.pci_id == "0000:00:1f.2"
    assert sata.alias is None
    assert set(tree.hardware.devices) == {"pci_0000_09_00_0", "pci_0000_00_1f_2"}


def test_bridges_are_not_devices():
    tree = DeviceTreeParser().parse_dtb_from_bytes(_pool_dtb())
    assert "pci_0000_00_03_0" not in tree.hardware.devices


def test_instance_devices_are_found_under_their_bridges():
    inst = DeviceTreeParser().parse_instance_dtb_from_bytes(_instance_dtb())
    assert inst.resources.devices == ["pci_0000_00_1f_2", "pci_0000_09_00_0"]
