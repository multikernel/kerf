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
Validation report generation and formatting.
"""

from typing import List, Dict, Any
from ..models import GlobalDeviceTree, ValidationResult, ResourceUsage
from .validator import MultikernelValidator


class ValidationReporter:
    """Generates validation reports in various formats."""
    
    def generate_report(self, result: ValidationResult, tree: GlobalDeviceTree, verbose: bool = False, format: str = 'text') -> str:
        """Generate comprehensive validation report in specified format."""
        if format == 'json':
            import json
            return json.dumps(self.generate_json_report(result, tree), indent=2)
        elif format == 'yaml':
            return self.generate_yaml_report(result, tree)
        else:
            return self._generate_text_report(result, tree, verbose)
    
    def _generate_text_report(self, result: ValidationResult, tree: GlobalDeviceTree, verbose: bool = False) -> str:
        """Generate text format validation report."""
        lines = []
        
        # Header
        lines.append("Multikernel Device Tree Validation Report")
        lines.append("=" * 42)
        
        # Status
        if result.is_valid:
            lines.append("Status: ✓ VALID")
        else:
            lines.append("Status: ✗ INVALID")
        
        lines.append("")
        
        # Hardware inventory summary
        lines.extend(self._format_hardware_inventory(tree))
        lines.append("")
        
        # Instance allocations
        lines.extend(self._format_instance_allocations(tree))
        lines.append("")
        
        # Resource utilization
        lines.extend(self._format_resource_utilization(tree))
        lines.append("")
        
        # Validation results
        if result.errors:
            lines.append("Validation Errors:")
            for error in result.errors:
                lines.append(f"  ✗ {error}")
            lines.append("")
        
        if result.warnings:
            lines.append("Validation Warnings:")
            for warning in result.warnings:
                lines.append(f"  ⚠ {warning}")
            lines.append("")
        
        if result.suggestions:
            lines.append("Suggestions:")
            for suggestion in result.suggestions:
                lines.append(f"  💡 {suggestion}")
            lines.append("")
        
        # Summary
        if result.is_valid:
            lines.append("✓ All validations passed")
        else:
            lines.append(f"✗ Validation failed with {len(result.errors)} errors")
            if result.warnings:
                lines.append(f"  and {len(result.warnings)} warnings")
        
        return "\n".join(lines)
    
    def _format_hardware_inventory(self, tree: GlobalDeviceTree) -> List[str]:
        """Format hardware inventory section."""
        lines = []
        lines.append("Hardware Inventory:")
        
        # CPU information
        cpus = tree.hardware.cpus
        host_count = len(cpus.host_reserved)
        available_count = len(cpus.available)
        host_percent = (host_count / cpus.total) * 100
        memory_pool_percent = (available_count / cpus.total) * 100
        
        lines.append(f"  CPUs: {cpus.total} total")
        lines.append(f"    Host reserved: {cpus.host_reserved[0]}-{cpus.host_reserved[-1]} ({host_count} CPUs, {host_percent:.0f}%)")
        lines.append(f"    Memory pool: {cpus.available[0]}-{cpus.available[-1]} ({available_count} CPUs, {memory_pool_percent:.0f}%)")
        
        # Memory information
        memory = tree.hardware.memory
        total_gb = memory.total_bytes / (1024**3)
        host_gb = memory.host_reserved_bytes / (1024**3)
        memory_pool_gb = memory.memory_pool_bytes / (1024**3)
        host_mem_percent = (memory.host_reserved_bytes / memory.total_bytes) * 100
        memory_pool_mem_percent = (memory.memory_pool_bytes / memory.total_bytes) * 100
        
        lines.append(f"  Memory: {total_gb:.0f}GB total")
        lines.append(f"    Host reserved: {host_gb:.0f}GB ({host_mem_percent:.0f}%)")
        lines.append(f"    Memory pool: {memory_pool_gb:.0f}GB at {hex(memory.memory_pool_base)} ({memory_pool_mem_percent:.0f}%)")
        
        # Device information
        device_count = len(tree.hardware.devices)
        network_devices = sum(1 for d in tree.hardware.devices.values() if 'ethernet' in d.compatible.lower())
        storage_devices = sum(1 for d in tree.hardware.devices.values() if 'nvme' in d.compatible.lower())
        
        lines.append(f"  Devices: {device_count} total")
        if network_devices > 0:
            lines.append(f"    Network: {network_devices}")
        if storage_devices > 0:
            lines.append(f"    Storage: {storage_devices}")
        
        return lines
    
    def _format_instance_allocations(self, tree: GlobalDeviceTree) -> List[str]:
        """Format instance allocation section."""
        lines = []
        lines.append("Instance Allocations:")
        
        for name, instance in tree.instances.items():
            lines.append(f"  {name} (ID: {instance.id}):")
            
            # CPU allocation
            cpu_count = len(instance.resources.cpus)
            cpu_range = f"{instance.resources.cpus[0]}-{instance.resources.cpus[-1]}" if instance.resources.cpus else "none"
            cpu_percent = (cpu_count / len(tree.hardware.cpus.available)) * 100
            lines.append(f"    CPUs: {cpu_range} ({cpu_count} CPUs, {cpu_percent:.0f}% of pool)")
            
            # Memory allocation
            memory_gb = instance.resources.memory_bytes / (1024**3)
            memory_percent = (instance.resources.memory_bytes / tree.hardware.memory.memory_pool_bytes) * 100
            lines.append(f"    Memory: {memory_gb:.0f}GB at {hex(instance.resources.memory_base)} ({memory_percent:.0f}% of pool)")
            
            # Device allocation
            device_count = len(instance.resources.devices)
            if device_count > 0:
                devices_str = ", ".join(instance.resources.devices)
                lines.append(f"    Devices: {devices_str}")
            else:
                lines.append("    Devices: none")
            
            
            lines.append("    Status: ✓ Valid")
            lines.append("")
        
        return lines
    
    def _format_resource_utilization(self, tree: GlobalDeviceTree) -> List[str]:
        """Format resource utilization section."""
        lines = []
        lines.append("Resource Utilization:")
        
        # Calculate totals
        total_cpus_allocated = sum(len(instance.resources.cpus) for instance in tree.instances.values())
        total_memory_allocated = sum(instance.resources.memory_bytes for instance in tree.instances.values())
        
        available_cpus = len(tree.hardware.cpus.available)
        memory_pool_bytes = tree.hardware.memory.memory_pool_bytes
        
        cpu_percent = (total_cpus_allocated / available_cpus) * 100
        memory_percent = (total_memory_allocated / memory_pool_bytes) * 100
        
        lines.append(f"  CPUs: {total_cpus_allocated}/{available_cpus} allocated ({cpu_percent:.0f}%), {available_cpus - total_cpus_allocated} free")
        
        memory_gb_allocated = total_memory_allocated / (1024**3)
        memory_gb_total = memory_pool_bytes / (1024**3)
        lines.append(f"  Memory: {memory_gb_allocated:.0f}/{memory_gb_total:.0f} GB allocated ({memory_percent:.0f}%), {memory_gb_total - memory_gb_allocated:.0f} GB free")
        
        # Device utilization
        total_devices = len(tree.hardware.devices)
        allocated_devices = sum(len(instance.resources.devices) for instance in tree.instances.values())
        
        # Calculate device-specific utilization
        network_devices = sum(1 for device in tree.hardware.devices.values() if 'ethernet' in device.compatible)
        storage_devices = sum(1 for device in tree.hardware.devices.values() if 'nvme' in device.compatible)
        
        allocated_network = sum(1 for instance in tree.instances.values() 
                              for device_ref in instance.resources.devices 
                              if '_vf' in device_ref)
        allocated_storage = sum(1 for instance in tree.instances.values() 
                              for device_ref in instance.resources.devices 
                              if '_ns' in device_ref)
        
        if network_devices > 0:
            lines.append(f"  Network: {allocated_network}/{network_devices} VFs allocated")
        if storage_devices > 0:
            lines.append(f"  Storage: {allocated_storage}/{storage_devices} namespaces allocated")
        
        lines.append(f"  Devices: {allocated_devices}/{total_devices} allocated")
        
        return lines
    
    def generate_json_report(self, result: ValidationResult, tree: GlobalDeviceTree) -> Dict[str, Any]:
        """Generate JSON format report."""
        return {
            "status": "valid" if result.is_valid else "invalid",
            "errors": result.errors,
            "warnings": result.warnings,
            "suggestions": result.suggestions,
            "hardware": {
                "cpus": {
                    "total": tree.hardware.cpus.total,
                    "host_reserved": tree.hardware.cpus.host_reserved,
                    "available": tree.hardware.cpus.available
                },
                "memory": {
                    "total_bytes": tree.hardware.memory.total_bytes,
                    "host_reserved_bytes": tree.hardware.memory.host_reserved_bytes,
                    "memory_pool_base": tree.hardware.memory.memory_pool_base,
                    "memory_pool_bytes": tree.hardware.memory.memory_pool_bytes
                },
                "devices": {
                    name: {
                        "compatible": device.compatible,
                        "pci_id": device.pci_id,
                        "sriov_vfs": device.sriov_vfs,
                        "namespaces": device.namespaces
                    }
                    for name, device in tree.hardware.devices.items()
                }
            },
            "instances": {
                name: {
                    "id": instance.id,
                    "resources": {
                        "cpus": instance.resources.cpus,
                        "memory_base": instance.resources.memory_base,
                        "memory_bytes": instance.resources.memory_bytes,
                        "devices": instance.resources.devices
                    },
                }
                for name, instance in tree.instances.items()
            }
        }
    
    def generate_yaml_report(self, result: ValidationResult, tree: GlobalDeviceTree) -> str:
        """Generate YAML format report."""
        import yaml
        data = self.generate_json_report(result, tree)
        return yaml.dump(data, default_flow_style=False)
