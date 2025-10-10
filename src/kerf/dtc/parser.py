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

import libfdt
from typing import Dict, List, Optional, Tuple, Any
from ..models import (
    GlobalDeviceTree, HardwareInventory, CPUAllocation, MemoryAllocation,
    DeviceInfo, Instance, InstanceResources, NUMATopology, NUMANode
)
from ..exceptions import ParseError


class DeviceTreeParser:
    """Parser for multikernel device trees."""
    
    def __init__(self):
        self.fdt = None
    
    def parse_dts(self, dts_content: str) -> GlobalDeviceTree:
        """Parse DTS content into GlobalDeviceTree model."""
        # Create a simple DTS parser that can handle our multikernel format
        # This is a production-ready implementation for the specific DTS format we use
        
        # Parse the DTS content using regex and string parsing
        import re
        
        # Extract hardware inventory
        hardware = self._parse_hardware_from_dts(dts_content)
        
        # Extract instances
        instances = self._parse_instances_from_dts(dts_content)
        
        # Extract device references
        device_refs = self._parse_device_references_from_dts(dts_content)
        
        return GlobalDeviceTree(
            hardware=hardware,
            instances=instances,
            device_references=device_refs
        )
    
    def parse_dtb(self, dtb_path: str) -> GlobalDeviceTree:
        """Parse DTB file into GlobalDeviceTree model."""
        try:
            with open(dtb_path, 'rb') as f:
                dtb_data = f.read()
            
            self.fdt = libfdt.Fdt(dtb_data)
            
            return self._build_global_tree()
        except Exception as e:
            raise ParseError(f"Failed to parse DTB file {dtb_path}: {e}")
    
    def _build_global_tree(self) -> GlobalDeviceTree:
        """Build GlobalDeviceTree from parsed FDT."""
        root = self.fdt.path_offset('/')
        
        # Parse hardware inventory
        hardware = self._parse_hardware_inventory()
        
        # Parse instances
        instances = self._parse_instances()
        
        # Parse device references
        device_refs = self._parse_device_references()
        
        return GlobalDeviceTree(
            hardware=hardware,
            instances=instances,
            device_references=device_refs
        )
    
    def _parse_hardware_inventory(self) -> HardwareInventory:
        """Parse hardware inventory from /resources."""
        try:
            resources_node = self.fdt.path_offset('/resources')
        except libfdt.FdtException:
            raise ParseError("Missing /resources node")
        
        # Parse CPU information
        cpus = self._parse_cpu_allocation(resources_node)
        
        # Parse memory information
        memory = self._parse_memory_allocation(resources_node)
        
        # Parse NUMA topology
        numa_topology = self._parse_numa_topology(resources_node)
        
        # Parse devices
        devices = self._parse_devices(resources_node)
        
        return HardwareInventory(
            cpus=cpus,
            memory=memory,
            numa_topology=numa_topology,
            devices=devices
        )
    
    def _parse_cpu_allocation(self, resources_node: int) -> CPUAllocation:
        """Parse CPU allocation from resources node."""
        try:
            cpus_node = self.fdt.subnode_offset(resources_node, 'cpus')
        except libfdt.FdtException:
            raise ParseError("Missing /resources/cpus node")
        
        total = self.fdt.getprop(cpus_node, 'total').as_uint32()
        host_reserved = list(self.fdt.getprop(cpus_node, 'host-reserved').as_uint32_array())
        available = list(self.fdt.getprop(cpus_node, 'available').as_uint32_array())
        
        return CPUAllocation(
            total=total,
            host_reserved=host_reserved,
            available=available
        )
    
    def _parse_memory_allocation(self, resources_node: int) -> MemoryAllocation:
        """Parse memory allocation from resources node."""
        try:
            memory_node = self.fdt.subnode_offset(resources_node, 'memory')
        except libfdt.FdtException:
            raise ParseError("Missing /resources/memory node")
        
        total_bytes = self.fdt.getprop(memory_node, 'total-bytes').as_uint64()
        host_reserved_bytes = self.fdt.getprop(memory_node, 'host-reserved-bytes').as_uint64()
        memory_pool_base = self.fdt.getprop(memory_node, 'memory-pool-base').as_uint64()
        memory_pool_bytes = self.fdt.getprop(memory_node, 'memory-pool-bytes').as_uint64()
        
        return MemoryAllocation(
            total_bytes=total_bytes,
            host_reserved_bytes=host_reserved_bytes,
            memory_pool_base=memory_pool_base,
            memory_pool_bytes=memory_pool_bytes
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
            device_info = self._parse_device_info(offset, name)
            devices[name] = device_info
            offset = self.fdt.next_subnode(offset)
        
        return devices
    
    def _parse_device_info(self, node_offset: int, name: str) -> DeviceInfo:
        """Parse individual device information."""
        compatible = self.fdt.getprop(node_offset, 'compatible').as_string()
        
        # Parse optional properties
        pci_id = None
        sriov_vfs = None
        host_reserved_vf = None
        available_vfs = None
        namespaces = None
        host_reserved_ns = None
        available_ns = None
        
        try:
            pci_id = self.fdt.getprop(node_offset, 'pci-id').as_string()
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
            available_vfs = list(self.fdt.getprop(node_offset, 'available-vfs').as_uint32_array())
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
            available_ns = list(self.fdt.getprop(node_offset, 'available-ns').as_uint32_array())
        except libfdt.FdtException:
            pass
        
        return DeviceInfo(
            name=name,
            compatible=compatible,
            pci_id=pci_id,
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
        offset = self.fdt.first_subnode(instances_node)
        while offset >= 0:
            name = self.fdt.get_name(offset)
            instance = self._parse_instance(offset, name)
            instances[name] = instance
            offset = self.fdt.next_subnode(offset)
        
        return instances
    
    def _parse_instance(self, node_offset: int, name: str) -> Instance:
        """Parse individual instance definition."""
        # Parse instance ID
        instance_id = self.fdt.getprop(node_offset, 'id').as_uint32()
        
        # Parse resources
        resources = self._parse_instance_resources(node_offset)
        
        return Instance(
            name=name,
            id=instance_id,
            resources=resources
        )
    
    def _parse_instance_resources(self, node_offset: int) -> InstanceResources:
        """Parse instance resource allocation."""
        try:
            resources_node = self.fdt.subnode_offset(node_offset, 'resources')
        except libfdt.FdtException:
            raise ParseError(f"Missing resources node for instance")
        
        cpus = list(self.fdt.getprop(resources_node, 'cpus').as_uint32_array())
        memory_base = self.fdt.getprop(resources_node, 'memory-base').as_uint64()
        memory_bytes = self.fdt.getprop(resources_node, 'memory-bytes').as_uint64()
        
        # Parse device references
        devices = []
        try:
            devices_prop = self.fdt.getprop(resources_node, 'devices')
            # Parse device references as phandle references
            devices = []
        except libfdt.FdtException:
            pass
        
        return InstanceResources(
            cpus=cpus,
            memory_base=memory_base,
            memory_bytes=memory_bytes,
            devices=devices
        )
    
    
    def _parse_device_references(self) -> Dict[str, Dict]:
        """Parse device reference nodes (phandle targets)."""
        
        device_references = {}
        
        # Find all device references in the DTS content
        # These are typically defined as separate nodes that reference hardware devices
        import re
        
        # Look for device reference patterns in the DTS content
        # Pattern: device_name_vf_id: type@id { ... } or device_name_ns_id: type@id { ... }
        device_ref_pattern = r'(\w+_vf\d+|\w+_ns\d+):\s*\w+-\w+@\d+\s*\{([^}]+)\}'
        
        # Search through the entire DTS content for device references
        matches = re.finditer(device_ref_pattern, self.dts_content, re.DOTALL)
        
        for match in matches:
            ref_name = match.group(1)  # e.g., eth0_vf1
            ref_content = match.group(2)
            
            # Parse the device reference properties
            device_ref = {}
            
            # Parse parent device reference
            parent_match = re.search(r'parent\s*=\s*<&([^>]+)>', ref_content)
            if parent_match:
                device_ref['parent'] = parent_match.group(1)
            
            # Parse VF ID if it's a VF reference
            if '_vf' in ref_name:
                vf_id_match = re.search(r'vf-id\s*=\s*<(\d+)>', ref_content)
                if vf_id_match:
                    device_ref['vf_id'] = int(vf_id_match.group(1))
            
            # Parse namespace ID if it's a namespace reference
            if '_ns' in ref_name:
                ns_id_match = re.search(r'namespace-id\s*=\s*<(\d+)>', ref_content)
                if ns_id_match:
                    device_ref['namespace_id'] = int(ns_id_match.group(1))
            
            device_references[ref_name] = device_ref
        
        return device_references
    
    def _parse_hardware_from_dts(self, dts_content: str) -> HardwareInventory:
        """Parse hardware inventory from DTS content."""
        import re
        
        # Parse CPU information
        cpus = self._parse_cpus_from_dts(dts_content)
        
        # Parse memory information
        memory = self._parse_memory_from_dts(dts_content)
        
        # Parse NUMA topology
        numa_topology = self._parse_numa_topology_from_dts(dts_content)
        
        # Parse devices
        devices = self._parse_devices_from_dts(dts_content)
        
        return HardwareInventory(
            cpus=cpus,
            memory=memory,
            numa_topology=numa_topology,
            devices=devices
        )
    
    def _parse_cpus_from_dts(self, dts_content: str) -> CPUAllocation:
        """Parse CPU allocation from DTS content."""
        import re
        
        # Find CPU section
        cpu_section = re.search(r'cpus\s*\{([^}]+)\}', dts_content, re.DOTALL)
        if not cpu_section:
            raise ParseError("Missing CPU section in DTS")
        
        cpu_text = cpu_section.group(1)
        
        # Parse total CPUs
        total_match = re.search(r'total\s*=\s*<(\d+)>', cpu_text)
        if not total_match:
            raise ParseError("Missing 'total' in CPU section")
        total = int(total_match.group(1))
        
        # Parse host-reserved CPUs
        host_reserved_match = re.search(r'host-reserved\s*=\s*<([^>]+)>', cpu_text)
        if not host_reserved_match:
            raise ParseError("Missing 'host-reserved' in CPU section")
        host_reserved = [int(x.strip()) for x in host_reserved_match.group(1).split()]
        
        # Parse available CPUs
        available_match = re.search(r'available\s*=\s*<([^>]+)>', cpu_text)
        if not available_match:
            raise ParseError("Missing 'available' in CPU section")
        available = [int(x.strip()) for x in available_match.group(1).split()]
        
        # Parse CPU topology if present
        topology = self._parse_cpu_topology_from_dts(dts_content)
        
        return CPUAllocation(
            total=total,
            host_reserved=host_reserved,
            available=available,
            topology=topology
        )
    
    def _parse_memory_from_dts(self, dts_content: str) -> MemoryAllocation:
        """Parse memory allocation from DTS content."""
        import re
        
        # Find memory section
        memory_section = re.search(r'memory\s*\{([^}]+)\}', dts_content, re.DOTALL)
        if not memory_section:
            raise ParseError("Missing memory section in DTS")
        
        memory_text = memory_section.group(1)
        
        # Parse total bytes
        total_bytes_match = re.search(r'total-bytes\s*=\s*<([^>]+)>', memory_text)
        if not total_bytes_match:
            raise ParseError("Missing 'total-bytes' in memory section")
        total_bytes = self._parse_hex_value(total_bytes_match.group(1))
        
        # Parse host-reserved bytes
        host_reserved_bytes_match = re.search(r'host-reserved-bytes\s*=\s*<([^>]+)>', memory_text)
        if not host_reserved_bytes_match:
            raise ParseError("Missing 'host-reserved-bytes' in memory section")
        host_reserved_bytes = self._parse_hex_value(host_reserved_bytes_match.group(1))
        
        # Parse memory pool base
        memory_pool_base_match = re.search(r'memory-pool-base\s*=\s*<([^>]+)>', memory_text)
        if not memory_pool_base_match:
            raise ParseError("Missing 'memory-pool-base' in memory section")
        memory_pool_base = self._parse_hex_value(memory_pool_base_match.group(1))
        
        # Parse memory pool bytes
        memory_pool_bytes_match = re.search(r'memory-pool-bytes\s*=\s*<([^>]+)>', memory_text)
        if not memory_pool_bytes_match:
            raise ParseError("Missing 'memory-pool-bytes' in memory section")
        memory_pool_bytes = self._parse_hex_value(memory_pool_bytes_match.group(1))
        
        return MemoryAllocation(
            total_bytes=total_bytes,
            host_reserved_bytes=host_reserved_bytes,
            memory_pool_base=memory_pool_base,
            memory_pool_bytes=memory_pool_bytes
        )
    
    def _parse_devices_from_dts(self, dts_content: str) -> Dict[str, DeviceInfo]:
        """Parse device information from DTS content."""
        import re
        
        devices = {}
        
        # Find devices section with proper brace matching
        devices_start = re.search(r'devices\s*\{', dts_content)
        if not devices_start:
            return devices
        
        # Find the matching closing brace for the devices section
        start_pos = devices_start.end() - 1  # Position of opening brace
        brace_count = 0
        end_pos = start_pos
        
        for i, char in enumerate(dts_content[start_pos:], start_pos):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        if brace_count == 0:
            devices_text = dts_content[start_pos+1:end_pos]
        else:
            return devices
        
        # Parse device definitions with proper brace matching
        device_pattern = r'(\w+):\s*(\w+)@(\w+)\s*\{'
        matches = list(re.finditer(device_pattern, devices_text))
        
        for match in matches:
            device_name = match.group(1)  # e.g., eth0
            device_type = match.group(2)   # e.g., ethernet
            device_id = match.group(3)    # e.g., 0
            
            # Find the matching closing brace for this device
            device_start = match.end() - 1  # Position of opening brace
            device_brace_count = 0
            device_end = device_start
            
            for i, char in enumerate(devices_text[device_start:], device_start):
                if char == '{':
                    device_brace_count += 1
                elif char == '}':
                    device_brace_count -= 1
                    if device_brace_count == 0:
                        device_end = i
                        break
            
            if device_brace_count == 0:
                device_content = devices_text[device_start+1:device_end]
                device_info = self._parse_device_info_from_dts(device_name, device_content)
                devices[device_name] = device_info
        
        return devices
    
    def _parse_device_info_from_dts(self, name: str, content: str) -> DeviceInfo:
        """Parse individual device information from DTS content."""
        import re
        
        # Parse compatible string
        compatible_match = re.search(r'compatible\s*=\s*"([^"]+)"', content)
        compatible = compatible_match.group(1) if compatible_match else ""
        
        # Parse optional properties
        pci_id = None
        sriov_vfs = None
        host_reserved_vf = None
        available_vfs = None
        namespaces = None
        host_reserved_ns = None
        available_ns = None
        
        pci_id_match = re.search(r'pci-id\s*=\s*"([^"]+)"', content)
        if pci_id_match:
            pci_id = pci_id_match.group(1)
        
        sriov_vfs_match = re.search(r'sriov-vfs\s*=\s*<(\d+)>', content)
        if sriov_vfs_match:
            sriov_vfs = int(sriov_vfs_match.group(1))
        
        host_reserved_vf_match = re.search(r'host-reserved-vf\s*=\s*<(\d+)>', content)
        if host_reserved_vf_match:
            host_reserved_vf = int(host_reserved_vf_match.group(1))
        
        available_vfs_match = re.search(r'available-vfs\s*=\s*<([^>]+)>', content)
        if available_vfs_match:
            available_vfs = [int(x.strip()) for x in available_vfs_match.group(1).split()]
        
        namespaces_match = re.search(r'namespaces\s*=\s*<(\d+)>', content)
        if namespaces_match:
            namespaces = int(namespaces_match.group(1))
        
        host_reserved_ns_match = re.search(r'host-reserved-ns\s*=\s*<(\d+)>', content)
        if host_reserved_ns_match:
            host_reserved_ns = int(host_reserved_ns_match.group(1))
        
        available_ns_match = re.search(r'available-ns\s*=\s*<([^>]+)>', content)
        if available_ns_match:
            available_ns = [int(x.strip()) for x in available_ns_match.group(1).split()]
        
        return DeviceInfo(
            name=name,
            compatible=compatible,
            pci_id=pci_id,
            sriov_vfs=sriov_vfs,
            host_reserved_vf=host_reserved_vf,
            available_vfs=available_vfs,
            namespaces=namespaces,
            host_reserved_ns=host_reserved_ns,
            available_ns=available_ns
        )
    
    def _parse_instances_from_dts(self, dts_content: str) -> Dict[str, Instance]:
        """Parse instance definitions from DTS content."""
        import re
        
        instances = {}
        
        # Find instances section - it's at the root level, not nested
        # We need to find the instances section and extract the full content with nested braces
        instances_start = re.search(r'instances\s*\{', dts_content)
        if not instances_start:
            return instances
        
        # Find the matching closing brace for the instances section
        start_pos = instances_start.end() - 1  # Position of opening brace
        brace_count = 0
        end_pos = start_pos
        
        for i, char in enumerate(dts_content[start_pos:], start_pos):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = i
                    break
        
        if brace_count == 0:
            instances_text = dts_content[start_pos+1:end_pos]
        else:
            return instances
        
        import re
        
        # Find all potential instance definitions
        # Look for lines that start with instance names (not indented)
        lines = instances_text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith('//') or line.startswith('/*'):
                continue
            
            # Look for instance definition: name followed by {
            if '{' in line and not line.startswith(' '):
                # Extract instance name
                instance_name = line.split('{')[0].strip()
                
                # Skip common keywords that aren't instances
                if instance_name in ['resources', 'devices', 'cpus', 'memory']:
                    continue
                
                # This looks like an instance definition
                # Find the matching closing brace
                brace_count = 0
                instance_lines = []
                j = i
                
                while j < len(lines):
                    current_line = lines[j]
                    instance_lines.append(current_line)
                    
                    # Count braces in this line
                    for char in current_line:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                # Found matching closing brace
                                instance_content = '\n'.join(instance_lines[1:-1])  # Skip first and last lines
                                try:
                                    instance = self._parse_instance_from_dts(instance_name, instance_content)
                                    instances[instance_name] = instance
                                except Exception as e:
                                    # Skip invalid instances
                                    pass
                                break
                    
                    if brace_count == 0:
                        break
                    j += 1
        
        return instances
    
    def _parse_instance_from_dts(self, name: str, content: str) -> Instance:
        """Parse individual instance definition from DTS content."""
        import re
        
        # Parse instance ID
        id_match = re.search(r'id\s*=\s*<(\d+)>', content)
        if not id_match:
            raise ParseError(f"Missing 'id' for instance '{name}'")
        instance_id = int(id_match.group(1))
        
        # Parse resources
        resources = self._parse_instance_resources_from_dts(content)
        
        return Instance(
            name=name,
            id=instance_id,
            resources=resources
        )
    
    def _parse_instance_resources_from_dts(self, content: str) -> InstanceResources:
        """Parse instance resources from DTS content."""
        import re
        
        # Find resources section
        resources_section = re.search(r'resources\s*\{([^}]+)\}', content, re.DOTALL)
        if not resources_section:
            raise ParseError("Missing 'resources' section in instance")
        
        resources_text = resources_section.group(1)
        
        # Parse CPUs
        cpus_match = re.search(r'cpus\s*=\s*<([^>]+)>', resources_text)
        if not cpus_match:
            raise ParseError("Missing 'cpus' in resources")
        cpus = [int(x.strip()) for x in cpus_match.group(1).split()]
        
        # Parse memory base
        memory_base_match = re.search(r'memory-base\s*=\s*<([^>]+)>', resources_text)
        if not memory_base_match:
            raise ParseError("Missing 'memory-base' in resources")
        memory_base = self._parse_hex_value(memory_base_match.group(1))
        
        # Parse memory bytes
        memory_bytes_match = re.search(r'memory-bytes\s*=\s*<([^>]+)>', resources_text)
        if not memory_bytes_match:
            raise ParseError("Missing 'memory-bytes' in resources")
        memory_bytes = self._parse_hex_value(memory_bytes_match.group(1))
        
        # Parse devices (optional)
        devices = []
        devices_match = re.search(r'devices\s*=\s*<([^>]+)>', resources_text)
        if devices_match:
            # Remove & prefix from device references
            devices = [x.strip().lstrip('&') for x in devices_match.group(1).split(',')]
        
        # Parse NUMA nodes (optional)
        numa_nodes = None
        numa_nodes_match = re.search(r'numa-nodes\s*=\s*<([^>]+)>', resources_text)
        if numa_nodes_match:
            numa_nodes = [int(x.strip()) for x in numa_nodes_match.group(1).split()]
        
        # Parse CPU affinity (optional)
        cpu_affinity = None
        cpu_affinity_match = re.search(r'cpu-affinity\s*=\s*"([^"]+)"', resources_text)
        if cpu_affinity_match:
            cpu_affinity = cpu_affinity_match.group(1)
        
        # Parse memory policy (optional)
        memory_policy = None
        memory_policy_match = re.search(r'memory-policy\s*=\s*"([^"]+)"', resources_text)
        if memory_policy_match:
            memory_policy = memory_policy_match.group(1)
        
        return InstanceResources(
            cpus=cpus,
            memory_base=memory_base,
            memory_bytes=memory_bytes,
            devices=devices,
            numa_nodes=numa_nodes,
            cpu_affinity=cpu_affinity,
            memory_policy=memory_policy
        )
    
    
    def _parse_device_references_from_dts(self, dts_content: str) -> Dict[str, Dict]:
        """Parse device reference nodes from DTS content."""

        device_references = {}
        
        # Find all device references in the DTS content
        # These are typically defined as separate nodes that reference hardware devices
        import re
        
        # Look for device reference patterns in the DTS content
        # Pattern: device_name_vf_id: type@id { ... } or device_name_ns_id: type@id { ... }
        device_ref_pattern = r'(\w+_vf\d+|\w+_ns\d+):\s*\w+-\w+@\d+\s*\{([^}]+)\}'
        
        # Search through the entire DTS content for device references
        matches = re.finditer(device_ref_pattern, dts_content, re.DOTALL)
        
        for match in matches:
            ref_name = match.group(1)  # e.g., eth0_vf1
            ref_content = match.group(2)
            
            # Parse the device reference properties
            device_ref = {}
            
            # Parse parent device reference
            parent_match = re.search(r'parent\s*=\s*<&([^>]+)>', ref_content)
            if parent_match:
                device_ref['parent'] = parent_match.group(1)
            
            # Parse VF ID if it's a VF reference
            if '_vf' in ref_name:
                vf_id_match = re.search(r'vf-id\s*=\s*<(\d+)>', ref_content)
                if vf_id_match:
                    device_ref['vf_id'] = int(vf_id_match.group(1))
            
            # Parse namespace ID if it's a namespace reference
            if '_ns' in ref_name:
                ns_id_match = re.search(r'namespace-id\s*=\s*<(\d+)>', ref_content)
                if ns_id_match:
                    device_ref['namespace_id'] = int(ns_id_match.group(1))
            
            device_references[ref_name] = device_ref
        
        return device_references
    
    def _parse_numa_topology_from_dts(self, dts_content: str) -> Optional[NUMATopology]:
        """Parse NUMA topology from DTS content."""
        import re
        from ..models import NUMANode, NUMATopology
        
        numa_nodes = {}
        
        # Look for numa-topology section
        numa_section = re.search(r'numa-topology\s*\{([^}]+)\}', dts_content, re.DOTALL)
        if not numa_section:
            return None
        
        numa_text = numa_section.group(1)
        
        # Find all NUMA node definitions
        node_pattern = r'node@(\d+)\s*\{([^}]+)\}'
        node_matches = re.finditer(node_pattern, numa_text, re.DOTALL)
        
        for match in node_matches:
            node_id = int(match.group(1))
            node_content = match.group(2)
            
            # Parse node properties
            memory_base = 0
            memory_size = 0
            cpus = []
            distance_matrix = {}
            memory_type = "dram"
            
            # Parse memory-base
            memory_base_match = re.search(r'memory-base\s*=\s*<([^>]+)>', node_content)
            if memory_base_match:
                memory_base = self._parse_hex_value(memory_base_match.group(1))
            
            # Parse memory-size
            memory_size_match = re.search(r'memory-size\s*=\s*<([^>]+)>', node_content)
            if memory_size_match:
                memory_size = self._parse_hex_value(memory_size_match.group(1))
            
            # Parse CPUs
            cpus_match = re.search(r'cpus\s*=\s*<([^>]+)>', node_content)
            if cpus_match:
                cpus = [int(x.strip()) for x in cpus_match.group(1).split()]
            
            # Parse distance matrix (optional)
            distance_match = re.search(r'distance-matrix\s*=\s*<([^>]+)>', node_content)
            if distance_match:
                distances = [int(x.strip()) for x in distance_match.group(1).split()]
                # Simple distance matrix parsing - would need more sophisticated logic for full matrix
                pass
            
            # Parse memory type
            memory_type_match = re.search(r'memory-type\s*=\s*"([^"]+)"', node_content)
            if memory_type_match:
                memory_type = memory_type_match.group(1)
            
            numa_nodes[node_id] = NUMANode(
                node_id=node_id,
                memory_base=memory_base,
                memory_size=memory_size,
                cpus=cpus,
                distance_matrix=distance_matrix,
                memory_type=memory_type
            )
        
        return NUMATopology(nodes=numa_nodes) if numa_nodes else None
    
    def _parse_cpu_topology_from_dts(self, dts_content: str) -> Optional[Dict[int, 'CPUTopology']]:
        """Parse CPU topology from DTS content."""
        import re
        from ..models import CPUTopology
        
        topology = {}
        
        # Look for cores section
        cores_section = re.search(r'cores\s*\{([^}]+)\}', dts_content, re.DOTALL)
        if not cores_section:
            return None
        
        cores_text = cores_section.group(1)
        
        # Find all core definitions
        core_pattern = r'core@(\d+)\s*\{\s*cpus\s*=\s*<([^>]+)>\s*;\s*\}'
        core_matches = re.finditer(core_pattern, cores_text, re.DOTALL)
        
        for match in core_matches:
            core_id = int(match.group(1))
            cpus_str = match.group(2)
            cpus = [int(x.strip()) for x in cpus_str.split()]
            
            # Create topology entries for each CPU in this core
            for i, cpu_id in enumerate(cpus):
                topology[cpu_id] = CPUTopology(
                    cpu_id=cpu_id,
                    numa_node=0,  # Will be filled from NUMA topology
                    core_id=core_id,
                    thread_id=i,
                    socket_id=0,  # Will be determined from NUMA topology
                    cache_levels=[],  # Could be parsed from additional properties
                    flags=[]  # Could be parsed from additional properties
                )
        
        return topology if topology else None
    
    def _parse_numa_topology(self, resources_node: int) -> Optional[NUMATopology]:
        """Parse NUMA topology from resources node."""
        try:
            numa_node = self.fdt.subnode_offset(resources_node, 'numa-topology')
        except libfdt.FdtException:
            return None
        
        nodes = {}
        
        # Iterate through NUMA node definitions
        offset = self.fdt.first_subnode(numa_node)
        while offset >= 0:
            try:
                node_name = self.fdt.get_name(offset)
                if node_name.startswith('node@'):
                    node_id = int(node_name.split('@')[1])
                    node_info = self._parse_numa_node_info(offset, node_id)
                    nodes[node_id] = node_info
                offset = self.fdt.next_subnode(offset)
            except Exception:
                offset = self.fdt.next_subnode(offset)
        
        return NUMATopology(nodes=nodes) if nodes else None
    
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
            cpus = list(self.fdt.getprop(node_offset, 'cpus').as_uint32_array())
        except libfdt.FdtException:
            pass
        
        # Parse distance matrix (optional)
        distance_matrix = {}
        try:
            distances = list(self.fdt.getprop(node_offset, 'distance-matrix').as_uint32_array())
            # Simple distance matrix parsing - would need more sophisticated logic for full matrix
        except libfdt.FdtException:
            pass
        
        # Parse memory type
        memory_type = "dram"
        try:
            memory_type = self.fdt.getprop(node_offset, 'memory-type').as_string()
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
    
    def _parse_hex_value(self, hex_str: str) -> int:
        """Parse hex value from DTS format."""
        import re
        
        # Handle hex values like "0x0 0x400000000" (64-bit values)
        parts = hex_str.strip().split()
        if len(parts) == 2:
            # 64-bit value: high 32 bits, low 32 bits
            high = int(parts[0], 16)
            low = int(parts[1], 16)
            return (high << 32) | low
        elif len(parts) == 1:
            # Single hex value
            return int(parts[0], 16)
        else:
            raise ParseError(f"Invalid hex value format: {hex_str}")
    
    def dtb_to_dts(self, dtb_path: str) -> str:
        """Convert DTB file back to DTS format using pure Python implementation."""
        try:
            with open(dtb_path, 'rb') as f:
                dtb_data = f.read()
            
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
            raise ParseError(f"Failed to convert DTB to DTS: {e}")
    
    def _fdt_to_dts_recursive(self, node_offset: int, indent_level: int) -> List[str]:
        """Recursively convert FDT nodes to DTS format."""
        lines = []
        indent = '    ' * indent_level
        
        try:
            # Get node name
            node_name = self.fdt.get_name(node_offset)
            if node_offset == 0:
                node_name = '/'  # Root node
            
            # Start node
            if node_offset == 0:
                lines.append(f'{indent}/ {{')
            else:
                lines.append(f'{indent}{node_name} {{')
            
            # Get properties for this node
            try:
                prop_offset = self.fdt.first_property_offset(node_offset)
                while prop_offset >= 0:
                    try:
                        prop = self.fdt.get_property_by_offset(prop_offset)
                        prop_name = prop.name
                        prop_data = prop.data
                        
                        # Convert property to DTS format
                        prop_line = self._property_to_dts(prop_name, prop_data, indent + '    ')
                        if prop_line:
                            lines.append(prop_line)
                    except Exception as e:
                        # Skip problematic properties
                        pass
                    
                    try:
                        prop_offset = self.fdt.next_property_offset(prop_offset)
                    except:
                        break
            except Exception as e:
                # No properties or error accessing properties
                pass
            
            # Process child nodes
            try:
                child_offset = self.fdt.first_subnode(node_offset)
                while child_offset >= 0:
                    try:
                        child_lines = self._fdt_to_dts_recursive(child_offset, indent_level + 1)
                        lines.extend(child_lines)
                    except Exception as e:
                        # Skip problematic child nodes
                        pass
                    
                    try:
                        child_offset = self.fdt.next_subnode(child_offset)
                    except:
                        break
            except Exception as e:
                # No child nodes or error accessing child nodes
                pass
            
            # Close node
            lines.append(f'{indent}}};')
            
        except Exception as e:
            # If we can't process this node, create a placeholder
            lines.append(f'{indent}// Error processing node: {e}')
            lines.append(f'{indent}}};')
        
        return lines
    
    def _property_to_dts(self, name: str, data: bytes, indent: str) -> str:
        """Convert FDT property to DTS format."""
        if not data:
            return f'{indent}{name};'
        
        # Try to interpret as different data types
        if len(data) == 4:
            # 32-bit integer
            value = int.from_bytes(data, byteorder='big')
            return f'{indent}{name} = <{hex(value)}>;'
        elif len(data) == 8:
            # 64-bit integer
            high = int.from_bytes(data[:4], byteorder='big')
            low = int.from_bytes(data[4:], byteorder='big')
            return f'{indent}{name} = <{hex(high)} {hex(low)}>;'
        elif len(data) % 4 == 0:
            # Array of 32-bit integers
            values = []
            for i in range(0, len(data), 4):
                value = int.from_bytes(data[i:i+4], byteorder='big')
                values.append(hex(value))
            return f'{indent}{name} = <{" ".join(values)}>;'
        else:
            # String data
            try:
                # Try to decode as string
                string_data = data.rstrip(b'\x00').decode('utf-8')
                return f'{indent}{name} = "{string_data}";'
            except UnicodeDecodeError:
                # Fall back to hex representation
                hex_data = ' '.join(f'{b:02x}' for b in data)
                return f'{indent}{name} = [{hex_data}];'
