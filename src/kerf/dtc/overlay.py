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
Device tree overlay generation.

This module provides the OverlayGenerator class for generating device tree
overlays (DTBO) that represent incremental changes to the device tree state.
"""

import struct
from typing import Dict, Optional, Set, Tuple

import libfdt

from ..models import GlobalDeviceTree
from ..pool_diff import ANY_NODE, PoolDiff
from .cells import pack_cpu_id, pack_cpu_ids

Range = Optional[Tuple[int, int]]


def _memory_ranges(old_base: int, old_size: int, new_base: int, new_size: int) -> Tuple[Range, Range]:
    """Ranges to take back from and to hand to an instance whose memory changed."""
    if old_base != new_base:
        return (old_base, old_size), (new_base, new_size)
    if new_size > old_size:
        return None, (old_base + old_size, new_size - old_size)
    if new_size < old_size:
        return (old_base + new_size, old_size - new_size), None
    return None, None


class OverlayGenerator:
    """Generates device tree overlay blobs (DTBO) from device tree model deltas."""

    POOL_PATH = "/resources"
    INSTANCES_PATH = "/instances"

    def generate_overlay(self, current: GlobalDeviceTree, modified: GlobalDeviceTree) -> bytes:
        """
        Generate overlay DTBO representing the difference between current and modified states.

        The overlay contains only instance changes (additions, modifications, deletions).
        Hardware resources are never included in overlays.

        Args:
            current: Current device tree state (before change)
            modified: Modified device tree state (after change)

        Returns:
            DTBO blob as bytes
        """
        # Compute instance delta
        instances_to_add = {}
        instances_to_update = {}
        instances_to_remove = set()

        for name, instance in modified.instances.items():
            if name not in current.instances:
                instances_to_add[name] = instance
            elif current.instances[name] != instance:
                instances_to_update[name] = instance

        for name in current.instances:
            if name not in modified.instances:
                instances_to_remove.add(name)

        pci_ids = {name: d.pci_id for name, d in modified.hardware.devices.items() if d.pci_id}
        return self._create_overlay_dtb(instances_to_add, instances_to_update, instances_to_remove,
                                        pci_ids)

    def generate_removal_overlay(self, instance_name: str) -> bytes:
        """
        Generate an instance-remove overlay for a single instance.

        This method generates a minimal overlay that only contains an instance-remove
        fragment, allowing the kernel to handle deletion via mk_instance_destroy().

        Args:
            instance_name: Name of the instance to remove

        Returns:
            DTBO blob as bytes containing only the instance-remove fragment
        """
        return self._create_overlay_dtb({}, {}, {instance_name})

    def generate_update_overlay(self, instance_name: str, old_instance, new_instance,
                                pci_ids: Optional[Dict[str, str]] = None) -> bytes:
        """
        Generate resource update overlay for an existing instance.

        Operations are generated in order: memory-remove, memory-add, cpu-remove, cpu-add,
        device-remove, device-add.
        All operations are in a single fragment.

        Args:
            instance_name: Name of the instance to update
            old_instance: Current instance state
            new_instance: New instance state
            pci_ids: PCI address of each device, by the node name the instances use

        Returns:
            DTBO blob as bytes containing resource update operations
        """
        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()

        fdt_sw.begin_node("")
        fdt_sw.property_string("compatible", "linux,multikernel-overlay")

        old_cpus = set(old_instance.resources.cpus)
        new_cpus = set(new_instance.resources.cpus)
        cpus_to_remove = sorted(old_cpus - new_cpus)
        cpus_to_add = sorted(new_cpus - old_cpus)

        memory_to_remove, memory_to_add = _memory_ranges(
            old_instance.resources.memory_base,
            old_instance.resources.memory_bytes,
            new_instance.resources.memory_base,
            new_instance.resources.memory_bytes,
        )

        numa_node = None
        if new_instance.resources.numa_nodes:
            numa_node = new_instance.resources.numa_nodes[0]

        old_devices = set(old_instance.resources.devices)
        new_devices = set(new_instance.resources.devices)
        pci_ids = pci_ids or {}
        devices_to_remove = sorted(pci_ids.get(d, d) for d in old_devices - new_devices)
        devices_to_add = sorted(pci_ids.get(d, d) for d in new_devices - old_devices)

        # Single fragment with all operations
        fdt_sw.begin_node("fragment@0")
        fdt_sw.property_string("target-path", f"{self.INSTANCES_PATH}/{instance_name}")
        fdt_sw.begin_node("__overlay__")

        if memory_to_remove:
            fdt_sw.begin_node("memory-remove")
            self._memory_item(fdt_sw, memory_to_remove)
            fdt_sw.end_node()

        if memory_to_add:
            fdt_sw.begin_node("memory-add")
            self._memory_item(fdt_sw, memory_to_add, numa_node)
            fdt_sw.end_node()

        self._cpu_op(fdt_sw, "cpu-remove", cpus_to_remove)
        self._cpu_op(fdt_sw, "cpu-add", cpus_to_add, numa_node)
        self._device_op(fdt_sw, "device-remove", devices_to_remove)
        self._device_op(fdt_sw, "device-add", devices_to_add)

        fdt_sw.end_node()  # End __overlay__
        fdt_sw.end_node()  # End fragment@0

        fdt_sw.end_node()  # End root

        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()

    def generate_pool_overlay(self, diff: PoolDiff) -> bytes:
        """
        Generate an overlay moving resources between the host and the pool.

        Operation names read from the pool's point of view: memory-add grows the
        pool, cpu-remove returns a pool CPU to the host, and so on.

        Args:
            diff: Resources to move, as computed against the requested baseline

        Returns:
            DTBO blob as bytes containing a single fragment targeting /resources
        """
        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()

        fdt_sw.begin_node("")
        fdt_sw.property_string("compatible", "linux,multikernel-overlay")

        fdt_sw.begin_node("fragment@0")
        fdt_sw.property_string("target-path", self.POOL_PATH)
        fdt_sw.begin_node("__overlay__")

        if diff.memory_to_host:
            fdt_sw.begin_node("memory-remove")
            for idx, region in enumerate(diff.memory_to_host):
                fdt_sw.begin_node(f"memory@{idx}")
                fdt_sw.property("reg", struct.pack(">QQ", region.base, region.size))
                fdt_sw.end_node()
            fdt_sw.end_node()

        if diff.memory_to_pool:
            fdt_sw.begin_node("memory-add")
            for idx, (node, size) in enumerate(diff.memory_to_pool):
                fdt_sw.begin_node(f"memory@{idx}")
                fdt_sw.property_u64("size", size)
                if node != ANY_NODE:
                    fdt_sw.property_u32("numa-node-id", node)
                fdt_sw.end_node()
            fdt_sw.end_node()

        self._cpu_op(fdt_sw, "cpu-remove", diff.cpus_to_host)
        self._cpu_op(fdt_sw, "cpu-add", diff.cpus_to_pool)
        self._device_op(fdt_sw, "device-remove", diff.devices_to_host)
        self._device_op(fdt_sw, "device-add", diff.devices_to_pool, diff.device_aliases)

        fdt_sw.end_node()  # End __overlay__
        fdt_sw.end_node()  # End fragment@0
        fdt_sw.end_node()  # End root

        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()

    def _memory_item(self, fdt_sw, region, numa_node=None):
        """Write a memory@0 item naming an existing range."""
        base, size = region
        fdt_sw.begin_node("memory@0")
        fdt_sw.property("reg", struct.pack(">QQ", base, size))
        if numa_node is not None:
            fdt_sw.property_u32("numa-node-id", numa_node)
        fdt_sw.end_node()

    def _cpu_op(self, fdt_sw, operation, cpu_ids, numa_node=None):
        """Write a CPU operation node, or nothing when there are no CPUs."""
        if not cpu_ids:
            return

        fdt_sw.begin_node(operation)
        for cpu_id in cpu_ids:
            fdt_sw.begin_node(f"cpu@{cpu_id}")
            fdt_sw.property("reg", pack_cpu_id(cpu_id))
            if numa_node is not None:
                fdt_sw.property_u32("numa-node-id", numa_node)
            fdt_sw.end_node()
        fdt_sw.end_node()

    def _device_op(self, fdt_sw, operation, pci_ids, aliases=None):
        """Write a device operation node, or nothing when there are no devices."""
        if not pci_ids:
            return

        fdt_sw.begin_node(operation)
        for idx, pci_id in enumerate(pci_ids):
            fdt_sw.begin_node(f"pci@{idx}")
            fdt_sw.property_string("pci-id", pci_id)
            if aliases and pci_id in aliases:
                fdt_sw.property_string("alias", aliases[pci_id])
            fdt_sw.end_node()
        fdt_sw.end_node()

    def _create_overlay_dtb(
        self, instances_to_add: dict, instances_to_update: dict, instances_to_remove: Set[str],
        pci_ids: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """
        Create overlay DTB with instance changes using fragment format.

        Args:
            instances_to_add: Dict of instance name -> Instance to add
            instances_to_update: Dict of instance name -> Instance to update
            instances_to_remove: Set of instance names to remove
            pci_ids: PCI address of each pool device, by node name

        Returns:
            DTBO blob as bytes
        """
        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()

        # Root node
        fdt_sw.begin_node("")
        fdt_sw.property_string("compatible", "linux,multikernel-overlay")

        fragment_id = 0

        all_instances = {**instances_to_add, **instances_to_update}
        for name, instance in all_instances.items():
            fdt_sw.begin_node(f"fragment@{fragment_id:x}")
            fdt_sw.property_string("target-path", self.INSTANCES_PATH)
            fdt_sw.begin_node("__overlay__")
            fdt_sw.begin_node("instance-create")

            # Add instance properties
            fdt_sw.property_string("instance-name", name)
            if instance.id is not None:
                fdt_sw.property_u32("id", instance.id)

            fdt_sw.begin_node("resources")

            fdt_sw.property("cpus", pack_cpu_ids(instance.resources.cpus))

            fdt_sw.property_u64("memory-bytes", instance.resources.memory_bytes)

            if instance.resources.numa_nodes:
                numa_data = struct.pack(
                    ">" + "I" * len(instance.resources.numa_nodes), *instance.resources.numa_nodes
                )
                fdt_sw.property("numa-nodes", numa_data)

            if instance.resources.cpu_affinity:
                fdt_sw.property_string("cpu-affinity", instance.resources.cpu_affinity)

            if instance.resources.memory_policy:
                fdt_sw.property_string("memory-policy", instance.resources.memory_policy)

            if instance.resources.uring:
                fdt_sw.begin_node("uring")
                if instance.resources.uring_sq_entries:
                    fdt_sw.property_u32("sq-entries", instance.resources.uring_sq_entries)
                if instance.resources.uring_cq_entries:
                    fdt_sw.property_u32("cq-entries", instance.resources.uring_cq_entries)
                if instance.resources.uring_shim_pages:
                    fdt_sw.property_u32("shim-data-pages", instance.resources.uring_shim_pages)
                fdt_sw.end_node()

            fdt_sw.end_node()  # End resources

            # Add options node if options exist
            if instance.options:
                fdt_sw.begin_node("options")

                # Add enable-host-kcore if enabled
                if instance.options.get("enable-host-kcore"):
                    fdt_sw.property("enable-host-kcore", b"")

                # Future options can be added here

                fdt_sw.end_node()  # End options

            fdt_sw.end_node()  # End instance-create
            fdt_sw.end_node()  # End __overlay__
            fdt_sw.end_node()  # End fragment
            fragment_id += 1

            # Devices join the instance the same way "kerf update" hands them
            # over, by PCI address against the live pool: the kernel resolves
            # device-names against its baseline snapshot, which never learns
            # of devices pooled after init.
            if instance.resources.devices:
                fdt_sw.begin_node(f"fragment@{fragment_id:x}")
                fdt_sw.property_string("target-path", f"{self.INSTANCES_PATH}/{name}")
                fdt_sw.begin_node("__overlay__")
                self._device_op(fdt_sw, "device-add",
                                [(pci_ids or {}).get(d, d) for d in instance.resources.devices])
                fdt_sw.end_node()
                fdt_sw.end_node()
                fragment_id += 1

        for name in instances_to_remove:
            fdt_sw.begin_node(f"fragment@{fragment_id:x}")
            fdt_sw.property_string("target-path", self.INSTANCES_PATH)
            fdt_sw.begin_node("__overlay__")
            fdt_sw.begin_node("instance-remove")
            fdt_sw.property_string("instance-name", name)
            fdt_sw.end_node()  # End instance-remove
            fdt_sw.end_node()  # End __overlay__
            fdt_sw.end_node()  # End fragment
            fragment_id += 1

        fdt_sw.end_node()  # End root

        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()
