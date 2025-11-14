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

This module provides functionality to generate device tree overlay blobs (DTBO)
that represent incremental changes to the device tree state.
"""

import libfdt
from typing import Dict, Optional, List, Tuple
from ..models import GlobalDeviceTree, Instance
from ..exceptions import ParseError, ValidationError


class OverlayGenerator:
    """Generates device tree overlay blobs (DTBO) from state changes."""
    
    def __init__(self):
        self.fdt = None
    
    def generate_overlay(
        self,
        current: GlobalDeviceTree,
        modified: GlobalDeviceTree
    ) -> bytes:
        """
        Generate DTBO overlay representing changes from current to modified state.
        
        The overlay contains only the differences between current and modified states.
        It can add new instances, modify existing ones, or delete instances.
        
        Overlays MUST NOT modify resources (enforced by validation).
        
        Args:
            current: Current device tree state (before change)
            modified: Modified device tree state (after change)
            
        Returns:
            DTBO blob as bytes
            
        Raises:
            ValidationError: If overlay tries to modify resources
        """
        # Validate overlay doesn't modify resources
        if current.hardware != modified.hardware:
            raise ValidationError(
                "Overlay cannot modify hardware resources. "
                "Resources are defined in baseline only. "
                "Overlays can only modify instances."
            )
        
        # Create a minimal FDT for the overlay
        # Overlays use fragments and target references
        fdt = self._create_overlay_fdt(current, modified)
        return fdt
    
    def _create_overlay_fdt(
        self,
        current: GlobalDeviceTree,
        modified: GlobalDeviceTree
    ) -> bytes:
        """Create FDT overlay blob using FdtSw to match kernel format."""
        # Calculate what changed
        # New instances: in modified but not in current
        # Modified instances: in both but different
        # Deleted instances: in current but not in modified (handled via deletion)
        
        new_instances = {}
        modified_instances = {}
        deleted_instances = []
        
        for name, instance in modified.instances.items():
            if name not in current.instances:
                new_instances[name] = instance
            elif current.instances[name] != instance:
                modified_instances[name] = instance
        
        # Find deleted instances
        for name in current.instances:
            if name not in modified.instances:
                deleted_instances.append(name)
        
        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()
        
        fdt_sw.begin_node('')
        fdt_sw.property_string('compatible', 'linux,multikernel-overlay')
        
        # For each new/modified instance, add a fragment
        # Kernel expects fragment@0, fragment@1, etc.
        fragment_index = 0
        for name, instance in {**new_instances, **modified_instances}.items():
            # Fragment node: fragment@<index>
            fdt_sw.begin_node(f'fragment@{fragment_index}')
            
            fdt_sw.property_string('target-path', '/')
            fdt_sw.begin_node('__overlay__')
            fdt_sw.begin_node('instance-create')
            fdt_sw.property_string('instance-name', name)
            fdt_sw.property_u32('id', instance.id)
            fdt_sw.begin_node('resources')

            fdt_sw.property_u64('memory-bytes', instance.resources.memory_bytes)
            
            import struct
            cpus_data = struct.pack('>' + 'I' * len(instance.resources.cpus), *instance.resources.cpus)
            fdt_sw.property('cpus', cpus_data)
            
            if instance.resources.devices:
                fdt_sw.property_string('devices', ' '.join(instance.resources.devices))
            
            fdt_sw.end_node()  # End resources
            fdt_sw.end_node()  # End instance-create
            fdt_sw.end_node()  # End __overlay__
            fdt_sw.end_node()  # End fragment
            
            fragment_index += 1
        
        fdt_sw.end_node()  # End root
        
        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()
    

