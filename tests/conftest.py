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
Pytest configuration and fixtures for kerf tests.
"""

import pytest
import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from kerf.models import (
    GlobalDeviceTree, HardwareInventory, CPUAllocation, MemoryAllocation,
    DeviceInfo, Instance, InstanceResources, InstanceConfig, WorkloadType
)


@pytest.fixture
def sample_hardware():
    """Create sample hardware inventory for testing."""
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
    
    return HardwareInventory(
        cpus=cpus,
        memory=memory,
        devices=devices
    )


@pytest.fixture
def sample_instances():
    """Create sample instances for testing."""
    return {
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


@pytest.fixture
def sample_tree(sample_hardware, sample_instances):
    """Create sample GlobalDeviceTree for testing."""
    return GlobalDeviceTree(
        hardware=sample_hardware,
        instances=sample_instances,
        device_references={}
    )
