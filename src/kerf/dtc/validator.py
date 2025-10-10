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
Validation layer for multikernel device tree configurations.
"""

from typing import List, Dict, Set, Tuple
from ..models import GlobalDeviceTree, ValidationResult, ResourceUsage
from ..exceptions import ValidationError, ResourceConflictError, ResourceExhaustionError, InvalidReferenceError


class MultikernelValidator:
    """Validator for multikernel device tree configurations."""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.suggestions: List[str] = []
        self.dts_content: str = ""
        self.dts_filename: str = ""
    
    def set_dts_context(self, dts_content: str, filename: str = ""):
        """Set DTS content and filename for enhanced error messages."""
        self.dts_content = dts_content
        self.dts_filename = filename
    
    def _find_line_number(self, pattern: str) -> int:
        """Find line number for a pattern in DTS content."""
        if not self.dts_content:
            return 0
        
        lines = self.dts_content.split('\n')
        for i, line in enumerate(lines, 1):
            if pattern in line:
                return i
        return 0
    
    def _format_error_with_context(self, error_type: str, instance_name: str, 
                                 problem: str, current_state: str, 
                                 suggestion: str, alternative: str = None,
                                 pattern: str = None) -> str:
        """Format enhanced error message with context and suggestions."""
        lines = []
        
        # Main error line
        lines.append(f"{instance_name}: {error_type}")
        lines.append(f"  {problem}")
        lines.append(f"  Current state: {current_state}")
        
        # Suggestions
        lines.append(f"  Suggestion: {suggestion}")
        if alternative:
            lines.append(f"  Alternative: {alternative}")
        
        # File context
        if pattern and self.dts_content:
            line_num = self._find_line_number(pattern)
            if line_num > 0:
                filename = self.dts_filename or "system.dts"
                lines.append(f"  In file {filename}:")
                lines.append(f"    Line {line_num}: {pattern}")
        
        return '\n'.join(lines)
    
    def validate(self, tree: GlobalDeviceTree) -> ValidationResult:
        """Perform comprehensive validation of the device tree."""
        self.errors.clear()
        self.warnings.clear()
        self.suggestions.clear()
        
        # Validate hardware inventory
        self._validate_hardware_inventory(tree)
        
        # Validate instances
        self._validate_instances(tree)
        
        # Validate resource allocations
        self._validate_resource_allocations(tree)
        
        # Validate device references
        self._validate_device_references(tree)
        
        # Generate resource usage summary
        usage = self._calculate_resource_usage(tree)
        self._validate_resource_limits(usage, tree)
        
        return ValidationResult(
            is_valid=len(self.errors) == 0,
            errors=self.errors.copy(),
            warnings=self.warnings.copy(),
            suggestions=self.suggestions.copy()
        )
    
    def _validate_hardware_inventory(self, tree: GlobalDeviceTree):
        """Validate hardware inventory consistency."""
        # Validate CPU allocation
        cpus = tree.hardware.cpus
        if cpus.total <= 0:
            self.errors.append("Hardware inventory: Total CPU count must be positive")
        
        if not cpus.available:
            self.errors.append("Hardware inventory: No CPUs available for spawn kernels")
        
        # Check for CPU overlap between host and available
        host_set = set(cpus.host_reserved)
        available_set = set(cpus.available)
        overlap = host_set.intersection(available_set)
        if overlap:
            self.errors.append(f"Hardware inventory: CPU overlap between host and available: {sorted(overlap)}")
        
        # Validate memory allocation
        memory = tree.hardware.memory
        if memory.total_bytes <= 0:
            self.errors.append("Hardware inventory: Total memory must be positive")
        
        if memory.memory_pool_bytes <= 0:
            self.errors.append("Hardware inventory: Spawn pool size must be positive")
        
        if memory.memory_pool_base + memory.memory_pool_bytes > memory.total_bytes:
            self.errors.append("Hardware inventory: Spawn pool extends beyond total memory")
    
    def _validate_instances(self, tree: GlobalDeviceTree):
        """Validate instance definitions."""
        instance_names = set()
        instance_ids = set()
        
        for name, instance in tree.instances.items():
            # Check for duplicate names (check the instance.name field, not the dict key)
            if instance.name in instance_names:
                self.errors.append(f"Duplicate instance name: '{instance.name}' appears multiple times")
            instance_names.add(instance.name)
            
            # Check for duplicate IDs
            if instance.id in instance_ids:
                self.errors.append(f"Duplicate instance ID: {instance.id} assigned to multiple instances")
            instance_ids.add(instance.id)
            
            # Validate instance-specific properties
            self._validate_instance_resources(instance, tree)
    
    def _validate_instance_resources(self, instance, tree: GlobalDeviceTree):
        """Validate resources for a specific instance."""
        # Validate CPU allocation
        self._validate_cpu_allocation(instance, tree)
        
        # Validate memory allocation
        self._validate_memory_allocation(instance, tree)
        
        # Validate device allocation
        self._validate_device_allocation(instance, tree)
        
        # Validate topology constraints
        self._validate_topology_constraints(instance, tree)
    
    def _validate_cpu_allocation(self, instance, tree: GlobalDeviceTree):
        """Validate CPU allocation for an instance."""
        cpus = tree.hardware.cpus
        instance_cpus = set(instance.resources.cpus)
        
        # Check if CPUs exist in hardware inventory
        for cpu in instance_cpus:
            if cpu < 0 or cpu >= cpus.total:
                error_msg = self._format_error_with_context(
                    error_type="CPU allocation error",
                    instance_name=instance.name,
                    problem=f"CPU {cpu} does not exist in hardware inventory",
                    current_state=f"Hardware has CPUs 0-{cpus.total-1}",
                    suggestion=f"Use CPUs in range 0-{cpus.total-1}",
                    alternative="Check hardware inventory configuration",
                    pattern=f"cpus = <{','.join(map(str, sorted(instance_cpus)))}>"
                )
                self.errors.append(error_msg)
        
        # Check if CPUs are available (not host-reserved)
        host_reserved = set(cpus.host_reserved)
        reserved_cpus = instance_cpus.intersection(host_reserved)
        if reserved_cpus:
            available_cpus = sorted(set(cpus.available))
            error_msg = self._format_error_with_context(
                error_type="CPU allocation error",
                instance_name=instance.name,
                problem=f"CPUs {sorted(reserved_cpus)} are reserved for host kernel",
                current_state=f"Host reserved CPUs: {sorted(host_reserved)}",
                suggestion=f"Use available CPUs: {available_cpus}",
                alternative="Modify host-reserved CPU configuration",
                pattern=f"cpus = <{','.join(map(str, sorted(instance_cpus)))}>"
            )
            self.errors.append(error_msg)
        
        # Check for CPU conflicts with other instances
        for other_name, other_instance in tree.instances.items():
            if other_name == instance.name:
                continue
            
            other_cpus = set(other_instance.resources.cpus)
            overlap = instance_cpus.intersection(other_cpus)
            if overlap:
                error_msg = self._format_error_with_context(
                    error_type="CPU allocation conflict",
                    instance_name=instance.name,
                    problem=f"CPU overlap with instance '{other_name}'",
                    current_state=f"Both instances use CPUs: {sorted(overlap)}",
                    suggestion=f"Assign different CPUs to {instance.name} or {other_name}",
                    alternative="Use CPU ranges instead of individual CPUs",
                    pattern=f"cpus = <{','.join(map(str, sorted(instance_cpus)))}>"
                )
                self.errors.append(error_msg)
                # Add suggestions
                available_cpus = set(cpus.available) - instance_cpus - other_cpus
                if available_cpus:
                    self.suggestions.append(
                        f"Consider using available CPUs: {sorted(list(available_cpus)[:len(overlap)])}"
                    )
    
    def _validate_memory_allocation(self, instance, tree: GlobalDeviceTree):
        """Validate memory allocation for an instance."""
        memory = tree.hardware.memory
        instance_memory = instance.resources
        
        # Check if memory region is within memory pool
        memory_start = instance_memory.memory_base
        memory_end = memory_start + instance_memory.memory_bytes
        
        if memory_start < memory.memory_pool_base:
            error_msg = self._format_error_with_context(
                error_type="Memory allocation error",
                instance_name=instance.name,
                problem=f"Memory base {hex(memory_start)} is before memory pool start",
                current_state=f"Spawn pool starts at {hex(memory.memory_pool_base)}",
                suggestion=f"Use memory base >= {hex(memory.memory_pool_base)}",
                alternative="Adjust memory pool configuration",
                pattern=f"memory-base = <{hex(memory_start)}>"
            )
            self.errors.append(error_msg)
        
        if memory_end > memory.memory_pool_base + memory.memory_pool_bytes:
            pool_end = memory.memory_pool_base + memory.memory_pool_bytes
            exceeds_by = memory_end - pool_end
            error_msg = self._format_error_with_context(
                error_type="Memory allocation error",
                instance_name=instance.name,
                problem=f"Memory region extends beyond memory pool",
                current_state=f"Instance memory: {hex(memory_start)}-{hex(memory_end)}, Pool: {hex(memory.memory_pool_base)}-{hex(pool_end)}",
                suggestion=f"Reduce memory size by {hex(exceeds_by)} or use different base address",
                alternative="Increase memory pool size or adjust memory allocation",
                pattern=f"memory-size = <{hex(instance_memory.memory_bytes)}>"
            )
            self.errors.append(error_msg)
        
        # Check for memory alignment
        if memory_start % 0x1000 != 0:
            self.warnings.append(
                f"Instance {instance.name}: Memory base {hex(memory_start)} not page-aligned\n"
                f"  Page size: 4KB (0x1000)\n"
                f"  Suggestion: Use base address {hex((memory_start // 0x1000) * 0x1000)}"
            )
        
        # Check for memory conflicts with other instances
        for other_name, other_instance in tree.instances.items():
            if other_name == instance.name:
                continue
            
            other_start = other_instance.resources.memory_base
            other_end = other_start + other_instance.resources.memory_bytes
            
            # Check for overlap
            if not (memory_end <= other_start or other_end <= memory_start):
                overlap_start = max(memory_start, other_start)
                overlap_end = min(memory_end, other_end)
                overlap_size = overlap_end - overlap_start
                
                self.errors.append(
                    f"Instance {instance.name} and {other_name}: Memory region overlap detected\n"
                    f"  {instance.name} memory: {hex(memory_start)} - {hex(memory_end)}\n"
                    f"  {other_name} memory: {hex(other_start)} - {hex(other_end)}\n"
                    f"  Overlapping region: {hex(overlap_start)} - {hex(overlap_end)} ({overlap_size} bytes)"
                )
    
    def _validate_device_allocation(self, instance, tree: GlobalDeviceTree):
        """Validate device allocation for an instance."""

        for device_ref in instance.resources.devices:
            # Parse device reference (e.g., "eth0_vf1" -> device "eth0", VF "1")
            if '_vf' in device_ref:
                device_name = device_ref.split('_vf')[0]
                vf_id = int(device_ref.split('_vf')[1])
                
                if device_name not in tree.hardware.devices:
                    available_devices = list(tree.hardware.devices.keys())
                    error_msg = self._format_error_with_context(
                        error_type="Device reference error",
                        instance_name=instance.name,
                        problem=f"Reference to non-existent device '{device_name}'",
                        current_state=f"Device '{device_name}' not found in hardware inventory",
                        suggestion=f"Use available devices: {available_devices}",
                        alternative="Add device '{device_name}' to hardware inventory",
                        pattern=f"devices = <&{device_ref}>"
                    )
                    self.errors.append(error_msg)
                    continue
                
                device_info = tree.hardware.devices[device_name]
                if device_info.available_vfs and vf_id not in device_info.available_vfs:
                    available_vfs = sorted(device_info.available_vfs)
                    error_msg = self._format_error_with_context(
                        error_type="VF allocation error",
                        instance_name=instance.name,
                        problem=f"VF {vf_id} not available for device {device_name}",
                        current_state=f"Device {device_name} has VFs: {available_vfs}",
                        suggestion=f"Use available VF: {available_vfs[0] if available_vfs else 'none'}",
                        alternative="Configure VF {vf_id} for device {device_name}",
                        pattern=f"devices = <&{device_ref}>"
                    )
                    self.errors.append(error_msg)
            
            elif '_ns' in device_ref:
                device_name = device_ref.split('_ns')[0]
                ns_id = int(device_ref.split('_ns')[1])
                
                if device_name not in tree.hardware.devices:
                    self.errors.append(f"Instance {instance.name}: Reference to non-existent device '{device_name}'")
                    continue
                
                device_info = tree.hardware.devices[device_name]
                if device_info.available_ns and ns_id not in device_info.available_ns:
                    self.errors.append(f"Instance {instance.name}: Namespace {ns_id} not available for device {device_name}")
            
            else:
                # Direct device reference
                if device_ref not in tree.hardware.devices:
                    self.errors.append(f"Instance {instance.name}: Reference to non-existent device '{device_ref}'")
    
    def _validate_resource_allocations(self, tree: GlobalDeviceTree):
        """Validate overall resource allocation limits."""
        # Calculate total allocations
        total_cpus_allocated = 0
        total_memory_allocated = 0
        
        for instance in tree.instances.values():
            total_cpus_allocated += len(instance.resources.cpus)
            total_memory_allocated += instance.resources.memory_bytes
        
        # Check CPU limits
        available_cpus = len(tree.hardware.cpus.available)
        if total_cpus_allocated > available_cpus:
            self.errors.append(
                f"Total CPU allocation ({total_cpus_allocated}) exceeds available CPUs ({available_cpus})"
            )
        
        # Check memory limits
        if total_memory_allocated > tree.hardware.memory.memory_pool_bytes:
            self.errors.append(
                f"Total memory allocation ({total_memory_allocated} bytes) "
                f"exceeds memory pool ({tree.hardware.memory.memory_pool_bytes} bytes)"
            )
    
    def _validate_device_references(self, tree: GlobalDeviceTree):
        """Validate device references and phandles."""
        
        for name, device_ref in tree.device_references.items():
            # Validate that the parent device exists
            if hasattr(device_ref, 'parent') and device_ref.parent:
                parent_name = device_ref.parent.replace('&', '').replace(':', '')
                if parent_name not in tree.hardware.devices:
                    self.errors.append(f"Device reference '{name}': Parent device '{parent_name}' not found in hardware inventory")
            
            # Validate device-specific properties
            if hasattr(device_ref, 'vf_id') and device_ref.vf_id is not None:
                if hasattr(device_ref, 'parent') and device_ref.parent:
                    parent_name = device_ref.parent.replace('&', '').replace(':', '')
                    if parent_name in tree.hardware.devices:
                        device_info = tree.hardware.devices[parent_name]
                        if device_info.available_vfs and device_ref.vf_id not in device_info.available_vfs:
                            self.errors.append(f"Device reference '{name}': VF {device_ref.vf_id} not available for device {parent_name}")
            
            if hasattr(device_ref, 'namespace_id') and device_ref.namespace_id is not None:
                if hasattr(device_ref, 'parent') and device_ref.parent:
                    parent_name = device_ref.parent.replace('&', '').replace(':', '')
                    if parent_name in tree.hardware.devices:
                        device_info = tree.hardware.devices[parent_name]
                        if device_info.available_ns and device_ref.namespace_id not in device_info.available_ns:
                            self.errors.append(f"Device reference '{name}': Namespace {device_ref.namespace_id} not available for device {parent_name}")
    
    def _calculate_resource_usage(self, tree: GlobalDeviceTree) -> ResourceUsage:
        """Calculate resource usage summary."""
        total_cpus_allocated = sum(len(instance.resources.cpus) for instance in tree.instances.values())
        total_memory_allocated = sum(instance.resources.memory_bytes for instance in tree.instances.values())
        
        return ResourceUsage(
            cpus_allocated=total_cpus_allocated,
            cpus_total=len(tree.hardware.cpus.available),
            memory_allocated=total_memory_allocated,
            memory_total=tree.hardware.memory.memory_pool_bytes,
            devices_allocated=0,  # Would calculate from device allocations
            devices_total=len(tree.hardware.devices)
        )
    
    def _validate_resource_limits(self, usage: ResourceUsage, tree: GlobalDeviceTree):
        """Validate resource limits and generate warnings."""
        # Check for unallocated resources
        unallocated_cpus = usage.cpus_total - usage.cpus_allocated
        if unallocated_cpus > 0:
            percentage = (unallocated_cpus / usage.cpus_total) * 100
            self.warnings.append(
                f"Resource utilization: {unallocated_cpus} CPUs ({percentage:.1f}% of memory pool) remain unallocated"
            )
        
        unallocated_memory = usage.memory_total - usage.memory_allocated
        if unallocated_memory > 0:
            percentage = (unallocated_memory / usage.memory_total) * 100
            self.warnings.append(
                f"Resource utilization: {unallocated_memory} bytes ({percentage:.1f}% of memory pool) remain unallocated"
            )
    
    def _validate_topology_constraints(self, instance, tree: GlobalDeviceTree):
        """Validate NUMA and CPU topology constraints for an instance."""
        resources = instance.resources
        
        # Validate NUMA node constraints
        if resources.numa_nodes and tree.hardware.numa_topology:
            self._validate_numa_constraints(instance, tree)
        
        # Validate CPU affinity constraints
        if resources.cpu_affinity:
            self._validate_cpu_affinity_constraints(instance, tree)
        
        # Validate memory policy constraints
        if resources.memory_policy:
            self._validate_memory_policy_constraints(instance, tree)
    
    def _validate_numa_constraints(self, instance, tree: GlobalDeviceTree):
        """Validate NUMA node constraints for an instance."""
        resources = instance.resources
        numa_topology = tree.hardware.numa_topology
        
        if not numa_topology:
            return
        
        # Check if specified NUMA nodes exist
        for numa_node in resources.numa_nodes:
            if numa_node not in numa_topology.nodes:
                self.errors.append(
                    f"Instance {instance.name}: NUMA node {numa_node} does not exist in hardware topology"
                )
        
        # Check if CPUs are in the specified NUMA nodes
        if resources.numa_nodes:
            for cpu in resources.cpus:
                cpu_numa_node = numa_topology.get_numa_node_for_cpu(cpu)
                
                if cpu_numa_node is not None and cpu_numa_node not in resources.numa_nodes:
                    self.warnings.append(
                        f"Instance {instance.name}: CPU {cpu} is in NUMA node {cpu_numa_node}, "
                        f"but instance is configured for NUMA nodes {resources.numa_nodes}. "
                        f"This may cause performance issues due to remote memory access."
                    )
    
    def _validate_cpu_affinity_constraints(self, instance, tree: GlobalDeviceTree):
        """Validate CPU affinity constraints for an instance."""
        resources = instance.resources
        cpus = resources.cpus
        
        if resources.cpu_affinity == "compact":
            # For compact affinity, CPUs should be from the same NUMA node and preferably same core
            self._validate_compact_affinity(instance, tree)
        elif resources.cpu_affinity == "spread":
            # For spread affinity, CPUs should be distributed across different NUMA nodes
            self._validate_spread_affinity(instance, tree)
        elif resources.cpu_affinity == "local":
            # For local affinity, CPUs should be from the same NUMA node as memory
            self._validate_local_affinity(instance, tree)
    
    def _validate_compact_affinity(self, instance, tree: GlobalDeviceTree):
        """Validate compact CPU affinity."""
        resources = instance.resources
        cpus = resources.cpus
        
        if not tree.hardware.numa_topology:
            return
        
        # Check if CPUs are from the same NUMA node
        numa_nodes = set()
        for cpu in cpus:
            numa_node = tree.hardware.numa_topology.get_numa_node_for_cpu(cpu)
            if numa_node is not None:
                numa_nodes.add(numa_node)
        
        if len(numa_nodes) > 1:
            self.warnings.append(
                f"Instance {instance.name}: Compact CPU affinity requested but CPUs span multiple NUMA nodes: {sorted(numa_nodes)}"
            )
        
        # Check if CPUs are from the same core (for SMT)
        if tree.hardware.cpus.topology:
            cores = set()
            for cpu in cpus:
                if cpu in tree.hardware.cpus.topology:
                    cores.add(tree.hardware.cpus.topology[cpu].core_id)
            
            if len(cores) > len(cpus) // 2:
                self.warnings.append(
                    f"Instance {instance.name}: Compact CPU affinity may not be optimal - CPUs span {len(cores)} cores"
                )
    
    def _validate_spread_affinity(self, instance, tree: GlobalDeviceTree):
        """Validate spread CPU affinity."""
        resources = instance.resources
        cpus = resources.cpus
        
        if not tree.hardware.numa_topology:
            return
        
        # Check if CPUs are distributed across different NUMA nodes
        numa_nodes = set()
        for cpu in cpus:
            numa_node = tree.hardware.numa_topology.get_numa_node_for_cpu(cpu)
            if numa_node is not None:
                numa_nodes.add(numa_node)
        
        if len(numa_nodes) < 2:
            self.warnings.append(
                f"Instance {instance.name}: Spread CPU affinity requested but CPUs are from single NUMA node {list(numa_nodes)[0]}"
            )
    
    def _validate_local_affinity(self, instance, tree: GlobalDeviceTree):
        """Validate local CPU affinity (CPUs and memory on same NUMA node)."""
        resources = instance.resources
        cpus = resources.cpus
        
        if not tree.hardware.numa_topology:
            return
        
        # Find the NUMA node for the memory allocation
        memory_numa_node = None
        memory_base = resources.memory_base
        
        for node_id, node in tree.hardware.numa_topology.nodes.items():
            if node.memory_base <= memory_base < node.memory_base + node.memory_size:
                memory_numa_node = node_id
                break
        
        if memory_numa_node is None:
            self.warnings.append(
                f"Instance {instance.name}: Could not determine NUMA node for memory allocation at {hex(memory_base)}"
            )
            return
        
        # Check if CPUs are from the same NUMA node as memory
        cpu_numa_nodes = set()
        for cpu in cpus:
            numa_node = tree.hardware.numa_topology.get_numa_node_for_cpu(cpu)
            if numa_node is not None:
                cpu_numa_nodes.add(numa_node)
        
        if memory_numa_node not in cpu_numa_nodes:
            self.warnings.append(
                f"Instance {instance.name}: Local affinity requested but CPUs are from NUMA nodes {sorted(cpu_numa_nodes)} "
                f"while memory is on NUMA node {memory_numa_node}"
            )
    
    def _validate_memory_policy_constraints(self, instance, tree: GlobalDeviceTree):
        """Validate memory policy constraints for an instance."""
        resources = instance.resources
        
        if resources.memory_policy == "local":
            # For local memory policy, memory should be allocated from the same NUMA node as CPUs
            self._validate_local_memory_policy(instance, tree)
        elif resources.memory_policy == "interleave":
            # For interleave policy, memory should be distributed across multiple NUMA nodes
            self._validate_interleave_memory_policy(instance, tree)
        elif resources.memory_policy == "bind":
            # For bind policy, memory should be bound to specific NUMA nodes
            self._validate_bind_memory_policy(instance, tree)
    
    def _validate_local_memory_policy(self, instance, tree: GlobalDeviceTree):
        """Validate local memory policy."""
        resources = instance.resources
        
        if not tree.hardware.numa_topology:
            return
        
        # Find the NUMA node for the memory allocation
        memory_numa_node = None
        memory_base = resources.memory_base
        
        for node_id, node in tree.hardware.numa_topology.nodes.items():
            if node.memory_base <= memory_base < node.memory_base + node.memory_size:
                memory_numa_node = node_id
                break
        
        if memory_numa_node is None:
            self.warnings.append(
                f"Instance {instance.name}: Local memory policy requested but memory allocation "
                f"at {hex(memory_base)} is not within any NUMA node memory range"
            )
            return
        
        # Check if CPUs are from the same NUMA node
        cpu_numa_nodes = set()
        for cpu in resources.cpus:
            numa_node = tree.hardware.numa_topology.get_numa_node_for_cpu(cpu)
            if numa_node is not None:
                cpu_numa_nodes.add(numa_node)
        
        if memory_numa_node not in cpu_numa_nodes:
            self.warnings.append(
                f"Instance {instance.name}: Local memory policy requested but CPUs are from NUMA nodes "
                f"{sorted(cpu_numa_nodes)} while memory is on NUMA node {memory_numa_node}"
            )
    
    def _validate_interleave_memory_policy(self, instance, tree: GlobalDeviceTree):
        """Validate interleave memory policy."""
        # For interleave policy, we would typically check if memory is distributed
        # across multiple NUMA nodes, but this is complex to validate without
        # knowing the actual memory allocation implementation
        pass
    
    def _validate_bind_memory_policy(self, instance, tree: GlobalDeviceTree):
        """Validate bind memory policy."""
        resources = instance.resources
        
        if not resources.numa_nodes:
            self.warnings.append(
                f"Instance {instance.name}: Bind memory policy requested but no NUMA nodes specified"
            )
            return
        
        # Check if specified NUMA nodes exist
        if tree.hardware.numa_topology:
            for numa_node in resources.numa_nodes:
                if numa_node not in tree.hardware.numa_topology.nodes:
                    self.errors.append(
                        f"Instance {instance.name}: Bind memory policy references non-existent NUMA node {numa_node}"
                    )
