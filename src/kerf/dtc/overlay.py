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
        """Create FDT overlay blob."""
        import struct
        
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
        
        # Estimate size needed
        header_size = 40
        mem_rsv_size = 16  # Two 8-byte entries (terminator)
        
        struct_size = 0
        # Root node
        struct_size += 4 + 1 + 4  # FDT_BEGIN_NODE + name + FDT_END_NODE
        # Compatible property
        struct_size += 4 + 4 + len("linux,multikernel-overlay") + 1
        
        # For each new/modified instance, add a fragment
        for name, instance in {**new_instances, **modified_instances}.items():
            # Fragment node: __overlay__
            struct_size += 4 + 13 + 4  # FDT_BEGIN_NODE + "__overlay__" + FDT_END_NODE
            # Target path: instances
            struct_size += 4 + 4 + 4  # target-path property
            # Instance node
            struct_size += self._estimate_instance_node_size(instance)
            struct_size += 4  # FDT_END_NODE (end fragment)
        
        struct_size += 4  # FDT_END
        
        strings_size = 0
        totalsize = header_size + mem_rsv_size + struct_size + strings_size
        
        # Create FDT
        fdt_data = bytearray(totalsize)
        
        off_mem_rsvmap = header_size
        off_dt_struct = off_mem_rsvmap + mem_rsv_size
        off_dt_strings = off_dt_struct + struct_size
        
        # FDT header
        header = struct.pack('>IIIIIIIIII',
                           0xd00dfeed,      # magic
                           totalsize,       # totalsize
                           off_dt_struct,   # off_dt_struct
                           off_dt_strings,  # off_dt_strings
                           off_mem_rsvmap,  # off_mem_rsvmap
                           17,              # version
                           16,              # last_comp_version
                           0,               # boot_cpuid_phys
                           strings_size,    # size_dt_strings
                           struct_size)     # size_dt_struct
        
        fdt_data[:len(header)] = header
        
        # Memory reservation block (empty)
        fdt_data[off_mem_rsvmap:off_mem_rsvmap+16] = b'\x00' * 16
        
        # Build structure block
        struct_data = bytearray()
        
        # Root node
        struct_data.extend(struct.pack('>I', 0x00000001))  # FDT_BEGIN_NODE
        struct_data.extend(b'\x00')  # Empty name
        struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
        struct_data.extend(struct.pack('>I', len("linux,multikernel-overlay") + 1))
        struct_data.extend(b'compatible\x00')
        struct_data.extend(b'linux,multikernel-overlay\x00')
        
        # For each new/modified instance, add fragment
        for name, instance in {**new_instances, **modified_instances}.items():
            # Fragment node: __overlay__@<instance-name>
            fragment_name = f"__overlay__@{name}"
            struct_data.extend(struct.pack('>I', 0x00000001))  # FDT_BEGIN_NODE
            struct_data.extend(f"{fragment_name}\x00".encode())
            
            # Target path property: /instances
            struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
            struct_data.extend(struct.pack('>I', len("/instances") + 1))
            struct_data.extend(b'target-path\x00')
            struct_data.extend(b'/instances\x00')
            
            # Instance node
            self._add_instance_node_to_overlay(struct_data, name, instance)
            
            struct_data.extend(struct.pack('>I', 0x00000002))  # FDT_END_NODE
        
        struct_data.extend(struct.pack('>I', 0x00000002))  # FDT_END_NODE (root)
        struct_data.extend(struct.pack('>I', 0x00000009))  # FDT_END
        
        fdt_data[off_dt_struct:off_dt_struct+len(struct_data)] = struct_data
        
        return bytes(fdt_data)
    
    def _estimate_instance_node_size(self, instance: Instance) -> int:
        """Estimate size needed for instance node in overlay."""
        size = 0
        size += 4 + len(instance.name) + 1 + 4  # FDT_BEGIN_NODE + name + FDT_END_NODE
        
        # id property
        size += 4 + 4 + 4  # FDT_PROP + length + value
        
        # resources node
        size += 4 + 9 + 4  # FDT_BEGIN_NODE + "resources" + FDT_END_NODE
        
        # cpus property
        size += 4 + 4 + 4 + (4 * len(instance.resources.cpus))
        
        # memory-base property (u64)
        size += 4 + 4 + 8
        
        # memory-bytes property (u64)
        size += 4 + 4 + 8
        
        # devices property (if any)
        if instance.resources.devices:
            devices_str = " ".join(instance.resources.devices)
            size += 4 + 4 + len(devices_str) + 1
        
        return size
    
    def _add_instance_node_to_overlay(
        self,
        struct_data: bytearray,
        name: str,
        instance: Instance
    ):
        """Add instance node to overlay structure."""
        import struct
        
        # Instance node
        struct_data.extend(struct.pack('>I', 0x00000001))  # FDT_BEGIN_NODE
        struct_data.extend(f"{name}\x00".encode())
        
        # id property
        struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
        struct_data.extend(struct.pack('>I', 4))
        struct_data.extend(b'id\x00')
        struct_data.extend(struct.pack('>I', instance.id))
        
        # resources node
        struct_data.extend(struct.pack('>I', 0x00000001))  # FDT_BEGIN_NODE
        struct_data.extend(b'resources\x00')
        
        # cpus property
        cpus_data = b''.join(struct.pack('>I', cpu) for cpu in instance.resources.cpus)
        struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
        struct_data.extend(struct.pack('>I', len(cpus_data)))
        struct_data.extend(b'cpus\x00')
        struct_data.extend(cpus_data)
        
        # memory-base property (u64)
        memory_base = instance.resources.memory_base
        struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
        struct_data.extend(struct.pack('>I', 8))
        struct_data.extend(b'memory-base\x00')
        struct_data.extend(struct.pack('>Q', memory_base))
        
        # memory-bytes property (u64)
        memory_bytes = instance.resources.memory_bytes
        struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
        struct_data.extend(struct.pack('>I', 8))
        struct_data.extend(b'memory-bytes\x00')
        struct_data.extend(struct.pack('>Q', memory_bytes))
        
        # devices property (if any)
        if instance.resources.devices:
            devices_str = " ".join(instance.resources.devices)
            struct_data.extend(struct.pack('>I', 0x00000003))  # FDT_PROP
            struct_data.extend(struct.pack('>I', len(devices_str) + 1))
            struct_data.extend(b'devices\x00')
            struct_data.extend(f"{devices_str}\x00".encode())
        
        struct_data.extend(struct.pack('>I', 0x00000002))  # FDT_END_NODE (resources)
        
        struct_data.extend(struct.pack('>I', 0x00000002))  # FDT_END_NODE (instance)

