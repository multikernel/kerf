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
Create kernel instance command.

This command creates a new kernel instance with specified resource allocations.
Resources are validated against the baseline and existing instances before
creating an overlay and applying it to the kernel.
"""

import sys
import re
from typing import List, Optional, Tuple, Union
import click

from ..runtime import DeviceTreeManager
from ..models import Instance, InstanceResources
from ..resources import (
    validate_cpu_allocation,
    validate_memory_allocation,
    find_available_memory_base,
    find_next_instance_id,
    get_available_cpus
)
from ..exceptions import ValidationError, KernelInterfaceError, ResourceError, ParseError


def parse_cpu_spec(cpu_spec: str) -> List[int]:
    """
    Parse CPU specification string into explicit CPU IDs.
    
    Supports formats:
    - "4" (single CPU ID 4)
    - "4-7" (range of CPUs: 4, 5, 6, 7)
    - "4,5,6,7" (comma-separated list)
    - "4-7,10-12" (mixed ranges and lists)
    
    Args:
        cpu_spec: CPU specification string
        
    Returns:
        List of explicit CPU IDs (sorted)
        
    Raises:
        ValueError: If specification is invalid
    """
    cpu_spec = cpu_spec.strip()
    
    # Explicit CPU specification
    cpus = set()
    
    # Split by comma
    parts = [p.strip() for p in cpu_spec.split(',')]
    
    for part in parts:
        if '-' in part:
            # Range format: "4-7"
            try:
                start, end = part.split('-', 1)
                start = int(start.strip())
                end = int(end.strip())
                if start > end:
                    raise ValueError(f"Invalid CPU range: {start} > {end}")
                cpus.update(range(start, end + 1))
            except ValueError as e:
                raise ValueError(f"Invalid CPU range format '{part}': {e}")
        else:
            # Single CPU
            try:
                cpus.add(int(part.strip()))
            except ValueError as e:
                raise ValueError(f"Invalid CPU ID '{part}': {e}")
    
    if not cpus:
        raise ValueError("CPU specification must include at least one CPU")
    
    return sorted(list(cpus))


def allocate_cpus_from_pool(
    tree,
    count: int,
    cpu_affinity: str = "compact",
    numa_nodes: Optional[List[int]] = None
) -> List[int]:
    """
    Allocate specified number of CPUs from available pool with topology awareness.
    
    Args:
        tree: GlobalDeviceTree to analyze
        count: Number of CPUs to allocate
        cpu_affinity: CPU affinity policy - "compact", "spread", or "local"
        numa_nodes: Preferred NUMA node IDs (optional). If specified, allocates
                   CPUs only from these NUMA nodes
        
    Returns:
        List of allocated CPU IDs (sorted)
        
    Raises:
        ResourceError: If not enough CPUs available
    """
    available = sorted(list(get_available_cpus(tree)))
    
    # Filter by NUMA nodes if specified
    if numa_nodes and tree.hardware.topology and tree.hardware.topology.numa_nodes:
        numa_filtered = []
        for cpu_id in available:
            cpu_numa = tree.hardware.topology.get_numa_node_for_cpu(cpu_id)
            if cpu_numa is not None and cpu_numa in numa_nodes:
                numa_filtered.append(cpu_id)
        available = numa_filtered
    
    if len(available) < count:
        if numa_nodes:
            raise ResourceError(
                f"Not enough CPUs available in NUMA nodes {numa_nodes}: "
                f"requested {count}, but only {len(available)} available"
            )
        else:
            raise ResourceError(
                f"Not enough CPUs available: requested {count}, "
                f"but only {len(available)} available in pool"
            )
    
    if cpu_affinity == "compact":
        # Allocate from same NUMA node, preferably consecutive CPUs
        if numa_nodes and tree.hardware.topology and tree.hardware.topology.numa_nodes:
            # Try to allocate all from single NUMA node
            for numa_node_id in numa_nodes:
                numa_cpus = [
                    cpu for cpu in available
                    if tree.hardware.topology.get_numa_node_for_cpu(cpu) == numa_node_id
                ]
                if len(numa_cpus) >= count:
                    # Find consecutive CPUs in this NUMA node
                    for i in range(len(numa_cpus) - count + 1):
                        consecutive = True
                        for j in range(count - 1):
                            if numa_cpus[i + j + 1] != numa_cpus[i + j] + 1:
                                consecutive = False
                                break
                        if consecutive:
                            return sorted(numa_cpus[i:i+count])
                    # No consecutive range, take first N
                    return sorted(numa_cpus[:count])
        
        # No NUMA topology or not constrained to specific nodes
        # Find consecutive CPUs
        for i in range(len(available) - count + 1):
            consecutive = True
            for j in range(count - 1):
                if available[i + j + 1] != available[i + j] + 1:
                    consecutive = False
                    break
            if consecutive:
                return available[i:i+count]
        
        # If no consecutive range found, take first N
        return available[:count]
    
    elif cpu_affinity == "spread":
        # Distribute CPUs across NUMA nodes if topology available
        if numa_nodes and tree.hardware.topology and tree.hardware.topology.numa_nodes:
            # Spread across specified NUMA nodes
            numa_cpu_lists = {}
            for numa_node_id in numa_nodes:
                numa_cpus = [
                    cpu for cpu in available
                    if tree.hardware.topology.get_numa_node_for_cpu(cpu) == numa_node_id
                ]
                if numa_cpus:
                    numa_cpu_lists[numa_node_id] = sorted(numa_cpus)
            
            if not numa_cpu_lists:
                raise ResourceError(
                    f"No available CPUs in specified NUMA nodes: {numa_nodes}"
                )
            
            # Distribute evenly across NUMA nodes
            allocated = []
            numa_indices = {node_id: 0 for node_id in numa_cpu_lists.keys()}
            
            for i in range(count):
                # Round-robin across NUMA nodes
                numa_idx = i % len(numa_cpu_lists)
                numa_node_id = list(numa_cpu_lists.keys())[numa_idx]
                cpu_list = numa_cpu_lists[numa_node_id]
                
                if numa_indices[numa_node_id] < len(cpu_list):
                    allocated.append(cpu_list[numa_indices[numa_node_id]])
                    numa_indices[numa_node_id] += 1
                else:
                    # This NUMA node exhausted, try next
                    for next_numa_id in numa_cpu_lists.keys():
                        if numa_indices[next_numa_id] < len(numa_cpu_lists[next_numa_id]):
                            allocated.append(numa_cpu_lists[next_numa_id][numa_indices[next_numa_id]])
                            numa_indices[next_numa_id] += 1
                            break
            
            return sorted(allocated)
        
        # No NUMA topology, spread evenly across available range
        if count == 1:
            return [available[0]]
        
        step = (len(available) - 1) / (count - 1) if count > 1 else 1
        indices = [int(i * step) for i in range(count)]
        return sorted([available[i] for i in indices])
    
    elif cpu_affinity == "local":
        # Allocate CPUs and ensure they're co-located with memory on same NUMA node
        # This requires NUMA topology
        if not tree.hardware.topology or not tree.hardware.topology.numa_nodes:
            raise ResourceError(
                "CPU affinity 'local' requires NUMA topology information. "
                "Use 'compact' or 'spread' instead, or specify NUMA topology in baseline."
            )
        
        # For 'local', we need to ensure CPUs are from same NUMA node as memory
        # This will be validated later when memory is allocated
        # For now, prefer single NUMA node allocation
        if numa_nodes and len(numa_nodes) > 0:
            # Use first specified NUMA node
            numa_node_id = numa_nodes[0]
            numa_cpus = [
                cpu for cpu in available
                if tree.hardware.topology.get_numa_node_for_cpu(cpu) == numa_node_id
            ]
            if len(numa_cpus) >= count:
                return sorted(numa_cpus[:count])
            else:
                raise ResourceError(
                    f"Not enough CPUs in NUMA node {numa_node_id}: "
                    f"requested {count}, but only {len(numa_cpus)} available"
                )
        else:
            # Find first NUMA node with enough CPUs
            for numa_node_id, numa_node in tree.hardware.topology.numa_nodes.items():
                numa_cpus = [
                    cpu for cpu in available
                    if tree.hardware.topology.get_numa_node_for_cpu(cpu) == numa_node_id
                ]
                if len(numa_cpus) >= count:
                    return sorted(numa_cpus[:count])
            
            raise ResourceError(
                f"No single NUMA node has {count} available CPUs for 'local' affinity"
            )
    
    else:
        raise ValueError(f"Unknown CPU affinity policy: {cpu_affinity}")


def parse_memory_spec(memory_spec: str) -> int:
    """
    Parse memory specification string into bytes.
    
    Supports formats:
    - "2GB" or "2gb"
    - "2048MB" or "2048mb"
    - "2097152KB" or "2097152kb"
    - "2147483648" (raw bytes)
    
    Args:
        memory_spec: Memory specification string
        
    Returns:
        Size in bytes
        
    Raises:
        ValueError: If specification is invalid
    """
    memory_spec = memory_spec.strip().upper()
    
    # Check for unit suffix
    multipliers = {
        'KB': 1024,
        'MB': 1024 ** 2,
        'GB': 1024 ** 3,
        'TB': 1024 ** 4,
    }
    
    for unit, multiplier in multipliers.items():
        if memory_spec.endswith(unit):
            try:
                value = float(memory_spec[:-len(unit)].strip())
                return int(value * multiplier)
            except ValueError:
                raise ValueError(f"Invalid memory value '{memory_spec}': expected number before {unit}")
    
    # No unit, assume bytes
    try:
        return int(memory_spec)
    except ValueError:
        raise ValueError(f"Invalid memory specification '{memory_spec}': expected size with unit (GB/MB/KB) or bytes")


def parse_memory_base(base_spec: str) -> int:
    """
    Parse memory base address specification.
    
    Supports formats:
    - "0x80000000" (hexadecimal)
    - "2147483648" (decimal)
    
    Args:
        base_spec: Base address specification
        
    Returns:
        Base address as integer
        
    Raises:
        ValueError: If specification is invalid
    """
    base_spec = base_spec.strip()
    
    if base_spec.startswith('0x') or base_spec.startswith('0X'):
        try:
            return int(base_spec, 16)
        except ValueError:
            raise ValueError(f"Invalid hexadecimal base address '{base_spec}'")
    else:
        try:
            return int(base_spec)
        except ValueError:
            raise ValueError(f"Invalid base address '{base_spec}'")


def parse_device_list(device_spec: Optional[str]) -> List[str]:
    """
    Parse device specification string into list of device references.
    
    Supports formats:
    - "eth0_vf1" (single device)
    - "eth0_vf1,nvme0_ns2" (comma-separated)
    
    Args:
        device_spec: Device specification string or None
        
    Returns:
        List of device reference strings
    """
    if not device_spec:
        return []
    
    devices = [d.strip() for d in device_spec.split(',') if d.strip()]
    return devices


@click.command(context_settings={'allow_extra_args': True})
@click.pass_context
@click.argument('name', required=False)
@click.option('--id', type=int, help='Instance ID (1-511, auto-assigned if not specified)')
@click.option('--cpus', '-c', 
              help='Explicit CPU allocation: CPU IDs (e.g., "4" for CPU 4, "4-7" for range, "4,5,6,7" for list, "4-7,10-12" for mixed). Mutually exclusive with --cpu-count')
@click.option('--cpu-count', type=int,
              help='Auto-allocate specified number of CPUs from available pool. Mutually exclusive with --cpus')
@click.option('--cpu-affinity', type=click.Choice(['compact', 'spread', 'local']), default='compact',
              help='CPU affinity policy: compact (same NUMA node, consecutive), spread (across NUMA nodes), or local (co-locate with memory)')
@click.option('--numa-nodes', help='Preferred NUMA node IDs (comma-separated, e.g., "0" or "0,1"). CPUs allocated from these nodes only')
@click.option('--memory-policy', type=click.Choice(['local', 'interleave', 'bind']),
              help='Memory allocation policy: local (same NUMA as CPUs), interleave (across NUMA nodes), or bind (specific NUMA nodes)')
@click.option('--memory', '-m', required=True, help='Memory allocation (e.g., "2GB", "2048MB", or bytes)')
@click.option('--memory-base', help='Memory base address (hex: 0x80000000 or decimal, auto-assigned if not specified)')
@click.option('--devices', '-d', help='Device references (comma-separated, e.g., "eth0_vf1,nvme0_ns2")')
@click.option('--dry-run', is_flag=True, help='Validate without applying to kernel')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def create(
    ctx: click.Context,
    name: Optional[str],
    id: Optional[int],
    cpus: Optional[str],
    cpu_count: Optional[int],
    cpu_affinity: str,
    numa_nodes: Optional[str],
    memory_policy: Optional[str],
    memory: str,
    memory_base: Optional[str],
    devices: Optional[str],
    dry_run: bool,
    verbose: bool
):
    """
    Create a new kernel instance with specified resources.
    
    This command creates a new kernel instance and allocates resources to it.
    Resources are validated against the baseline (must be within available pool)
    and existing instances (no overlaps allowed).
    
    The instance name is a required positional argument that can appear anywhere
    after the 'create' command.
    
    Examples:
    
        # Create instance with CPUs 4-7 and 2GB memory (auto-assigned base)
        kerf create web-server --cpus=4-7 --memory=2GB
        
        # Create instance with name after options
        kerf create --cpus=8-15 --memory=8GB web-server
        
        # Create instance with specific memory base address
        kerf create database --cpus=8-15 --memory=8GB --memory-base=0x100000000
        
        # Create instance with devices
        kerf create compute --cpus=16-23 --memory=4GB --devices=eth0_vf2
        
        # Create instance with explicit single CPU
        kerf create web-server --cpus=4 --memory=2GB
        
        # Create instance with auto-allocated CPU count
        kerf create web-server --cpu-count=4 --memory=2GB
        
        # Create instance with topology-aware allocation (auto-allocated)
        kerf create database --cpu-count=8 --memory=16GB --numa-nodes=0 --cpu-affinity=compact --memory-policy=local
        
        # Create instance with spread affinity across multiple NUMA nodes (auto-allocated)
        kerf create compute --cpu-count=16 --memory=32GB --numa-nodes=0,1 --cpu-affinity=spread --memory-policy=interleave
        
        # Validate without applying
        kerf create web-server --cpu-count=4 --memory=2GB --dry-run
    """
    try:
        if name is None and ctx is not None:
            # Get remaining args (ones that don't match options)
            remaining_args = ctx.args
            for arg in remaining_args:
                # First non-option argument is the name
                if not arg.startswith('-') and arg:
                    name = arg
                    break
        
        # Validate name is provided
        if not name:
            click.echo("Error: Instance name is required", err=True)
            click.echo("\nUsage: kerf create <name> [OPTIONS]", err=True)
            click.echo("       kerf create [OPTIONS] <name>", err=True)
            sys.exit(2)
        
        # Validate that exactly one of --cpus or --cpu-count is provided
        if cpus and cpu_count is not None:
            click.echo("Error: --cpus and --cpu-count are mutually exclusive. Specify either explicit CPUs (--cpus) or a count (--cpu-count)", err=True)
            sys.exit(2)
        
        if not cpus and cpu_count is None:
            click.echo("Error: Either --cpus or --cpu-count must be specified", err=True)
            sys.exit(2)
        
        # Parse CPU specification
        is_count = (cpu_count is not None)
        if is_count:
            if cpu_count <= 0:
                click.echo(f"Error: CPU count must be positive, got {cpu_count}", err=True)
                sys.exit(2)
            cpu_spec_value = cpu_count
        else:
            try:
                cpu_spec_value = parse_cpu_spec(cpus)
            except ValueError as e:
                click.echo(f"Error: Invalid CPU specification '{cpus}': {e}", err=True)
                sys.exit(2)
        
        # Parse memory specification
        try:
            memory_bytes = parse_memory_spec(memory)
        except ValueError as e:
            click.echo(f"Error: Invalid memory specification '{memory}': {e}", err=True)
            sys.exit(2)
        
        # Parse memory base (if specified)
        memory_base_addr = None
        if memory_base:
            try:
                memory_base_addr = parse_memory_base(memory_base)
            except ValueError as e:
                click.echo(f"Error: Invalid memory base '{memory_base}': {e}", err=True)
                sys.exit(2)
        
        # Parse device list
        device_list = parse_device_list(devices)
        
        # Parse NUMA nodes (if specified)
        numa_node_list = None
        if numa_nodes:
            try:
                numa_node_list = [int(n.strip()) for n in numa_nodes.split(',') if n.strip()]
                if not numa_node_list:
                    click.echo(f"Error: Invalid NUMA nodes specification '{numa_nodes}'", err=True)
                    sys.exit(2)
            except ValueError as e:
                click.echo(f"Error: Invalid NUMA nodes specification '{numa_nodes}': {e}", err=True)
                sys.exit(2)
        
        # Validate instance ID if specified
        if id is not None:
            if id < 1 or id > 511:
                click.echo(f"Error: Instance ID must be in range 1-511, got {id}", err=True)
                sys.exit(2)
        
        # Initialize manager
        manager = DeviceTreeManager()
        
        # Define operation to create instance
        def create_instance_operation(current):
            """Operation function to create instance in device tree."""
            import copy
            nonlocal memory_base_addr  # Allow modification of outer scope variable
            
            # Check if instance already exists
            if name in current.instances:
                raise ResourceError(f"Instance '{name}' already exists")
            
            # Create modified state
            modified = copy.deepcopy(current)
            
            # Check if instance ID is already in use (if specified)
            if id is not None:
                existing_ids = {inst.id for inst in modified.instances.values()}
                if id in existing_ids:
                    # Find which instance uses this ID
                    for inst_name, inst in modified.instances.items():
                        if inst.id == id:
                            raise ResourceError(
                                f"Instance ID {id} is already in use by instance '{inst_name}'"
                            )
                instance_id = id
            else:
                # Find next instance ID if not specified
                instance_id = find_next_instance_id(modified)
            
            # Allocate CPUs based on specification
            if is_count:
                # Allocate CPUs automatically from available pool with topology awareness
                cpu_list = allocate_cpus_from_pool(
                    modified, 
                    cpu_spec_value,  # cpu_spec_value is int (count)
                    cpu_affinity=cpu_affinity,
                    numa_nodes=numa_node_list
                )
            else:
                # Use explicitly specified CPUs
                cpu_list = cpu_spec_value  # cpu_spec_value is List[int]
            
            # Validate CPU allocation (against baseline and existing instances)
            validate_cpu_allocation(modified, cpu_list)
            
            # Find memory base if not specified
            if memory_base_addr is None:
                found_base = find_available_memory_base(modified, memory_bytes)
                if found_base is None:
                    raise ResourceError(
                        f"No available memory region found for {memory_bytes} bytes. "
                        "Try specifying --memory-base or reduce memory size."
                    )
                memory_base_addr = found_base
            else:
                # Validate specified memory base
                validate_memory_allocation(modified, memory_base_addr, memory_bytes)
            
            # Validate devices (if specified)
            # TODO: Add device validation when device reference parsing is implemented
            
            # Create instance resources with topology settings
            resources = InstanceResources(
                cpus=cpu_list,
                memory_base=memory_base_addr,
                memory_bytes=memory_bytes,
                devices=device_list,
                numa_nodes=numa_node_list,
                cpu_affinity=cpu_affinity,
                memory_policy=memory_policy
            )
            
            # Create instance
            instance = Instance(
                name=name,
                id=instance_id,
                resources=resources
            )
            
            # Add to modified state
            modified.instances[name] = instance
            
            return modified
        
        # Validate only (dry-run)
        if dry_run:
            try:
                current = manager.read_current_state()
                modified = create_instance_operation(current)
                
                # Get instance details from modified tree
                instance = modified.instances[name]
                
                click.echo(f"✓ Validation passed for instance '{name}'")
                if is_count:
                    click.echo(f"  CPUs: {cpu_spec_value} CPUs auto-allocated: {', '.join(map(str, instance.resources.cpus))}")
                else:
                    click.echo(f"  CPUs: {', '.join(map(str, instance.resources.cpus))}")
                if instance.resources.cpu_affinity:
                    click.echo(f"  CPU Affinity: {instance.resources.cpu_affinity}")
                if instance.resources.numa_nodes:
                    click.echo(f"  NUMA Nodes: {', '.join(map(str, instance.resources.numa_nodes))}")
                click.echo(f"  Memory: {memory} at {hex(instance.resources.memory_base)}")
                if instance.resources.memory_policy:
                    click.echo(f"  Memory Policy: {instance.resources.memory_policy}")
                if instance.resources.devices:
                    click.echo(f"  Devices: {', '.join(instance.resources.devices)}")
                click.echo(f"  Instance ID: {instance.id}")
                click.echo("\n✓ Instance would be created (dry-run mode)")
                click.echo("  Remove --dry-run to apply overlay to kernel")
            except (ResourceError, ValidationError) as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            except (KernelInterfaceError, ParseError) as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            return
        
        try:
            tx_id = manager.apply_operation(create_instance_operation)
            
            click.echo(f"✓ Created instance '{name}' (transaction {tx_id})")
            if verbose:
                current = manager.read_current_state()
                instance = current.instances[name]
                click.echo(f"  Instance ID: {instance.id}")
                click.echo(f"  CPUs: {', '.join(map(str, instance.resources.cpus))}")
                if instance.resources.cpu_affinity:
                    click.echo(f"  CPU Affinity: {instance.resources.cpu_affinity}")
                if instance.resources.numa_nodes:
                    click.echo(f"  NUMA Nodes: {', '.join(map(str, instance.resources.numa_nodes))}")
                click.echo(f"  Memory: {memory} at {hex(instance.resources.memory_base)}")
                if instance.resources.memory_policy:
                    click.echo(f"  Memory Policy: {instance.resources.memory_policy}")
                if instance.resources.devices:
                    click.echo(f"  Devices: {', '.join(instance.resources.devices)}")
        except ResourceError as e:
            click.echo(f"Error: Resource allocation failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
        except ValidationError as e:
            click.echo(f"Error: Validation failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
        except KernelInterfaceError as e:
            click.echo(f"Error: Kernel interface error: {e}", err=True)
            click.echo("\nIs the multikernel kernel module loaded?", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
            
    except KeyboardInterrupt:
        click.echo("\nOperation cancelled", err=True)
        sys.exit(130)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    create()

