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
Baseline device tree management.

This module provides the BaselineManager class for managing the baseline device tree,
which contains only hardware resources and is set during system initialization.
"""

import os
from pathlib import Path
from typing import Optional

from .dtc.parser import DeviceTreeParser
from .dtc.extractor import InstanceExtractor
from .dtc.validator import MultikernelValidator
from .models import GlobalDeviceTree
from .exceptions import ValidationError, ParseError, KernelInterfaceError


class BaselineManager:
    """
    Manages root device tree in kernel.
    
    The root device_tree path serves dual purposes:
    - Writing (via write_baseline): Used during initialization to write a
      resources-only baseline. Must contain only hardware resources, no instances.
    - Reading (via read_baseline): Returns complete current state including
      both resources and instances. The kernel keeps this up-to-date by
      automatically merging all applied overlays.
    
    Attributes:
        baseline_path: Path to root device tree in kernel
        parser: DeviceTreeParser instance for reading state
        extractor: InstanceExtractor instance for generating DTB
        validator: MultikernelValidator instance for validation
    """
    
    DEFAULT_BASELINE_PATH = "/sys/fs/multikernel/device_tree"
    
    def __init__(self, baseline_path: Optional[str] = None):
        """
        Initialize BaselineManager.
        
        Args:
            baseline_path: Path to baseline device tree. Defaults to
                         /sys/fs/multikernel/device_tree
        """
        self.baseline_path = Path(baseline_path or self.DEFAULT_BASELINE_PATH)
        
        self.parser = DeviceTreeParser()
        self.extractor = InstanceExtractor()
        self.validator = MultikernelValidator()
    
    def validate_baseline(self, tree: GlobalDeviceTree) -> None:
        """
        Validate that tree is a valid baseline (resources only, no instances).
        
        Args:
            tree: GlobalDeviceTree to validate
            
        Raises:
            ValidationError: If tree is not a valid baseline
        """
        # Baseline must not contain instances
        if tree.instances:
            instance_names = ", ".join(tree.instances.keys())
            raise ValidationError(
                f"Baseline device tree must not contain instances. "
                f"Found instances: {instance_names}. "
                f"Instances should be created via overlays using 'kerf create'."
            )
        
        # Baseline must contain resources
        if not tree.hardware:
            raise ValidationError(
                "Baseline device tree must contain hardware resources section."
            )
        
        # Validate resources structure
        if not tree.hardware.cpus:
            raise ValidationError("Baseline must contain CPU allocation information")
        
        if not tree.hardware.memory:
            raise ValidationError("Baseline must contain memory allocation information")
    
    def write_baseline(self, tree: GlobalDeviceTree) -> None:
        """
        Write baseline device tree to kernel.
        
        Validates that the tree contains only resources (no instances) before writing.
        This is used for initial system setup via 'kerf init'.
        
        Args:
            tree: GlobalDeviceTree containing only resources (no instances)
            
        Raises:
            ValidationError: If tree contains instances or invalid resources
            KernelInterfaceError: If write operation fails
        """
        # Validate baseline structure (no instances)
        self.validate_baseline(tree)
        
        # Validate resources are valid
        validation_result = self.validator.validate(tree)
        if not validation_result.is_valid:
            error_msg = "Cannot write invalid baseline:\n"
            error_msg += "\n".join(f"  - {err}" for err in validation_result.errors)
            raise ValidationError(error_msg)
        
        # Generate DTB from model
        try:
            dtb_data = self.extractor.generate_global_dtb(tree)
        except Exception as e:
            raise KernelInterfaceError(
                f"Failed to generate baseline device tree blob: {e}"
            ) from e
        
        # The kernfs write operation is handled atomically by the kernel
        try:
            with open(self.baseline_path, 'wb') as f:
                f.write(dtb_data)
            
        except OSError as e:
            raise KernelInterfaceError(
                f"Failed to write baseline to {self.baseline_path}: {e}"
            ) from e
    
    def read_baseline(self) -> GlobalDeviceTree:
        """
        Read root device tree from kernel.

        Returns:
            GlobalDeviceTree model representing current complete state
            
        Raises:
            KernelInterfaceError: If kernel interface is inaccessible
            ParseError: If device tree cannot be parsed
        """
        try:
            if not self.baseline_path.exists():
                raise KernelInterfaceError(
                    f"Root device tree not found: {self.baseline_path}. "
                    "Initialize it first with 'kerf init'."
                )
            
            with open(self.baseline_path, 'rb') as f:
                dtb_data = f.read()
            
            if not dtb_data:
                raise KernelInterfaceError(
                    f"Root device tree is empty. "
                    "Initialize it first with 'kerf init'."
                )
            
            tree = self.parser.parse_dtb_from_bytes(dtb_data)

            return tree
            
        except OSError as e:
            raise KernelInterfaceError(
                f"Failed to read root device tree from {self.baseline_path}: {e}"
            ) from e
        except ParseError as e:
            raise ParseError(
                f"Failed to parse root device tree: {e}"
            ) from e

