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
kerf: Multikernel Management System

A comprehensive multikernel management system designed to orchestrate and manage multiple kernel instances on a single host.
"""

__version__ = "0.1.0"
__author__ = "Cong Wang"

# Export main runtime components for easy access
from .runtime import DeviceTreeManager
from .baseline import BaselineManager
from .dtc.overlay import OverlayGenerator
from .models import InstanceState
from .exceptions import (
    KerfError,
    ValidationError,
    ParseError,
    ResourceConflictError,
    ResourceExhaustionError,
    InvalidReferenceError,
    KernelInterfaceError,
    ResourceError,
)
from .resources import (
    get_available_cpus,
    get_allocated_cpus,
    get_allocated_memory_regions,
    find_available_memory_base,
    validate_cpu_allocation,
    validate_memory_allocation,
    find_next_instance_id,
)

__all__ = [
    # Core classes
    'DeviceTreeManager',
    'BaselineManager',
    'OverlayGenerator',
    # Models
    'InstanceState',
    # Exceptions
    'KerfError',
    'ValidationError',
    'ParseError',
    'ResourceConflictError',
    'ResourceExhaustionError',
    'InvalidReferenceError',
    'KernelInterfaceError',
    'ResourceError',
    # Resource utilities
    'get_available_cpus',
    'get_allocated_cpus',
    'get_allocated_memory_regions',
    'find_available_memory_base',
    'validate_cpu_allocation',
    'validate_memory_allocation',
    'find_next_instance_id',
]
