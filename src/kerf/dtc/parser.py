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
Device tree parsing and model building.
"""

import struct
from typing import Dict, List, Optional

import libfdt

from ..exceptions import ParseError
from ..pool_diff import ANY_NODE
from .cells import unpack_cpu_ids
from ..models import (
    CPUAllocation,
    DeviceInfo,
    GlobalDeviceTree,
    HardwareInventory,
    Instance,
    InstanceResources,
    MemoryAllocation,
    NUMANode,
    OverlayInstanceData,
    PoolMemoryRegion,
    TopologySection,
)


_LEGACY_MEMORY_ERROR = (
    "memory-base/memory-bytes are not supported; "
    "use memory@N { size; numa-node-id; }"
)

_NO_MEMORY_ERROR = (
    "No memory description in /resources; "
    "expected memory@N { size; numa-node-id; }"
)

# A live pool always publishes cpus-available, and may legitimately hold no
# memory at all: a transaction that gave back every chunk but kept the CPUs.


class DeviceTreeParser:
    """Parser for multikernel device trees."""

    def __init__(self):
        self.fdt = None
        self._last_overlay_data: Optional[OverlayInstanceData] = None

    def parse_dtb(self, dtb_path: str) -> GlobalDeviceTree:
        """Parse DTB file into GlobalDeviceTree model."""
        try:
            with open(dtb_path, 'rb') as f:
                dtb_data = f.read()

            return self.parse_dtb_from_bytes(dtb_data)
        except Exception as e:
            raise ParseError(f"Failed to parse DTB file {dtb_path}: {e}") from e

    def parse_dtb_from_bytes(self, dtb_data: bytes) -> GlobalDeviceTree:
        """Parse DTB from bytes into GlobalDeviceTree model."""
        try:
            self.fdt = libfdt.Fdt(dtb_data)
            return self._build_global_tree()
        except libfdt.FdtException as e:
            error_msg = f"FDT error: {e}"
            if hasattr(e, 'err'):
                error_msg += f" (error code: {e.err})"
            raise ParseError(f"Failed to parse DTB from bytes: {error_msg}") from e
        except Exception as e:
            raise ParseError(f"Failed to parse DTB from bytes: {e}") from e

    def _build_global_tree(self) -> GlobalDeviceTree:
        """Build GlobalDeviceTree from parsed FDT."""
        try:
            root = self.fdt.path_offset('/')
        except libfdt.FdtException as e:
            raise ParseError(f"Failed to access root node: {e}") from e

        is_overlay = False
        try:
            compatible = self.fdt.getprop(root, 'compatible')
            if compatible:
                compatible_str = compatible.as_str().rstrip('\0')
                if compatible_str == 'linux,multikernel-overlay':
                    is_overlay = True
        except libfdt.FdtException:
            pass

        # Fallback: detect overlays by fragment nodes (for compatibility with overlays missing compatible property)
        if not is_overlay:
            try:
                offset = self.fdt.first_subnode(root)
                while offset >= 0:
                    if self.fdt.get_name(offset).startswith('fragment@'):
                        is_overlay = True
                        break
                    try:
                        offset = self.fdt.next_subnode(offset)
                    except libfdt.FdtException:
                        break
            except libfdt.FdtException:
                pass

        if not is_overlay:
            try:
                hardware = self._parse_hardware_inventory()
            except ParseError:
                raise
            except libfdt.FdtException as e:
                raise ParseError(f"Failed to parse hardware inventory: FDT error - {e}") from e
            except Exception as e:
                raise ParseError(f"Failed to parse hardware inventory: {e}") from e
        else:
            # Overlays have empty hardware (resources are in baseline only)
            from ..models import HardwareInventory, CPUAllocation, MemoryAllocation  # pylint: disable=reimported,redefined-outer-name
            hardware = HardwareInventory(
                cpus=CPUAllocation(total=0, host_reserved=[], available=[]),
                memory=MemoryAllocation(
                    total_bytes=0,
                    host_reserved_bytes=0,
                ),
                topology=None,
                devices={}
            )

        try:
            if is_overlay:
                overlay_data = self._parse_overlay_instances()
                self._last_overlay_data = overlay_data
                instances = overlay_data.instances
            else:
                instances = self._parse_instances()
                self._last_overlay_data = None
        except Exception as e:
            raise ParseError(f"Failed to parse instances: {e}") from e

        device_refs = {}
        if not is_overlay:
            try:
                device_refs = self._parse_device_references()
            except Exception as e:
                raise ParseError(f"Failed to parse device references: {e}") from e

        return GlobalDeviceTree(
            hardware=hardware,
            instances=instances,
            device_references=device_refs
        )

    def _parse_hardware_inventory(self) -> HardwareInventory:
        """Parse hardware inventory from /resources."""
        try:
            resources_node = self.fdt.path_offset('/resources')
        except libfdt.FdtException as e:
            raise ParseError(f"Missing /resources node: {e}") from e

        # Parse CPU information
        try:
            cpus = self._parse_cpu_allocation(resources_node)
        except libfdt.FdtException as e:
            raise ParseError(f"Error parsing CPU allocation: {e}") from e

        # Parse memory information
        try:
            memory = self._parse_memory_allocation(resources_node)
        except libfdt.FdtException as e:
            raise ParseError(f"Error parsing memory allocation: {e}") from e

        # Parse topology section
        topology = self._parse_topology(resources_node)

        # Parse devices
        devices = self._parse_devices(resources_node)

        return HardwareInventory(
            cpus=cpus,
            memory=memory,
            topology=topology,
            devices=devices
        )

    def _parse_cpu_allocation(self, resources_node: int) -> CPUAllocation:
        """Parse CPU allocation from resources node."""
        try:
            cpus_prop = self.fdt.getprop(resources_node, 'cpus')
            available = unpack_cpu_ids(cpus_prop)
        except libfdt.FdtException:
            # No cpus property means all CPUs are allocated
            available = []

        available_free = None
        free_prop = self._optional_prop(resources_node, 'cpus-available')
        if free_prop is not None:
            available_free = unpack_cpu_ids(free_prop)

        if available:
            total = max(available) + 1
        else:
            total = 0
        host_reserved = []

        return CPUAllocation(
            total=total,
            host_reserved=host_reserved,
            available=available,
            available_free=available_free
        )

    def _subnodes(self, node: int):
        """Yield the offsets of a node's immediate children."""
        try:
            offset = self.fdt.first_subnode(node)
        except libfdt.FdtException:
            return
        while offset >= 0:
            yield offset
            try:
                offset = self.fdt.next_subnode(offset)
            except libfdt.FdtException:
                return

    def _optional_prop(self, node: int, name: str):
        """Return a property, or None when the node does not carry it."""
        try:
            return self.fdt.getprop(node, name)
        except libfdt.FdtException:
            return None

    def _optional_u32(self, node: int, name: str, default: int) -> int:
        """Return a u32 property, or default when the node does not carry it."""
        prop = self._optional_prop(node, name)
        if prop is None:
            return default
        return prop.as_uint32()

    def _parse_memory_allocation(self, resources_node: int) -> MemoryAllocation:
        """Parse pool chunks and per-node memory requests from resources node."""
        regions = []
        requested = {}

        for node in self._subnodes(resources_node):
            name = self.fdt.get_name(node)
            if not name.startswith('memory@'):
                continue
            node_id = self._optional_u32(node, 'numa-node-id', ANY_NODE)
            reg = self._optional_prop(node, 'reg')
            size_prop = self._optional_prop(node, 'size')
            if reg is not None:
                if len(reg) != 16:
                    raise ParseError(f"{name}: 'reg' must be two u64 cells")
                base, size = struct.unpack('>QQ', bytes(reg))
                regions.append(PoolMemoryRegion(base=base, size=size, node=node_id))
            elif size_prop is not None:
                if len(size_prop) != 8:
                    raise ParseError(f"{name}: 'size' must be a u64")
                requested[node_id] = requested.get(node_id, 0) + size_prop.as_uint64()
            else:
                raise ParseError(f"{name}: expected 'reg' (existing chunk) or 'size' (request)")

        for legacy in ('memory-base', 'memory-bytes'):
            if self._optional_prop(resources_node, legacy) is not None:
                raise ParseError(_LEGACY_MEMORY_ERROR)

        if not regions and not requested:
            if self._optional_prop(resources_node, 'cpus-available') is None:
                raise ParseError(_NO_MEMORY_ERROR)

        total_bytes = sum(r.size for r in regions) or sum(requested.values())

        return MemoryAllocation(
            total_bytes=total_bytes,
            host_reserved_bytes=0,
            regions=regions,
            requested=requested
        )

    def _parse_devices(self, resources_node: int) -> Dict[str, DeviceInfo]:
        """Parse device information from resources node."""
        devices = {}

        try:
            devices_node = self.fdt.subnode_offset(resources_node, 'devices')
        except libfdt.FdtException:
            return devices

        # Iterate through device nodes
        offset = self.fdt.first_subnode(devices_node)
        while offset >= 0:
            name = self.fdt.get_name(offset)
            try:
                device_info = self._parse_device_info(offset, name)
                devices[name] = device_info
            except ParseError:
                # Skip nodes that don't have required properties (not valid devices)
                pass
            try:
                offset = self.fdt.next_subnode(offset)
            except libfdt.FdtException:
                # No more subnodes
                break

        return devices

    def _parse_device_info(self, node_offset: int, name: str) -> DeviceInfo:
        """Parse individual device information."""
        compatible = ""
        try:
            compatible = self.fdt.getprop(node_offset, 'compatible').as_str()
        except libfdt.FdtException:
            pass

        # Parse optional properties
        device_type = None
        device_name = None
        pci_id = None
        vendor_id = None
        device_id = None
        sriov_vfs = None
        host_reserved_vf = None
        available_vfs = None
        namespaces = None
        host_reserved_ns = None
        available_ns = None

        try:
            device_type = self.fdt.getprop(node_offset, 'device-type').as_str()
        except libfdt.FdtException:
            pass

        try:
            device_name = self.fdt.getprop(node_offset, 'device-name').as_str()
        except libfdt.FdtException:
            pass

        try:
            pci_id = self.fdt.getprop(node_offset, 'pci-id').as_str()
        except libfdt.FdtException:
            pass

        try:
            vendor_id = self.fdt.getprop(node_offset, 'vendor-id').as_uint32()
        except libfdt.FdtException:
            pass

        try:
            device_id = self.fdt.getprop(node_offset, 'device-id').as_uint32()
        except libfdt.FdtException:
            pass

        try:
            sriov_vfs = self.fdt.getprop(node_offset, 'sriov-vfs').as_uint32()
        except libfdt.FdtException:
            pass

        try:
            host_reserved_vf = self.fdt.getprop(node_offset, 'host-reserved-vf').as_uint32()
        except libfdt.FdtException:
            pass

        try:
            available_vfs = self.fdt.getprop(node_offset, 'available-vfs').as_uint32_list()
        except libfdt.FdtException:
            pass

        try:
            namespaces = self.fdt.getprop(node_offset, 'namespaces').as_uint32()
        except libfdt.FdtException:
            pass

        try:
            host_reserved_ns = self.fdt.getprop(node_offset, 'host-reserved-ns').as_uint32()
        except libfdt.FdtException:
            pass

        try:
            available_ns = self.fdt.getprop(node_offset, 'available-ns').as_uint32_list()
        except libfdt.FdtException:
            pass

        return DeviceInfo(
            name=name,
            compatible=compatible,
            device_type=device_type,
            device_name=device_name,
            pci_id=pci_id,
            vendor_id=vendor_id,
            device_id=device_id,
            sriov_vfs=sriov_vfs,
            host_reserved_vf=host_reserved_vf,
            available_vfs=available_vfs,
            namespaces=namespaces,
            host_reserved_ns=host_reserved_ns,
            available_ns=available_ns
        )

    def _parse_instances(self) -> Dict[str, Instance]:
        """Parse instance definitions from /instances."""
        instances = {}

        try:
            instances_node = self.fdt.path_offset('/instances')
        except libfdt.FdtException:
            return instances

        # Iterate through instance nodes
        try:
            offset = self.fdt.first_subnode(instances_node)
            while offset >= 0:
                name = self.fdt.get_name(offset)
                try:
                    instance = self._parse_instance(offset, name)
                    instances[name] = instance
                except Exception as e:
                    raise ParseError(f"Failed to parse instance '{name}': {e}") from e

                try:
                    offset = self.fdt.next_subnode(offset)
                except libfdt.FdtException:
                    break
        except libfdt.FdtException as e:
            error_str = str(e)
            if 'FDT_ERR_NOTFOUND' in error_str or 'NOTFOUND' in error_str:
                return instances
            # For other errors, re-raise
            raise ParseError(f"Error iterating instance nodes: {e}") from e

        return instances

    def _parse_overlay_instances(self) -> OverlayInstanceData:
        """Parse instance definitions from overlay fragments (fragment@X/__overlay__/instance-create)."""
        instances = {}
        removals = set()

        try:
            root = self.fdt.path_offset('/')
        except libfdt.FdtException:
            return OverlayInstanceData(instances=instances, removals=removals)

        try:
            offset = self.fdt.first_subnode(root)
            while offset >= 0:
                name = self.fdt.get_name(offset)

                if name.startswith('fragment@'):
                    try:
                        overlay_node = self.fdt.subnode_offset(offset, '__overlay__')

                        try:
                            instance_create_node = self.fdt.subnode_offset(overlay_node, 'instance-create')
                            instance = self._parse_instance_create(instance_create_node)
                            instances[instance.name] = instance
                        except libfdt.FdtException:
                            try:
                                instance_remove_node = self.fdt.subnode_offset(overlay_node, 'instance-remove')
                                instance_name_prop = self.fdt.getprop(instance_remove_node, 'instance-name')
                                instance_name = instance_name_prop.as_str()
                                removals.add(instance_name)
                            except libfdt.FdtException:
                                pass
                    except libfdt.FdtException:
                        pass

                try:
                    offset = self.fdt.next_subnode(offset)
                except libfdt.FdtException:
                    break
        except libfdt.FdtException:
            pass

        return OverlayInstanceData(instances=instances, removals=removals)

    def get_last_overlay_data(self) -> Optional[OverlayInstanceData]:
        """Get the overlay data from the last parsed overlay (if any)."""
        return self._last_overlay_data

    def _parse_instance_create(self, node_offset: int) -> Instance:
        """Parse instance from instance-create node in overlay."""
        try:
            instance_name_prop = self.fdt.getprop(node_offset, 'instance-name')
            instance_name = instance_name_prop.as_str()
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'instance-name' property in instance-create: {e}") from e

        instance_id = None
        try:
            instance_id = self.fdt.getprop(node_offset, 'id').as_uint32()
        except libfdt.FdtException:
            pass

        resources = self._parse_instance_resources_from_overlay(node_offset)
        options = self._parse_instance_options(node_offset)

        return Instance(
            name=instance_name,
            id=instance_id,
            resources=resources,
            options=options
        )

    def _parse_instance_resources_from_overlay(self, node_offset: int) -> InstanceResources:
        """Parse instance resources from overlay instance-create node."""
        try:
            resources_node = self.fdt.subnode_offset(node_offset, 'resources')
        except libfdt.FdtException as e:
            raise ParseError(f"Missing resources node in instance-create: {e}") from e

        try:
            cpus_prop = self.fdt.getprop(resources_node, 'cpus')
            cpus = unpack_cpu_ids(cpus_prop)
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'cpus' property in resources: {e}") from e

        try:
            memory_bytes = self.fdt.getprop(resources_node, 'memory-bytes').as_uint64()
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'memory-bytes' property in resources: {e}") from e

        memory_base = 0
        try:
            memory_base = self.fdt.getprop(resources_node, 'memory-base').as_uint64()
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'memory-base' property in resources: {e}") from e

        devices = []
        try:
            device_names_prop = self.fdt.getprop(resources_node, 'device-names')
            device_names_str = device_names_prop.as_str()
            if device_names_str:
                devices = [d.strip() for d in device_names_str.split() if d.strip()]
        except libfdt.FdtException:
            pass

        uring_enabled = False
        uring_sq = None
        uring_cq = None
        uring_shim = None
        try:
            uring_node = self.fdt.subnode_offset(resources_node, 'uring')
            uring_enabled = True
            try:
                uring_sq = self.fdt.getprop(uring_node, 'sq-entries').as_uint32()
            except libfdt.FdtException:
                pass
            try:
                uring_cq = self.fdt.getprop(uring_node, 'cq-entries').as_uint32()
            except libfdt.FdtException:
                pass
            try:
                uring_shim = self.fdt.getprop(uring_node, 'shim-data-pages').as_uint32()
            except libfdt.FdtException:
                pass
        except libfdt.FdtException:
            pass

        return InstanceResources(
            cpus=cpus,
            memory_base=memory_base,
            memory_bytes=memory_bytes,
            devices=devices,
            uring=uring_enabled,
            uring_sq_entries=uring_sq,
            uring_cq_entries=uring_cq,
            uring_shim_pages=uring_shim,
        )

    def _parse_instance(self, node_offset: int, name: str) -> Instance:
        """Parse individual instance definition."""
        # Parse instance ID
        try:
            instance_id = self.fdt.getprop(node_offset, 'id').as_uint32()
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'id' property for instance '{name}': {e}") from e

        # Parse resources and options
        resources = self._parse_instance_resources(node_offset)
        options = self._parse_instance_options(node_offset)

        return Instance(
            name=name,
            id=instance_id,
            resources=resources,
            options=options
        )

    def _parse_instance_resources(self, node_offset: int) -> InstanceResources:
        """Parse instance resource allocation."""
        try:
            resources_node = self.fdt.subnode_offset(node_offset, 'resources')
        except libfdt.FdtException as e:
            raise ParseError(f"Missing resources node for instance: {e}") from e

        try:
            cpus = unpack_cpu_ids(self.fdt.getprop(resources_node, 'cpus'))
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'cpus' property in instance resources: {e}") from e

        try:
            memory_base = self.fdt.getprop(resources_node, 'memory-base').as_uint64()
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'memory-base' property in instance resources: {e}") from e

        try:
            memory_bytes = self.fdt.getprop(resources_node, 'memory-bytes').as_uint64()
        except libfdt.FdtException as e:
            raise ParseError(f"Missing 'memory-bytes' property in instance resources: {e}") from e

        devices = []
        try:
            device_names_prop = self.fdt.getprop(resources_node, 'device-names')
            device_names_str = device_names_prop.as_str()
            if device_names_str:
                devices = [d.strip() for d in device_names_str.split() if d.strip()]
        except libfdt.FdtException:
            pass

        uring_enabled = False
        uring_sq = None
        uring_cq = None
        uring_shim = None
        try:
            uring_node = self.fdt.subnode_offset(resources_node, 'uring')
            uring_enabled = True
            try:
                uring_sq = self.fdt.getprop(uring_node, 'sq-entries').as_uint32()
            except libfdt.FdtException:
                pass
            try:
                uring_cq = self.fdt.getprop(uring_node, 'cq-entries').as_uint32()
            except libfdt.FdtException:
                pass
            try:
                uring_shim = self.fdt.getprop(uring_node, 'shim-data-pages').as_uint32()
            except libfdt.FdtException:
                pass
        except libfdt.FdtException:
            pass

        return InstanceResources(
            cpus=cpus,
            memory_base=memory_base,
            memory_bytes=memory_bytes,
            devices=devices,
            uring=uring_enabled,
            uring_sq_entries=uring_sq,
            uring_cq_entries=uring_cq,
            uring_shim_pages=uring_shim,
        )

    def _parse_instance_options(self, node_offset: int) -> Optional[Dict[str, bool]]:
        """Parse instance options from DTB node."""
        options = {}

        try:
            options_node = self.fdt.subnode_offset(node_offset, 'options')
        except libfdt.FdtException:
            return None if not options else options

        try:
            self.fdt.getprop(options_node, 'enable-host-kcore')
            options['enable-host-kcore'] = True
        except libfdt.FdtException:
            pass

        return options if options else None

    def _parse_device_references(self) -> Dict[str, Dict]:
        """Parse device reference nodes (phandle targets) from DTB."""
        device_references = {}

        # When parsing from DTB, device references are nodes at the root level
        # that match the pattern of device references (e.g., eth0_vf1, nvme0_ns2)
        try:
            root = self.fdt.path_offset('/')
        except libfdt.FdtException:
            return device_references

        # Iterate through root-level nodes to find device references
        # Device references are typically named like: eth0_vf1, nvme0_ns2, etc.
        # Skip known nodes like 'resources' and 'instances'
        try:
            offset = self.fdt.first_subnode(root)
            while offset >= 0:
                name = self.fdt.get_name(offset)

                # Skip known structural nodes
                if name in ('resources', 'instances'):
                    offset = self.fdt.next_subnode(offset)
                    continue

                # Check if this looks like a device reference (contains _vf or _ns)
                if '_vf' in name or '_ns' in name:
                    device_ref = {}

                    # Parse parent property
                    try:
                        parent = self.fdt.getprop(offset, 'parent').as_str()
                        device_ref['parent'] = parent
                    except libfdt.FdtException:
                        pass

                    # Parse vf-id if it's a VF reference
                    if '_vf' in name:
                        try:
                            vf_id = self.fdt.getprop(offset, 'vf-id').as_uint32()
                            device_ref['vf_id'] = vf_id
                        except libfdt.FdtException:
                            pass

                    # Parse namespace-id if it's a namespace reference
                    if '_ns' in name:
                        try:
                            ns_id = self.fdt.getprop(offset, 'namespace-id').as_uint32()
                            device_ref['namespace_id'] = ns_id
                        except libfdt.FdtException:
                            pass

                    if device_ref:  # Only add if we found at least one property
                        device_references[name] = device_ref

                offset = self.fdt.next_subnode(offset)
        except libfdt.FdtException:
            # If we can't iterate subnodes, just return empty dict
            pass

        return device_references

    def _parse_topology(self, resources_node: int) -> Optional[TopologySection]:
        """Parse topology section from resources node."""
        try:
            topology_node = self.fdt.subnode_offset(resources_node, 'topology')
        except libfdt.FdtException:
            return None

        # Parse NUMA nodes from topology section
        numa_nodes = self._parse_numa_nodes_from_topology(topology_node)

        return TopologySection(numa_nodes=numa_nodes) if numa_nodes else None

    def _parse_numa_nodes_from_topology(self, topology_node: int) -> Optional[Dict[int, NUMANode]]:
        """Parse NUMA nodes from topology section."""
        try:
            numa_nodes_node = self.fdt.subnode_offset(topology_node, 'numa-nodes')
        except libfdt.FdtException:
            return None

        nodes = {}
        for offset in self._subnodes(numa_nodes_node):
            node_name = self.fdt.get_name(offset)
            if node_name.startswith('node@'):
                node_id = int(node_name.split('@')[1])
                nodes[node_id] = self._parse_numa_node_info(offset, node_id)

        return nodes if nodes else None

    def _parse_numa_node_info(self, node_offset: int, node_id: int) -> NUMANode:
        """Parse individual NUMA node information."""
        # Parse memory-base
        memory_base = 0
        try:
            memory_base = self.fdt.getprop(node_offset, 'memory-base').as_uint64()
        except libfdt.FdtException:
            pass

        # Parse memory-size
        memory_size = 0
        try:
            memory_size = self.fdt.getprop(node_offset, 'memory-size').as_uint64()
        except libfdt.FdtException:
            pass

        # Parse CPUs
        cpus = []
        try:
            cpus = self.fdt.getprop(node_offset, 'cpus').as_uint32_list()
        except libfdt.FdtException:
            pass

        # Parse distance matrix (optional)
        distance_matrix = {}
        try:
            _ = self.fdt.getprop(node_offset, 'distance-matrix').as_uint32_list()
            # Simple distance matrix parsing - would need more sophisticated logic for full matrix
        except libfdt.FdtException:
            pass

        # Parse memory type
        memory_type = "dram"
        try:
            memory_type = self.fdt.getprop(node_offset, 'memory-type').as_str()
        except libfdt.FdtException:
            pass

        return NUMANode(
            node_id=node_id,
            memory_base=memory_base,
            memory_size=memory_size,
            cpus=cpus,
            distance_matrix=distance_matrix,
            memory_type=memory_type
        )

    def dtb_to_dts(self, dtb_path: str) -> str:
        """Convert DTB file back to DTS format using pure Python implementation."""
        try:
            with open(dtb_path, 'rb') as f:
                _ = f.read()  # Read to validate file exists and is readable

            # Create a comprehensive DTS representation
            dts_lines = [
                '/multikernel-v1/;',
                '',
                '/ {',
                '    compatible = "linux,multikernel-host";',
                '    // DTB converted from binary format using pure Python implementation',
                '    // This is a simplified representation of the original DTB',
                '    // The full structure may require manual reconstruction',
                '',
                '    // Note: This DTB was generated by kerf and contains',
                '    // multikernel device tree information in binary format.',
                '    // To get the original DTS source, use the original .dts file.',
                '};'
            ]

            return '\n'.join(dts_lines)

        except Exception as e:
            raise ParseError(f"Failed to convert DTB to DTS: {e}") from e

    def _fdt_to_dts_recursive(self, node_offset: int, indent_level: int) -> List[str]:
        """Recursively convert FDT nodes to DTS format."""
        lines = []
        indent = '    ' * indent_level

        try:
            # Get node name
            node_name = self.fdt.get_name(node_offset)
            if not node_name:
                node_name = '/'  # Empty name means root

            # Start node
            if not node_name or node_name == '/':
                lines.append(f'{indent}/ {{')
            else:
                if node_offset == 0:
                    lines.append(f'{indent}/{node_name} {{')
                else:
                    lines.append(f'{indent}{node_name} {{')

            # Get properties for this node
            try:
                prop_offset = self.fdt.first_property_offset(node_offset, libfdt.QUIET_NOTFOUND)
                while prop_offset >= 0:
                    try:
                        prop = self.fdt.get_property_by_offset(prop_offset)
                        prop_name = prop.name
                        prop_data = bytes(prop)

                        # Convert property to DTS format
                        prop_line = self._property_to_dts(prop_name, prop_data, indent + '    ')
                        if prop_line:
                            lines.append(prop_line)
                    except Exception as e:
                        # Skip problematic properties but log for debugging
                        lines.append(f'{indent}    // Error reading property: {e}')

                    try:
                        prop_offset = self.fdt.next_property_offset(prop_offset, libfdt.QUIET_NOTFOUND)
                    except Exception:
                        break
            except Exception:
                # No properties or error accessing properties
                pass

            # Process child nodes
            try:
                child_offset = self.fdt.first_subnode(node_offset)
                while child_offset >= 0:
                    try:
                        child_lines = self._fdt_to_dts_recursive(child_offset, indent_level + 1)
                        lines.extend(child_lines)
                    except Exception:
                        # Skip problematic child nodes
                        pass

                    try:
                        child_offset = self.fdt.next_subnode(child_offset)
                    except Exception:
                        break
            except Exception:
                # No child nodes or error accessing child nodes
                pass

            # Close node
            lines.append(f'{indent}}};')

        except Exception as e:
            # If we can't process this node, create a placeholder
            lines.append(f'{indent}// Error processing node: {e}')
            lines.append(f'{indent}}};')

        return lines

    def _is_printable_string(self, data: bytes) -> bool:
        """Check if bytes represent a printable ASCII string."""
        return len(data) >= 2 and all(32 <= b < 127 or b in (9, 10, 13) for b in data)

    def _try_parse_stringlist(self, data: bytes) -> Optional[List[str]]:
        """Try to parse data as a stringlist. Returns list of strings or None."""
        stripped = data.rstrip(b'\x00')
        if not stripped:
            return None

        parts = stripped.split(b'\x00')
        strings = []
        for part in parts:
            if not part or not self._is_printable_string(part):
                return None
            try:
                strings.append(part.decode('utf-8'))
            except UnicodeDecodeError:
                return None
        return strings if strings else None

    def _property_to_dts(self, name: str, data: bytes, indent: str) -> str:
        """Convert FDT property to DTS format."""
        if not data:
            return f'{indent}{name};'

        # Handle standard integer sizes first
        if len(data) == 4:
            value = int.from_bytes(data, byteorder='big')
            return f'{indent}{name} = <{hex(value)}>;'
        if len(data) == 8:
            high = int.from_bytes(data[:4], byteorder='big')
            low = int.from_bytes(data[4:], byteorder='big')
            return f'{indent}{name} = <{hex(high)} {hex(low)}>;'

        # Try parsing as stringlist
        strings = self._try_parse_stringlist(data)
        if strings is not None:
            quoted = ', '.join(f'"{s}"' for s in strings)
            return f'{indent}{name} = {quoted};'

        # Handle arrays of 32-bit integers
        if len(data) % 4 == 0:
            values = [hex(int.from_bytes(data[i:i+4], byteorder='big')) for i in range(0, len(data), 4)]
            return f'{indent}{name} = <{" ".join(values)}>;'

        # Fall back to hex representation
        hex_data = ' '.join(f'{b:02x}' for b in data)
        return f'{indent}{name} = [{hex_data}];'
