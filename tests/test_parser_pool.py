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

"""Tests for parsing pool chunks and per-node memory requests."""

import struct

import libfdt
import pytest

from kerf.dtc.parser import DeviceTreeParser
from kerf.exceptions import ParseError


def _dtb(build):
    sw = libfdt.FdtSw()
    sw.finish_reservemap()
    sw.begin_node("")
    sw.property_string("compatible", "linux,multikernel-host")
    sw.begin_node("resources")
    sw.property("cpus", struct.pack(">QQ", 4, 5))
    build(sw)
    sw.end_node()
    sw.end_node()
    fdt = sw.as_fdt()
    fdt.pack()
    return bytes(fdt.as_bytearray())


def test_parse_readback_regions():
    def build(sw):
        sw.begin_node("memory@100000000")
        sw.property_string("device_type", "memory")
        sw.property("reg", struct.pack(">QQ", 0x100000000, 1 << 30))
        sw.property_u32("numa-node-id", 1)
        sw.end_node()

    tree = DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
    mem = tree.hardware.memory
    assert [(r.base, r.size, r.node) for r in mem.regions] == [(0x100000000, 1 << 30, 1)]
    assert mem.requested == {}
    assert mem.total_bytes == 1 << 30


def test_parse_requested_memory_nodes():
    def build(sw):
        sw.begin_node("memory@0")
        sw.property_u64("size", 1 << 30)
        sw.property_u32("numa-node-id", 0)
        sw.end_node()
        sw.begin_node("memory@1")
        sw.property_u64("size", 1 << 29)
        sw.end_node()

    tree = DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
    assert tree.hardware.memory.regions == []
    assert tree.hardware.memory.requested == {0: 1 << 30, -1: 1 << 29}
    assert tree.hardware.memory.total_bytes == (1 << 30) + (1 << 29)


def test_parse_memory_node_without_reg_or_size_rejected():
    def build(sw):
        sw.begin_node("memory@0")
        sw.property_u32("numa-node-id", 0)
        sw.end_node()

    with pytest.raises(ParseError):
        DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))


def test_parse_legacy_memory_base_bytes_rejected():
    def build(sw):
        sw.property_u64("memory-base", 0x80000000)
        sw.property_u64("memory-bytes", 1 << 30)

    with pytest.raises(ParseError):
        DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))


def test_parse_cpus_available():
    def build(sw):
        sw.property("cpus-available", struct.pack(">Q", 5))
        sw.begin_node("memory@0")
        sw.property_u64("size", 1 << 30)
        sw.end_node()

    tree = DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
    assert tree.hardware.cpus.available == [4, 5]
    assert tree.hardware.cpus.available_free == [5]


def test_parse_cpus_available_absent():
    def build(sw):
        sw.begin_node("memory@0")
        sw.property_u64("size", 1 << 30)
        sw.end_node()

    tree = DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
    assert tree.hardware.cpus.available_free is None


def test_parse_topology_memory_base_is_not_the_pool():
    def build(sw):
        sw.begin_node("topology")
        sw.begin_node("numa-nodes")
        for node_id, cpu in ((0, 4), (1, 5)):
            sw.begin_node(f"node@{node_id}")
            sw.property_u32("node-id", node_id)
            sw.property_u64("memory-base", node_id * (1 << 35))
            sw.property_u64("memory-size", 1 << 35)
            sw.property("cpus", struct.pack(">Q", cpu))
            sw.end_node()
        sw.end_node()
        sw.end_node()
        sw.begin_node("memory@0")
        sw.property_u64("size", 1 << 30)
        sw.property_u32("numa-node-id", 1)
        sw.end_node()

    tree = DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
    assert tree.hardware.memory.requested == {1: 1 << 30}
    assert tree.hardware.memory.regions == []


def test_parse_dtb_without_memory_rejected():
    def build(sw):
        sw.property_u32("placeholder", 0)

    with pytest.raises(ParseError, match="No memory description"):
        DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))


def test_parse_dtb_live_pool_without_memory_accepted():
    def build(sw):
        sw.property("cpus-available", struct.pack(">Q", 5))

    tree = DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
    assert tree.hardware.memory.regions == []
    assert tree.hardware.memory.requested == {}
    assert tree.hardware.memory.total_bytes == 0
    assert tree.hardware.cpus.available_free == [5]


def test_parse_dtb_legacy_memory_base_alone_rejected():
    def build(sw):
        sw.property_u64("memory-base", 0x80000000)
        sw.begin_node("memory@0")
        sw.property_u64("size", 1 << 30)
        sw.end_node()

    with pytest.raises(ParseError, match="not supported"):
        DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))
