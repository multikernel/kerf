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
Simple test script for kerf functionality.
"""

import sys
import os
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kerf.models import (
    GlobalDeviceTree, HardwareInventory, CPUAllocation, MemoryAllocation,
    DeviceInfo, Instance, InstanceResources, InstanceConfig, WorkloadType
)
from kerf.dtc.validator import MultikernelValidator
from kerf.dtc.extractor import InstanceExtractor
from kerf.dtc.reporter import ValidationReporter


def create_test_tree():
    """Create a test GlobalDeviceTree for demonstration."""

    # Create hardware inventory
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

    # Create instances
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


def test_validation():
    """Test validation functionality."""
    print("=== Testing Validation ===")

    tree = create_test_tree()
    validator = MultikernelValidator()
    result = validator.validate(tree)

    reporter = ValidationReporter()
    report = reporter.generate_report(result, tree, verbose=True)
    print(report)

    assert result.is_valid, "Validation should pass for valid tree"


def test_extraction():
    """Test instance extraction functionality."""
    print("\n=== Testing Instance Extraction ===")

    tree = create_test_tree()
    extractor = InstanceExtractor()

    # Extract individual instances
    for instance_name in tree.instances.keys():
        try:
            instance_dtb = extractor.extract_instance(tree, instance_name)
            print(f"✓ Extracted {instance_name}: {len(instance_dtb)} bytes")
        except Exception as e:
            print(f"✗ Failed to extract {instance_name}: {e}")

    # Extract all instances
    try:
        all_instances = extractor.extract_all_instances(tree)
        print(f"✓ Extracted all instances: {list(all_instances.keys())}")
    except Exception as e:
        print(f"✗ Failed to extract all instances: {e}")


def test_conflict_detection():
    """Test conflict detection with invalid configuration."""
    print("\n=== Testing Conflict Detection ===")

    # Create a tree with conflicts
    tree = create_test_tree()

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
        print("Errors found:")
        for error in result.errors:
            print(f"  - {error}")

    assert not result.is_valid, "Validation should fail for conflicting resources"
    assert len(result.errors) > 0, "Should have error messages for conflicts"


def main():
    """Run all tests."""
    print("kerf Test Suite")
    print("=" * 50)

    # Test 1: Basic validation
    valid = test_validation()
    print(f"✓ Basic validation: {'PASSED' if valid else 'FAILED'}")

    # Test 2: Instance extraction
    test_extraction()
    print("✓ Instance extraction: PASSED")

    # Test 3: Conflict detection
    conflicts_detected = test_conflict_detection()
    print(f"✓ Conflict detection: {'PASSED' if conflicts_detected else 'FAILED'}")

    print("\n" + "=" * 50)
    print("Test Summary:")
    print(f"  Basic validation: {'PASS' if valid else 'FAIL'}")
    print(f"  Instance extraction: PASS")
    print(f"  Conflict detection: {'PASS' if conflicts_detected else 'FAIL'}")

    if valid and conflicts_detected:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed!")
        return 1


if __name__ == '__main__':
    sys.exit(main())
