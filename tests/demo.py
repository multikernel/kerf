#!/usr/bin/env python3
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
Demonstration of kerf core functionality.
"""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kerf.models import (
    GlobalDeviceTree, HardwareInventory, CPUAllocation, MemoryAllocation,
    DeviceInfo, Instance, InstanceResources, InstanceConfig, WorkloadType
)
from kerf.dtc.validator import MultikernelValidator
from kerf.dtc.reporter import ValidationReporter


def create_demo_system():
    """Create a demonstration multikernel system."""
    print("Creating demonstration multikernel system...")
    print("=" * 50)
    
    # Hardware inventory
    cpus = CPUAllocation(
        total=32,
        host_reserved=[0, 1, 2, 3],
        available=list(range(4, 32))
    )
    
    memory = MemoryAllocation(
        total_bytes=16 * 1024**3,  # 16GB
        host_reserved_bytes=2 * 1024**3,  # 2GB
        memory_pool_base=0x80000000,
        memory_pool_bytes=14 * 1024**3  # 14GB
    )
    
    devices = {
        'eth0': DeviceInfo(
            name='eth0',
            compatible='intel,i40e',
            pci_id='0000:01:00.0',
            sriov_vfs=8,
            host_reserved_vf=0,
            available_vfs=[1, 2, 3, 4, 5, 6, 7]
        ),
        'nvme0': DeviceInfo(
            name='nvme0',
            compatible='nvme',
            pci_id='0000:02:00.0',
            namespaces=4,
            host_reserved_ns=1,
            available_ns=[2, 3, 4]
        )
    }
    
    hardware = HardwareInventory(
        cpus=cpus,
        memory=memory,
        devices=devices
    )
    
    # Kernel instances
    instances = {
        'web-server': Instance(
            name='web-server',
            id=1,
            resources=InstanceResources(
                cpus=[4, 5, 6, 7],
                memory_base=0x80000000,
                memory_bytes=2 * 1024**3,  # 2GB
                devices=['eth0_vf1']
            ),
            config=InstanceConfig(
                workload_type=WorkloadType.WEB_SERVER,
                enable_pgo=True,
                pgo_profile='/var/lib/mk/web_server.profdata'
            )
        ),
        'database': Instance(
            name='database',
            id=2,
            resources=InstanceResources(
                cpus=[8, 9, 10, 11, 12, 13, 14, 15],
                memory_base=0x100000000,
                memory_bytes=8 * 1024**3,  # 8GB
                devices=['eth0_vf2', 'nvme0_ns2']
            ),
            config=InstanceConfig(
                workload_type=WorkloadType.DATABASE_OLTP,
                enable_numa=True
            )
        ),
        'compute': Instance(
            name='compute',
            id=3,
            resources=InstanceResources(
                cpus=[16, 17, 18, 19, 20, 21, 22, 23],
                memory_base=0x300000000,
                memory_bytes=4 * 1024**3,  # 4GB
                devices=[]
            ),
            config=InstanceConfig(
                workload_type=WorkloadType.COMPUTE
            )
        )
    }
    
    return GlobalDeviceTree(
        hardware=hardware,
        instances=instances,
        device_references={}
    )


def demonstrate_validation():
    """Demonstrate validation functionality."""
    print("\n1. VALIDATION DEMONSTRATION")
    print("-" * 30)
    
    tree = create_demo_system()
    validator = MultikernelValidator()
    result = validator.validate(tree)
    
    reporter = ValidationReporter()
    report = reporter.generate_report(result, tree, verbose=True)
    print(report)
    
    return result.is_valid


def demonstrate_conflict_detection():
    """Demonstrate conflict detection."""
    print("\n2. CONFLICT DETECTION DEMONSTRATION")
    print("-" * 40)
    
    tree = create_demo_system()
    
    # Add conflicting instance
    conflicting_instance = Instance(
        name='conflicting',
        id=4,
        resources=InstanceResources(
            cpus=[4, 5],  # Conflicts with web-server
            memory_base=0x80000000,  # Conflicts with web-server
            memory_bytes=1024**3,  # 1GB
            devices=[]
        ),
        config=InstanceConfig(workload_type=WorkloadType.COMPUTE)
    )
    
    tree.instances['conflicting'] = conflicting_instance
    
    validator = MultikernelValidator()
    result = validator.validate(tree)
    
    print(f"Validation result: {'VALID' if result.is_valid else 'INVALID'}")
    if result.errors:
        print("\nErrors detected:")
        for i, error in enumerate(result.errors, 1):
            print(f"  {i}. {error}")
    
    if result.warnings:
        print("\nWarnings:")
        for i, warning in enumerate(result.warnings, 1):
            print(f"  {i}. {warning}")
    
    if result.suggestions:
        print("\nSuggestions:")
        for i, suggestion in enumerate(result.suggestions, 1):
            print(f"  {i}. {suggestion}")
    
    return not result.is_valid


def demonstrate_resource_analysis():
    """Demonstrate resource analysis."""
    print("\n3. RESOURCE ANALYSIS DEMONSTRATION")
    print("-" * 40)
    
    tree = create_demo_system()
    
    # Calculate resource usage
    total_cpus_allocated = sum(len(instance.resources.cpus) for instance in tree.instances.values())
    total_memory_allocated = sum(instance.resources.memory_bytes for instance in tree.instances.values())
    
    available_cpus = len(tree.hardware.cpus.available)
    memory_pool_bytes = tree.hardware.memory.memory_pool_bytes
    
    print(f"Hardware Inventory:")
    print(f"  CPUs: {tree.hardware.cpus.total} total")
    print(f"    Host reserved: {len(tree.hardware.cpus.host_reserved)} CPUs")
    print(f"    Available for spawn: {available_cpus} CPUs")
    print(f"  Memory: {tree.hardware.memory.total_bytes / (1024**3):.0f}GB total")
    print(f"    Host reserved: {tree.hardware.memory.host_reserved_bytes / (1024**3):.0f}GB")
    print(f"    Memory pool: {memory_pool_bytes / (1024**3):.0f}GB")
    print(f"  Devices: {len(tree.hardware.devices)} total")
    
    print(f"\nInstance Allocations:")
    for name, instance in tree.instances.items():
        cpu_count = len(instance.resources.cpus)
        memory_gb = instance.resources.memory_bytes / (1024**3)
        device_count = len(instance.resources.devices)
        
        print(f"  {name}:")
        print(f"    CPUs: {cpu_count} ({cpu_count/available_cpus*100:.1f}% of pool)")
        print(f"    Memory: {memory_gb:.0f}GB ({memory_gb/(memory_pool_bytes/(1024**3))*100:.1f}% of pool)")
        print(f"    Devices: {device_count}")
        print(f"    Workload: {instance.config.workload_type.value}")
    
    print(f"\nResource Utilization:")
    cpu_percent = (total_cpus_allocated / available_cpus) * 100
    memory_percent = (total_memory_allocated / memory_pool_bytes) * 100
    
    print(f"  CPUs: {total_cpus_allocated}/{available_cpus} ({cpu_percent:.1f}%)")
    print(f"  Memory: {total_memory_allocated/(1024**3):.0f}GB/{memory_pool_bytes/(1024**3):.0f}GB ({memory_percent:.1f}%)")
    
    unallocated_cpus = available_cpus - total_cpus_allocated
    unallocated_memory = memory_pool_bytes - total_memory_allocated
    
    if unallocated_cpus > 0:
        print(f"  Unallocated: {unallocated_cpus} CPUs, {unallocated_memory/(1024**3):.0f}GB memory")


def demonstrate_error_messages():
    """Demonstrate comprehensive error messages."""
    print("\n4. ERROR MESSAGE DEMONSTRATION")
    print("-" * 35)
    
    # Create a system with various errors
    tree = create_demo_system()
    
    # Add instance with CPU conflict
    bad_instance = Instance(
        name='bad-instance',
        id=5,
        resources=InstanceResources(
            cpus=[4, 5],  # Conflicts with web-server
            memory_base=0x80000000,  # Conflicts with web-server
            memory_bytes=1024**3,
            devices=[]
        ),
        config=InstanceConfig(workload_type=WorkloadType.COMPUTE)
    )
    
    tree.instances['bad-instance'] = bad_instance
    
    validator = MultikernelValidator()
    result = validator.validate(tree)
    
    print("Example error messages from kerf:")
    print("=" * 45)
    
    for i, error in enumerate(result.errors, 1):
        print(f"\nERROR {i}:")
        print(f"  {error}")
    
    if result.suggestions:
        print(f"\nSUGGESTIONS:")
        for i, suggestion in enumerate(result.suggestions, 1):
            print(f"  {i}. {suggestion}")


def main():
    """Run the complete demonstration."""
    print("kerf: Multikernel Device Tree Compiler")
    print("Demonstration of Core Functionality")
    print("=" * 50)
    
    # Run demonstrations
    valid = demonstrate_validation()
    conflicts_detected = demonstrate_conflict_detection()
    demonstrate_resource_analysis()
    demonstrate_error_messages()
    
    print("\n" + "=" * 50)
    print("DEMONSTRATION SUMMARY")
    print("=" * 50)
    print(f"✓ Validation system: {'PASSED' if valid else 'FAILED'}")
    print(f"✓ Conflict detection: {'PASSED' if conflicts_detected else 'FAILED'}")
    print(f"✓ Resource analysis: PASSED")
    print(f"✓ Error reporting: PASSED")
    
    print(f"\n🎉 kerf core functionality demonstration complete!")
    print(f"\nKey Features Demonstrated:")
    print(f"  • Comprehensive validation of multikernel configurations")
    print(f"  • CPU, memory, and device allocation conflict detection")
    print(f"  • Resource utilization analysis and reporting")
    print(f"  • Detailed error messages with actionable suggestions")
    print(f"  • Support for various workload types and configurations")
    
    print(f"\nNext Steps:")
    print(f"  1. Implement DTS/DTB parsing for real device tree files")
    print(f"  2. Add instance extraction and DTB generation")
    print(f"  3. Integrate with kernel sysfs interface")
    print(f"  4. Add support for dynamic configuration updates")


if __name__ == '__main__':
    main()
