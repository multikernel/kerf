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

from typing import Set

import libfdt

from ..models import GlobalDeviceTree


class OverlayGenerator:
    """Generates device tree overlay blobs (DTBO) from device tree model deltas."""

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

        return self._create_overlay_dtb(instances_to_add, instances_to_update, instances_to_remove)

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

    def generate_update_overlay(self, instance_name: str, old_instance, new_instance) -> bytes:
        """
        Generate resource update overlay for an existing instance.

        Operations are generated in order: memory-remove, memory-add, cpu-remove, cpu-add,
        device-remove, device-add.
        All operations are in a single fragment.

        Args:
            instance_name: Name of the instance to update
            old_instance: Current instance state
            new_instance: New instance state

        Returns:
            DTBO blob as bytes containing resource update operations
        """
        import struct

        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()

        fdt_sw.begin_node("")
        fdt_sw.property_string("compatible", "linux,multikernel-overlay")

        old_cpus = set(old_instance.resources.cpus)
        new_cpus = set(new_instance.resources.cpus)
        cpus_to_remove = sorted(old_cpus - new_cpus)
        cpus_to_add = sorted(new_cpus - old_cpus)

        old_mem_base = old_instance.resources.memory_base
        old_mem_size = old_instance.resources.memory_bytes
        new_mem_base = new_instance.resources.memory_base
        new_mem_size = new_instance.resources.memory_bytes
        memory_changed = (old_mem_base != new_mem_base) or (old_mem_size != new_mem_size)

        old_devices = set(old_instance.resources.devices)
        new_devices = set(new_instance.resources.devices)
        devices_to_remove = sorted(old_devices - new_devices)
        devices_to_add = sorted(new_devices - old_devices)

        # Single fragment with all operations
        fdt_sw.begin_node("fragment@0")
        fdt_sw.begin_node("__overlay__")

        # 1. memory-remove (if memory changed)
        if memory_changed:
            fdt_sw.begin_node("memory-remove")
            fdt_sw.property_string("mk,instance", instance_name)

            fdt_sw.begin_node("region@0")
            reg_data = struct.pack(">QQ", old_mem_base, old_mem_size)
            fdt_sw.property("reg", reg_data)
            fdt_sw.end_node()

            fdt_sw.end_node()

        # 2. memory-add (if memory changed)
        if memory_changed:
            fdt_sw.begin_node("memory-add")
            fdt_sw.property_string("mk,instance", instance_name)

            fdt_sw.begin_node("region@0")
            reg_data = struct.pack(">QQ", new_mem_base, new_mem_size)
            fdt_sw.property("reg", reg_data)
            fdt_sw.end_node()

            fdt_sw.end_node()

        # 3. cpu-remove (if CPUs removed)
        if cpus_to_remove:
            fdt_sw.begin_node("cpu-remove")
            fdt_sw.property_string("mk,instance", instance_name)

            for cpu_id in cpus_to_remove:
                fdt_sw.begin_node(f"cpu@{cpu_id}")
                reg_data = struct.pack(">I", cpu_id)
                fdt_sw.property("reg", reg_data)
                fdt_sw.end_node()

            fdt_sw.end_node()

        # 4. cpu-add (if CPUs added)
        if cpus_to_add:
            fdt_sw.begin_node("cpu-add")
            fdt_sw.property_string("mk,instance", instance_name)

            for cpu_id in cpus_to_add:
                fdt_sw.begin_node(f"cpu@{cpu_id}")
                reg_data = struct.pack(">I", cpu_id)
                fdt_sw.property("reg", reg_data)

                if new_instance.resources.numa_nodes:
                    fdt_sw.property_u32("numa-node", new_instance.resources.numa_nodes[0])

                fdt_sw.end_node()

            fdt_sw.end_node()

        # 5. device-remove (if devices removed)
        if devices_to_remove:
            fdt_sw.begin_node("device-remove")
            fdt_sw.property_string("mk,instance", instance_name)

            for idx, pci_id in enumerate(devices_to_remove):
                fdt_sw.begin_node(f"pci@{idx}")
                fdt_sw.property_string("pci-id", pci_id)
                fdt_sw.end_node()

            fdt_sw.end_node()

        # 6. device-add (if devices added)
        if devices_to_add:
            fdt_sw.begin_node("device-add")
            fdt_sw.property_string("mk,instance", instance_name)

            for idx, pci_id in enumerate(devices_to_add):
                fdt_sw.begin_node(f"pci@{idx}")
                fdt_sw.property_string("pci-id", pci_id)
                fdt_sw.end_node()

            fdt_sw.end_node()

        fdt_sw.end_node()  # End __overlay__
        fdt_sw.end_node()  # End fragment@0

        fdt_sw.end_node()  # End root

        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()

    def _add_memory_operation(self, fdt_sw, fragment_id, operation, instance_name, base, size):
        """Helper to add memory operation fragment."""
        import struct

        fdt_sw.begin_node(f"fragment@{fragment_id}")
        fdt_sw.begin_node("__overlay__")
        fdt_sw.begin_node(operation)
        fdt_sw.property_string("mk,instance", instance_name)

        fdt_sw.begin_node("region@0")
        reg_data = struct.pack(">QQ", base, size)
        fdt_sw.property("reg", reg_data)
        fdt_sw.end_node()

        fdt_sw.end_node()
        fdt_sw.end_node()
        fdt_sw.end_node()

        return fragment_id + 1

    def _add_cpu_operation(
        self, fdt_sw, fragment_id, operation, instance_name, cpu_ids, numa_nodes
    ):
        """Helper to add CPU operation fragment."""
        import struct

        fdt_sw.begin_node(f"fragment@{fragment_id}")
        fdt_sw.begin_node("__overlay__")
        fdt_sw.begin_node(operation)
        fdt_sw.property_string("mk,instance", instance_name)

        for cpu_id in cpu_ids:
            fdt_sw.begin_node(f"cpu@{cpu_id}")
            reg_data = struct.pack(">I", cpu_id)
            fdt_sw.property("reg", reg_data)

            if operation == "cpu-add" and numa_nodes:
                fdt_sw.property_u32("numa-node", numa_nodes[0])

            fdt_sw.end_node()

        fdt_sw.end_node()
        fdt_sw.end_node()
        fdt_sw.end_node()

        return fragment_id + 1

    def _create_overlay_dtb(
        self, instances_to_add: dict, instances_to_update: dict, instances_to_remove: Set[str]
    ) -> bytes:
        """
        Create overlay DTB with instance changes using fragment format.

        Args:
            instances_to_add: Dict of instance name -> Instance to add
            instances_to_update: Dict of instance name -> Instance to update
            instances_to_remove: Set of instance names to remove

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
            fdt_sw.begin_node(f"fragment@{fragment_id}")
            fdt_sw.begin_node("__overlay__")
            fdt_sw.begin_node("instance-create")

            # Add instance properties
            fdt_sw.property_string("instance-name", name)
            if instance.id is not None:
                fdt_sw.property_u32("id", instance.id)

            fdt_sw.begin_node("resources")

            import struct

            cpus_data = struct.pack(
                ">" + "I" * len(instance.resources.cpus), *instance.resources.cpus
            )
            fdt_sw.property("cpus", cpus_data)

            fdt_sw.property_u64("memory-base", instance.resources.memory_base)
            fdt_sw.property_u64("memory-bytes", instance.resources.memory_bytes)

            if instance.resources.devices:
                fdt_sw.property_string("device-names", " ".join(instance.resources.devices))

            if instance.resources.numa_nodes:
                import struct

                numa_data = struct.pack(
                    ">" + "I" * len(instance.resources.numa_nodes), *instance.resources.numa_nodes
                )
                fdt_sw.property("numa-nodes", numa_data)

            if instance.resources.cpu_affinity:
                fdt_sw.property_string("cpu-affinity", instance.resources.cpu_affinity)

            if instance.resources.memory_policy:
                fdt_sw.property_string("memory-policy", instance.resources.memory_policy)

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

        for name in instances_to_remove:
            fdt_sw.begin_node(f"fragment@{fragment_id}")
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
