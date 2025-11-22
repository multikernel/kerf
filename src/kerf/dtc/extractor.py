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
DTB generation from device tree models.
"""

import libfdt
from typing import Dict, List, Optional
from ..models import GlobalDeviceTree, Instance
from ..exceptions import ParseError


class InstanceExtractor:
    """Generates device tree blobs (DTB) from device tree models."""
    
    def __init__(self):
        self.fdt = None
    
    def _create_minimal_fdt(self) -> bytes:
        """Create a minimal valid FDT structure."""
        # Create a minimal FDT with proper structure
        import struct
        
        # Calculate sizes
        header_size = 40  # FDT header is 40 bytes
        mem_rsv_size = 16  # Two 8-byte entries (terminator)
        struct_size = 16   # FDT_BEGIN_NODE + FDT_END_NODE + FDT_END
        strings_size = 0   # No strings for minimal FDT
        
        # Calculate offsets
        off_mem_rsvmap = header_size
        off_dt_struct = off_mem_rsvmap + mem_rsv_size
        off_dt_strings = off_dt_struct + struct_size
        totalsize = off_dt_strings + strings_size
        
        # Create FDT data
        fdt_data = bytearray(totalsize)
        
        # FDT header (40 bytes)
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
        
        # Memory reservation block (16 bytes - two 8-byte entries of zeros)
        fdt_data[off_mem_rsvmap:off_mem_rsvmap+16] = b'\x00' * 16
        
        # Structure block
        struct_data = b'\x00\x00\x00\x01'  # FDT_BEGIN_NODE
        struct_data += b'\x00'             # Root node name (empty)
        struct_data += b'\x00\x00\x00\x02' # FDT_END_NODE  
        struct_data += b'\x00\x00\x00\x09' # FDT_END
        
        fdt_data[off_dt_struct:off_dt_struct+len(struct_data)] = struct_data
        
        return bytes(fdt_data)
    
    def generate_global_dtb(self, tree: GlobalDeviceTree) -> bytes:
        """Generate global DTB from tree model."""
        # For production use, we'll create a comprehensive DTB that includes all the parsed data
        # This creates a proper device tree blob with hardware inventory, instances, and device references
        
        # Create a larger FDT structure to accommodate all the data
        fdt_data = self._create_comprehensive_fdt(tree)
        
        return fdt_data
    
    def _create_comprehensive_fdt(self, tree: GlobalDeviceTree) -> bytes:
        """Create a comprehensive FDT with all parsed data using libfdt FdtSw."""
        # Use libfdt's FdtSw (FDT source writer) to properly build the DTB
        # This ensures correct structure, size calculations, and string handling
        # FdtSw automatically resizes the buffer as needed
        
        fdt_sw = libfdt.FdtSw()
        fdt_sw.finish_reservemap()
        
        fdt_sw.begin_node('')
        fdt_sw.property_string('compatible', 'linux,multikernel-host')
        
        fdt_sw.begin_node('resources')
        self._add_cpu_properties_sw(fdt_sw, tree.hardware.cpus)
        self._add_memory_properties_sw(fdt_sw, tree.hardware.memory)
        
        if tree.hardware.devices:
            self._add_devices_section_sw(fdt_sw, tree.hardware.devices)
        
        fdt_sw.end_node()  # End resources
        
        if tree.instances:
            self._add_instances_section_sw(fdt_sw, tree.instances)
        
        if tree.device_references:
            self._add_device_references_sw(fdt_sw, tree.device_references)
        
        fdt_sw.end_node()
        
        dtb = fdt_sw.as_fdt()
        dtb.pack()
        return dtb.as_bytearray()
    
    def _add_cpu_properties_sw(self, fdt_sw, cpus):
        """Add CPU properties directly to resources node."""
        import struct
        available_data = struct.pack('>' + 'I' * len(cpus.available), *cpus.available)
        fdt_sw.property('cpus', available_data)
    
    def _add_memory_properties_sw(self, fdt_sw, memory):
        """Add memory properties directly to resources node."""
        fdt_sw.property_u64('memory-base', memory.memory_pool_base)
        fdt_sw.property_u64('memory-bytes', memory.memory_pool_bytes)
    
    def _add_devices_section_sw(self, fdt_sw, devices):
        """Add devices section using FdtSw."""
        fdt_sw.begin_node('devices')
        
        for name, device_info in devices.items():
            fdt_sw.begin_node(name)
            fdt_sw.property_string('compatible', device_info.compatible)
            
            if device_info.pci_id:
                fdt_sw.property_string('pci-id', device_info.pci_id)
            
            if device_info.vendor_id is not None:
                fdt_sw.property_u32('vendor-id', device_info.vendor_id)

            if device_info.device_id is not None:
                fdt_sw.property_u32('device-id', device_info.device_id)

            if device_info.sriov_vfs is not None:
                fdt_sw.property_u32('sriov-vfs', device_info.sriov_vfs)
            
            if device_info.host_reserved_vf is not None:
                fdt_sw.property_u32('host-reserved-vf', device_info.host_reserved_vf)
            
            if device_info.available_vfs:
                import struct
                vfs_data = struct.pack('>' + 'I' * len(device_info.available_vfs), *device_info.available_vfs)
                fdt_sw.property('available-vfs', vfs_data)
            
            if device_info.namespaces is not None:
                fdt_sw.property_u32('namespaces', device_info.namespaces)
            
            if device_info.host_reserved_ns is not None:
                fdt_sw.property_u32('host-reserved-ns', device_info.host_reserved_ns)
            
            if device_info.available_ns:
                import struct
                ns_data = struct.pack('>' + 'I' * len(device_info.available_ns), *device_info.available_ns)
                fdt_sw.property('available-ns', ns_data)
            
            fdt_sw.end_node()
        
        fdt_sw.end_node()
    
    def _add_instances_section_sw(self, fdt_sw, instances):
        """Add instances section using FdtSw."""
        fdt_sw.begin_node('instances')
        
        for name, instance in instances.items():
            fdt_sw.begin_node(name)
            fdt_sw.property_u32('id', instance.id)
            
            fdt_sw.begin_node('resources')
            
            import struct
            cpus_data = struct.pack('>' + 'I' * len(instance.resources.cpus), *instance.resources.cpus)
            fdt_sw.property('cpus', cpus_data)
            
            fdt_sw.property_u64('memory-base', instance.resources.memory_base)
            fdt_sw.property_u64('memory-bytes', instance.resources.memory_bytes)
            
            if instance.resources.devices:
                fdt_sw.property_string('devices', ' '.join(instance.resources.devices))
            
            fdt_sw.end_node()  # End resources
            fdt_sw.end_node()  # End instance
        
        fdt_sw.end_node()  # End instances
    
    def _add_device_references_sw(self, fdt_sw, device_references):
        """Add device references using FdtSw."""
        for name, device_ref in device_references.items():
            fdt_sw.begin_node(name)
            
            if isinstance(device_ref, dict):
                if 'parent' in device_ref and device_ref['parent']:
                    fdt_sw.property_string('parent', device_ref['parent'])
                if 'vf_id' in device_ref and device_ref['vf_id'] is not None:
                    fdt_sw.property_u32('vf-id', device_ref['vf_id'])
                if 'namespace_id' in device_ref and device_ref['namespace_id'] is not None:
                    fdt_sw.property_u32('namespace-id', device_ref['namespace_id'])
            else:
                if hasattr(device_ref, 'parent') and device_ref.parent:
                    fdt_sw.property_string('parent', device_ref.parent)
                if hasattr(device_ref, 'vf_id') and device_ref.vf_id is not None:
                    fdt_sw.property_u32('vf-id', device_ref.vf_id)
                if hasattr(device_ref, 'namespace_id') and device_ref.namespace_id is not None:
                    fdt_sw.property_u32('namespace-id', device_ref.namespace_id)
            
            fdt_sw.end_node()
    
    def _add_resources_section(self, parent_offset: int, tree: GlobalDeviceTree):
        """Add resources section to DTB."""
        resources_offset = self.fdt.add_subnode(parent_offset, "resources")
        
        # Add CPU information
        self._add_cpu_section(resources_offset, tree.hardware.cpus)
        
        # Add memory information
        self._add_memory_section(resources_offset, tree.hardware.memory)
        
        # Add device information
        self._add_devices_section(resources_offset, tree.hardware.devices)
    
    def _add_cpu_section(self, parent_offset: int, cpus):
        """Add CPU section to DTB."""
        cpus_offset = self.fdt.add_subnode(parent_offset, "cpus")
        self.fdt.setprop_u32(cpus_offset, "total", cpus.total)
        
        # Convert lists to proper format for FDT
        import struct
        host_reserved_data = struct.pack('>' + 'I' * len(cpus.host_reserved), *cpus.host_reserved)
        self.fdt.setprop(cpus_offset, "host-reserved", host_reserved_data)
        
        available_data = struct.pack('>' + 'I' * len(cpus.available), *cpus.available)
        self.fdt.setprop(cpus_offset, "available", available_data)
    
    def _add_memory_section(self, parent_offset: int, memory):
        """Add memory section to DTB."""
        memory_offset = self.fdt.add_subnode(parent_offset, "memory")
        self.fdt.setprop_u64(memory_offset, "total-bytes", memory.total_bytes)
        self.fdt.setprop_u64(memory_offset, "host-reserved-bytes", memory.host_reserved_bytes)
        self.fdt.setprop_u64(memory_offset, "memory-pool-base", memory.memory_pool_base)
        self.fdt.setprop_u64(memory_offset, "memory-pool-bytes", memory.memory_pool_bytes)
    
    def _add_devices_section(self, parent_offset: int, devices):
        """Add devices section to DTB."""
        devices_offset = self.fdt.add_subnode(parent_offset, "devices")
        
        for name, device_info in devices.items():
            device_offset = self.fdt.add_subnode(devices_offset, name)
            self.fdt.setprop_str(device_offset, "compatible", device_info.compatible)
            
            if device_info.pci_id:
                self.fdt.setprop_str(device_offset, "pci-id", device_info.pci_id)
            
            if device_info.sriov_vfs is not None:
                self.fdt.setprop_u32(device_offset, "sriov-vfs", device_info.sriov_vfs)
            
            if device_info.host_reserved_vf is not None:
                self.fdt.setprop_u32(device_offset, "host-reserved-vf", device_info.host_reserved_vf)
            
            if device_info.available_vfs:
                import struct
                vfs_data = struct.pack('>' + 'I' * len(device_info.available_vfs), *device_info.available_vfs)
                self.fdt.setprop(device_offset, "available-vfs", vfs_data)
            
            if device_info.namespaces is not None:
                self.fdt.setprop_u32(device_offset, "namespaces", device_info.namespaces)
            
            if device_info.host_reserved_ns is not None:
                self.fdt.setprop_u32(device_offset, "host-reserved-ns", device_info.host_reserved_ns)
            
            if device_info.available_ns:
                import struct
                ns_data = struct.pack('>' + 'I' * len(device_info.available_ns), *device_info.available_ns)
                self.fdt.setprop(device_offset, "available-ns", ns_data)
    
    def _add_instances_section(self, parent_offset: int, tree: GlobalDeviceTree):
        """Add instances section to DTB."""
        instances_offset = self.fdt.add_subnode(parent_offset, "instances")
        
        for name, instance in tree.instances.items():
            instance_offset = self.fdt.add_subnode(instances_offset, name)
            self.fdt.setprop_u32(instance_offset, "id", instance.id)
            
            # Add resources section
            self._add_instance_resources(instance_offset, instance)
            
    
    def _add_instance_resources(self, parent_offset: int, instance: Instance):
        """Add instance resources section."""
        resources_offset = self.fdt.add_subnode(parent_offset, "resources")
        
        # Convert CPU list to proper format
        import struct
        cpus_data = struct.pack('>' + 'I' * len(instance.resources.cpus), *instance.resources.cpus)
        self.fdt.setprop(resources_offset, "cpus", cpus_data)
        
        self.fdt.setprop_u64(resources_offset, "memory-base", instance.resources.memory_base)
        self.fdt.setprop_u64(resources_offset, "memory-bytes", instance.resources.memory_bytes)
        
        if instance.resources.devices:
            # This would need proper phandle handling
            self.fdt.setprop_str(resources_offset, "devices", " ".join(instance.resources.devices))
    
    
    def _add_device_references(self, parent_offset: int, tree: GlobalDeviceTree):
        """Add device reference nodes."""

        for name, device_ref in tree.device_references.items():
            device_ref_offset = self.fdt.add_subnode(parent_offset, name)
            
            # Add parent phandle reference
            if hasattr(device_ref, 'parent') and device_ref.parent:
                # This would need proper phandle handling in a full implementation
                self.fdt.setprop_str(device_ref_offset, "parent", device_ref.parent)
            
            # Add device-specific properties
            if hasattr(device_ref, 'vf_id') and device_ref.vf_id is not None:
                self.fdt.setprop_u32(device_ref_offset, "vf-id", device_ref.vf_id)
            
            if hasattr(device_ref, 'namespace_id') and device_ref.namespace_id is not None:
                self.fdt.setprop_u32(device_ref_offset, "namespace-id", device_ref.namespace_id)
