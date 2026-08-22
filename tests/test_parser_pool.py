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
from pathlib import Path

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


_DTS_REQUEST = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;

        memory@0 {
            size = <0x0 0x40000000>;
            numa-node-id = <1>;
        };

        memory@1 {
            size = <0x0 0x20000000>;
        };
    };
};
"""

_DTS_READBACK = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;

        memory@100000000 {
            device_type = "memory";
            reg = <0x1 0x0 0x0 0x40000000>;
            numa-node-id = <1>;
        };
    };
};
"""

_DTS_LEGACY = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;
        memory-base = <0x0 0x80000000>;
        memory-bytes = <0x0 0x40000000>;
    };
};
"""


def test_parse_dts_requested_memory_nodes():
    tree = DeviceTreeParser().parse_dts(_DTS_REQUEST)
    assert tree.hardware.memory.regions == []
    assert tree.hardware.memory.requested == {1: 1 << 30, -1: 1 << 29}


def test_parse_dts_readback_regions():
    tree = DeviceTreeParser().parse_dts(_DTS_READBACK)
    regions = tree.hardware.memory.regions
    assert [(r.base, r.size, r.node) for r in regions] == [(0x100000000, 1 << 30, 1)]


def test_parse_dts_legacy_memory_base_bytes_rejected():
    with pytest.raises(ParseError):
        DeviceTreeParser().parse_dts(_DTS_LEGACY)


_DTS_TOPOLOGY = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;

        topology {
            numa-nodes {
                node@0 {
                    node-id = <0>;
                    memory-base = <0x0 0x0>;
                    memory-size = <0x0 0x800000000>;
                    cpus = <4>;
                };

                node@1 {
                    node-id = <1>;
                    memory-base = <0x0 0x800000000>;
                    memory-size = <0x0 0x800000000>;
                    cpus = <5>;
                };
            };
        };

        memory@0 {
            size = <0x0 0x40000000>;
            numa-node-id = <1>;
        };
    };
};
"""

_DTS_LIVE_NO_MEMORY = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;
        cpus-available = <5>;
    };
};
"""

_DTS_NO_MEMORY = """
/multikernel-v1/;

/ {
    resources {
        cpus = <4 5>;
    };
};
"""


def test_parse_dts_topology_memory_base_is_not_the_pool():
    tree = DeviceTreeParser().parse_dts(_DTS_TOPOLOGY)
    assert tree.hardware.memory.requested == {1: 1 << 30}
    assert tree.hardware.memory.regions == []


def test_parse_dts_without_memory_rejected():
    with pytest.raises(ParseError, match="No memory description"):
        DeviceTreeParser().parse_dts(_DTS_NO_MEMORY)


def test_parse_dtb_without_memory_rejected():
    def build(sw):
        sw.property_u32("placeholder", 0)

    with pytest.raises(ParseError, match="No memory description"):
        DeviceTreeParser().parse_dtb_from_bytes(_dtb(build))


def test_parse_dts_live_pool_without_memory_accepted():
    tree = DeviceTreeParser().parse_dts(_DTS_LIVE_NO_MEMORY)
    assert tree.hardware.memory.regions == []
    assert tree.hardware.memory.requested == {}
    assert tree.hardware.memory.total_bytes == 0
    assert tree.hardware.cpus.available_free == [5]


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


@pytest.mark.parametrize(
    "path", sorted(str(p) for p in Path(__file__).parent.parent.glob("examples/*.dts"))
)
def test_parse_example_dts(path):
    tree = DeviceTreeParser().parse_dts(Path(path).read_text(encoding="utf-8"))
    assert tree.hardware.memory.memory_pool_bytes > 0
