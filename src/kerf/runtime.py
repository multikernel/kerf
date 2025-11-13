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
Runtime management layer for device tree state with overlay support.

This module provides the DeviceTreeManager class which bridges imperative
kerf commands with the declarative device tree overlay interface exposed by the kernel.
It implements a transactional overlay pattern to maintain the kernel as the single
source of truth while supporting imperative operations.

Architecture:
============

The system uses a clean separation between baseline and overlays:

**Baseline** (`/sys/fs/multikernel/device_tree`):
- Contains ONLY hardware resources (no instances)
- Describes: CPUs, memory pool, devices available for allocation
- Set once via `kerf init --input=baseline.dts --apply`
- Updated rarely via explicit baseline update operations

**Overlays** (`/sys/fs/multikernel/overlays/`):
- Contains ONLY instance changes (no resource modifications)
- Applied via `/sys/fs/multikernel/overlays/new`
- Kernel creates `tx_XXX/` directories after applying
- Rollback via `rmdir /sys/fs/multikernel/overlays/tx_XXX/`

DeviceTreeManager workflow (for runtime operations):
1. Read: Load baseline (resources) + all applied overlays (instances)
2. Merge: Build effective state (baseline resources + overlay instances)
3. Modify: Apply imperative operation to create modified state
4. Validate: Validate entire modified state + enforce overlay constraints
5. Generate: Create DTBO overlay representing instance delta only
6. Apply: Write DTBO to `/sys/fs/multikernel/overlays/new`
7. Track: Kernel creates `tx_XXX/` directory with transaction metadata

Example Usage (for future commands):
====================================

```python
# In kerf/create/main.py
from kerf.runtime import DeviceTreeManager
from kerf.resources import validate_cpu_allocation, find_next_instance_id, ...

@click.command()
def create(name: str, cpus: List[int], memory: int):
    manager = DeviceTreeManager()
    
    def create_instance(current: GlobalDeviceTree) -> GlobalDeviceTree:
        # Create modified state
        import copy
        modified = copy.deepcopy(current)
        
        # Check existence
        if name in modified.instances:
            raise ValueError(f"Instance '{name}' already exists")
        
        # Validate resources (against baseline)
        validate_cpu_allocation(modified, cpus)
        
        # Find memory base
        memory_base = find_available_memory_base(modified, memory)
        if not memory_base:
            raise ResourceError("No memory available")
        
        # Create instance
        instance = Instance(
            name=name,
            id=find_next_instance_id(modified),
            resources=InstanceResources(
                cpus=cpus,
                memory_base=memory_base,
                memory_bytes=memory,
                devices=[]
            )
        )
        modified.instances[name] = instance
        
        return modified
    
    # Apply transactionally via overlay
    tx_id = manager.apply_operation(create_instance)
    click.echo(f"Created instance '{name}' (transaction {tx_id})")
```

All operations follow this same pattern, ensuring consistency and validation.
"""

import os
import re
from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple
from contextlib import contextmanager

from .dtc.parser import DeviceTreeParser
from .dtc.overlay import OverlayGenerator
from .dtc.validator import MultikernelValidator
from .baseline import BaselineManager
from .models import GlobalDeviceTree
from .exceptions import ValidationError, ParseError, KernelInterfaceError


class DeviceTreeManager:
    """
    Manages device tree state with kernel overlay interface.
    
    This class provides a transactional interface for modifying the device tree
    using overlays. Each operation generates a device tree overlay (DTBO) that
    represents the incremental change.
    
    Attributes:
        baseline_path: Path to baseline device tree
        overlays_dir: Path to overlays directory
        overlays_new: Path to write new overlays
        lock_file: Path to lock file for concurrency control
        parser: DeviceTreeParser instance for reading state
        overlay_gen: OverlayGenerator instance for creating overlays
        validator: MultikernelValidator instance for validation
    """
    
    DEFAULT_BASELINE_PATH = "/sys/fs/multikernel/device_tree"
    DEFAULT_OVERLAYS_DIR = "/sys/fs/multikernel/overlays"
    
    def __init__(
        self,
        baseline_path: Optional[str] = None,
        overlays_dir: Optional[str] = None
    ):
        """
        Initialize DeviceTreeManager.
        
        Args:
            baseline_path: Path to baseline device tree. Defaults to
                         /sys/fs/multikernel/device_tree
            overlays_dir: Path to overlays directory. Defaults to
                        /sys/fs/multikernel/overlays
        """
        self.baseline_path = Path(baseline_path or self.DEFAULT_BASELINE_PATH)
        self.overlays_dir = Path(overlays_dir or self.DEFAULT_OVERLAYS_DIR)
        self.overlays_new = self.overlays_dir / "new"

        lock_dir = Path("/var/run")
        if not lock_dir.exists() or not os.access(lock_dir, os.W_OK):
            lock_dir = Path("/tmp")
        self.lock_file = lock_dir / "kerf.lock"
        
        self.parser = DeviceTreeParser()
        self.overlay_gen = OverlayGenerator()
        self.validator = MultikernelValidator()
        self.baseline_mgr = BaselineManager(str(self.baseline_path))
    
    def read_baseline(self) -> GlobalDeviceTree:
        """
        Read baseline device tree from kernel (resources only).
        
        Returns:
            GlobalDeviceTree model representing baseline (resources only, no instances)
            
        Raises:
            KernelInterfaceError: If kernel interface is inaccessible
            ParseError: If device tree cannot be parsed
        """
        return self.baseline_mgr.read_baseline()
    
    def read_applied_overlays(self) -> List[Tuple[str, bytes]]:
        """
        Read all applied overlays from kernel.
        
        Returns:
            List of (transaction_id, dtbo_data) tuples
        """
        overlays = []
        
        if not self.overlays_dir.exists():
            return overlays
        
        for tx_dir in self.overlays_dir.iterdir():
            if not tx_dir.is_dir():
                continue
            
            match = re.match(r'^tx_(\d+)$', tx_dir.name)
            if not match:
                continue
            
            tx_id = match.group(1)
            dtbo_path = tx_dir / "dtbo"
            
            if dtbo_path.exists():
                try:
                    with open(dtbo_path, 'rb') as f:
                        dtbo_data = f.read()
                    overlays.append((tx_id, dtbo_data))
                except OSError:
                    # Skip if we can't read it
                    continue
        
        # Sort by transaction ID
        overlays.sort(key=lambda x: int(x[0]))
        return overlays
    
    def read_current_state(self) -> GlobalDeviceTree:
        """
        Read current effective device tree state (baseline + all overlays).
        
        This reads the baseline (resources only) and all applied overlays (instances),
        then merges them to produce the effective current state.
        
        Returns:
            GlobalDeviceTree model representing current effective state
            
        Raises:
            KernelInterfaceError: If kernel interface is inaccessible
            ParseError: If device tree cannot be parsed
        """
        # Read baseline (resources only, no instances)
        baseline = self.read_baseline()
        
        import copy
        effective = copy.deepcopy(baseline)
        if not hasattr(effective, 'instances') or effective.instances is None:
            effective.instances = {}
        else:
            effective.instances = {}
        
        applied_overlays = self.read_applied_overlays()
        for tx_id, dtbo_data in applied_overlays:
            try:
                overlay_tree = self.parser.parse_dtb_from_bytes(dtbo_data)
                effective = self._merge_overlay(effective, overlay_tree)
            except ParseError:
                continue
        
        return effective
    
    def _merge_overlay(
        self,
        base: GlobalDeviceTree,
        overlay: GlobalDeviceTree
    ) -> GlobalDeviceTree:
        """
        Merge overlay into base tree.
        
        Overlay instances replace or add to base instances.
        Overlays must NOT modify resources (enforced by validation).
        
        Args:
            base: Base device tree (baseline + previous overlays)
            overlay: Overlay device tree (instance changes only)
            
        Returns:
            Merged device tree
        """
        import copy
        
        merged = copy.deepcopy(base)
        
        # Validate overlay doesn't modify resources
        if base.hardware != overlay.hardware:
            raise ValidationError(
                "Overlay cannot modify hardware resources. "
                "Resources are defined in baseline only. "
                "Use 'kerf baseline update' to change resources."
            )
        
        # Overlay instances merge into base (add or replace)
        if overlay.instances:
            for name, instance in overlay.instances.items():
                merged.instances[name] = copy.deepcopy(instance)
        
        return merged
    
    def apply_overlay(self, current: GlobalDeviceTree, modified: GlobalDeviceTree) -> str:
        """
        Apply overlay by writing DTBO to kernel.
        
        Generates an overlay representing the difference between current and
        modified states, then writes it to /sys/fs/multikernel/overlays/new.
        The kernel applies it and creates a transaction directory.
        
        Args:
            current: Current effective state (before change)
            modified: Modified state (after change)
            
        Returns:
            Transaction ID (from kernel-created directory)
            
        Raises:
            ValidationError: If modified state validation fails
            KernelInterfaceError: If overlay application fails
        """
        # Validate overlay doesn't modify resources
        if current.hardware != modified.hardware:
            raise ValidationError(
                "Overlays cannot modify hardware resources. "
                "Resources are defined in baseline only. "
                "Current and modified states have different hardware definitions."
            )
        
        # Validate modified state before generating overlay
        validation_result = self.validator.validate(modified)
        if not validation_result.is_valid:
            error_msg = "Cannot apply overlay with invalid state:\n"
            error_msg += "\n".join(f"  - {err}" for err in validation_result.errors)
            if validation_result.warnings:
                error_msg += "\n\nWarnings:\n"
                error_msg += "\n".join(f"  - {warn}" for warn in validation_result.warnings)
            raise ValidationError(error_msg)
        
        try:
            dtbo_data = self.overlay_gen.generate_overlay(current, modified)
        except Exception as e:
            raise KernelInterfaceError(
                f"Failed to generate overlay: {e}"
            ) from e
        
        try:
            if not self.overlays_new.exists():
                raise KernelInterfaceError(
                    f"Overlay interface not found: {self.overlays_new}. "
                    "Is the multikernel kernel module loaded?"
                )
            
            with open(self.overlays_new, 'wb') as f:
                f.write(dtbo_data)

            tx_id = self._find_latest_transaction()
            if not tx_id:
                raise KernelInterfaceError(
                    "Overlay written but kernel did not create transaction directory"
                )
            
            # Verify transaction succeeded by checking status
            tx_dir = self.overlays_dir / f"tx_{tx_id}"
            status_file = tx_dir / "status"
            
            if status_file.exists():
                try:
                    with open(status_file, 'r') as f:
                        status = f.read().strip()
                    if status not in ("applied", "success", "ok"):
                        error_msg = f"Overlay transaction {tx_id} failed with status: '{status}'"
                        instance_file = tx_dir / "instance"
                        if instance_file.exists():
                            try:
                                with open(instance_file, 'r') as f:
                                    instance_name = f.read().strip()
                                error_msg += f" (instance: {instance_name})"
                            except OSError:
                                pass
                        
                        raise KernelInterfaceError(error_msg)
                except OSError:
                    # If we can't read status, assume it might still be processing
                    # But warn that we couldn't verify
                    pass
            
            return tx_id
            
        except OSError as e:
            raise KernelInterfaceError(
                f"Failed to write overlay to {self.overlays_new}: {e}"
            ) from e
    
    def _find_latest_transaction(self) -> Optional[str]:
        """Find the latest transaction ID from kernel-created directories."""
        if not self.overlays_dir.exists():
            return None
        
        max_tx_id = None
        max_id = -1
        
        for tx_dir in self.overlays_dir.iterdir():
            if not tx_dir.is_dir():
                continue
            
            match = re.match(r'^tx_(\d+)$', tx_dir.name)
            if match:
                tx_id = int(match.group(1))
                if tx_id > max_id:
                    max_id = tx_id
                    max_tx_id = match.group(1)
        
        return max_tx_id
    
    def rollback_transaction(self, tx_id: str) -> None:
        """
        Rollback a transaction by removing its overlay.
        
        Args:
            tx_id: Transaction ID to rollback
            
        Raises:
            KernelInterfaceError: If rollback fails
        """
        tx_dir = self.overlays_dir / f"tx_{tx_id}"
        
        if not tx_dir.exists():
            raise KernelInterfaceError(
                f"Transaction {tx_id} not found: {tx_dir}"
            )
        
        try:
            # Remove transaction directory (kernel handles rollback)
            tx_dir.rmdir()
        except OSError as e:
            raise KernelInterfaceError(
                f"Failed to rollback transaction {tx_id}: {e}"
            ) from e
    
    def list_transactions(self) -> List[Dict[str, str]]:
        """
        List all applied transactions.
        
        Returns:
            List of transaction info dicts with keys: id, status, instance
        """
        transactions = []
        
        if not self.overlays_dir.exists():
            return transactions
        
        for tx_dir in self.overlays_dir.iterdir():
            if not tx_dir.is_dir():
                continue
            
            match = re.match(r'^tx_(\d+)$', tx_dir.name)
            if not match:
                continue
            
            tx_id = match.group(1)
            tx_info = {"id": tx_id}
            
            # Read transaction metadata
            status_file = tx_dir / "status"
            instance_file = tx_dir / "instance"
            
            if status_file.exists():
                try:
                    with open(status_file, 'r') as f:
                        tx_info["status"] = f.read().strip()
                except OSError:
                    tx_info["status"] = "unknown"
            
            if instance_file.exists():
                try:
                    with open(instance_file, 'r') as f:
                        tx_info["instance"] = f.read().strip()
                except OSError:
                    pass
            
            transactions.append(tx_info)
        
        # Sort by transaction ID
        transactions.sort(key=lambda x: int(x["id"]))
        return transactions
    
    @contextmanager
    def _acquire_lock(self):
        """
        Acquire file lock for concurrency safety.
        
        Yields:
            Lock context
            
        Raises:
            KernelInterfaceError: If lock cannot be acquired
        """
        lock_acquired = False
        
        try:
            max_retries = 10
            retry_delay = 0.1
            
            for attempt in range(max_retries):
                try:
                    if not self.lock_file.exists():
                        self.lock_file.touch(exist_ok=False)
                        lock_acquired = True
                        break
                    import time
                    time.sleep(retry_delay)
                except FileExistsError:
                    import time
                    time.sleep(retry_delay)
                    continue
            
            if not lock_acquired:
                raise KernelInterfaceError(
                    f"Could not acquire lock after {max_retries} attempts. "
                    "Another kerf operation may be in progress."
                )
            
            yield
            
        finally:
            if lock_acquired and self.lock_file.exists():
                self.lock_file.unlink()
    
    def apply_operation(
        self,
        operation: Callable[[GlobalDeviceTree], GlobalDeviceTree]
    ) -> str:
        """
        Apply an operation transactionally via overlay.
        
        This is the core method that implements the overlay pattern:
        1. Read baseline and applied overlays to get current effective state
        2. Apply operation to get modified state
        3. Validate modified state
        4. Generate overlay (delta between current effective and modified)
        5. Apply overlay to kernel
        
        The operation function receives the current effective GlobalDeviceTree
        and must return a modified GlobalDeviceTree (typically a copy).
        
        Args:
            operation: Callable that takes GlobalDeviceTree and returns modified
                      GlobalDeviceTree. Should raise appropriate exceptions for errors.
                      
        Returns:
            Transaction ID from applied overlay
            
        Raises:
            ValidationError: If resulting state is invalid
            KernelInterfaceError: If kernel interface operations fail
            Any exceptions raised by the operation function
        """
        with self._acquire_lock():
            # Read current effective state (baseline + all overlays)
            current = self.read_current_state()
            
            # Apply operation (returns modified state)
            modified = operation(current)
            
            # Generate overlay comparing current effective to modified
            # Each overlay represents incremental change from current state
            tx_id = self.apply_overlay(current, modified)
            
            return tx_id
    
    def get_instance_names(self) -> List[str]:
        """
        Get list of all instance names in current effective state.
        
        Returns:
            List of instance names (empty list if kernel not initialized)
        """
        try:
            tree = self.read_current_state()
            return list(tree.instances.keys())
        except (KernelInterfaceError, ParseError):
            return []
    
    def has_instance(self, name: str) -> bool:
        """
        Check if an instance exists in the current effective state.
        
        Args:
            name: Instance name to check
            
        Returns:
            True if instance exists, False otherwise
        """
        return name in self.get_instance_names()
