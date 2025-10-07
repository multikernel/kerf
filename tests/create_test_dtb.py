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
Create a test DTB file for demonstration.
"""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import libfdt
from kerf.models import (
    GlobalDeviceTree, HardwareInventory, CPUAllocation, MemoryAllocation,
    DeviceInfo, Instance, InstanceResources, InstanceConfig, WorkloadType
)
from kerf.dtc.extractor import InstanceExtractor

def create_test_tree():
    """Create a test GlobalDeviceTree."""
    
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
                devices=['eth0_vf2']
            ),
            config=InstanceConfig(
                workload_type=WorkloadType.DATABASE_OLTP,
                enable_numa=True
            )
        )
    }
    
    return GlobalDeviceTree(
        hardware=hardware,
        instances=instances,
        device_references={}
    )

def main():
    """Create test DTB file."""
    print("Creating test DTB file...")
    
    tree = create_test_tree()
    extractor = InstanceExtractor()
    
    # Generate global DTB
    global_dtb = extractor.generate_global_dtb(tree)
    
    with open('test_global.dtb', 'wb') as f:
        f.write(global_dtb)
    
    print(f"✓ Created test_global.dtb ({len(global_dtb)} bytes)")
    
    # Generate instance DTBs
    instances = extractor.extract_all_instances(tree)
    for name, instance_dtb in instances.items():
        filename = f'test_{name}.dtb'
        with open(filename, 'wb') as f:
            f.write(instance_dtb)
        print(f"✓ Created {filename} ({len(instance_dtb)} bytes)")

if __name__ == '__main__':
    main()
