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

import libfdt
from typing import Set
from ..models import GlobalDeviceTree


class OverlayGenerator:
    """Generates device tree overlay blobs (DTBO) from device tree model deltas."""
    
    def generate_overlay(
        self,
        current: GlobalDeviceTree,
        modified: GlobalDeviceTree
    ) -> bytes:
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
    
    def _create_overlay_dtb(
        self,
        instances_to_add: dict,
        instances_to_update: dict,
        instances_to_remove: Set[str]
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
        fdt_sw.begin_node('')
        fdt_sw.property_string('compatible', 'linux,multikernel-overlay')
        
        fragment_id = 0
        
        all_instances = {**instances_to_add, **instances_to_update}
        for name, instance in all_instances.items():
            fdt_sw.begin_node(f'fragment@{fragment_id}')
            fdt_sw.begin_node('__overlay__')
            fdt_sw.begin_node('instance-create')
            
            # Add instance properties
            fdt_sw.property_string('instance-name', name)
            fdt_sw.property_u32('id', instance.id)
            
            fdt_sw.begin_node('resources')
            
            import struct
            cpus_data = struct.pack('>' + 'I' * len(instance.resources.cpus), *instance.resources.cpus)
            fdt_sw.property('cpus', cpus_data)
            
            fdt_sw.property_u64('memory-base', instance.resources.memory_base)
            fdt_sw.property_u64('memory-bytes', instance.resources.memory_bytes)
            
            if instance.resources.devices:
                fdt_sw.property_string('device-names', ' '.join(instance.resources.devices))

            if instance.resources.numa_nodes:
                import struct
                numa_data = struct.pack('>' + 'I' * len(instance.resources.numa_nodes), *instance.resources.numa_nodes)
                fdt_sw.property('numa-nodes', numa_data)
            
            if instance.resources.cpu_affinity:
                fdt_sw.property_string('cpu-affinity', instance.resources.cpu_affinity)
            
            if instance.resources.memory_policy:
                fdt_sw.property_string('memory-policy', instance.resources.memory_policy)
            
            fdt_sw.end_node()  # End resources
            
            # Add options node if options exist
            if instance.options:
                fdt_sw.begin_node('options')
                
                # Add enable-host-kcore if enabled
                if instance.options.get('enable-host-kcore'):
                    fdt_sw.property('enable-host-kcore', b'')
                
                # Future options can be added here
                
                fdt_sw.end_node()  # End options
            
            fdt_sw.end_node()  # End instance-create
            fdt_sw.end_node()  # End __overlay__
            fdt_sw.end_node()  # End fragment
            fragment_id += 1
        
        for name in instances_to_remove:
            fdt_sw.begin_node(f'fragment@{fragment_id}')
            fdt_sw.begin_node('__overlay__')
            fdt_sw.begin_node('instance-remove')
            fdt_sw.property_string('instance-name', name)
            fdt_sw.end_node()  # End instance-remove
            fdt_sw.end_node()  # End __overlay__
            fdt_sw.end_node()  # End fragment
            fragment_id += 1
        
        fdt_sw.end_node()  # End root
        
        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()
    

