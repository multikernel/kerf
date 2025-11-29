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
Update kernel instance resources.

This command updates hardware resources of an existing kernel instance.
The underlying DTS overlay processes operations in a specific order to ensure
clean resource migration:
  1. memory-remove - Remove memory from instance first
  2. memory-add    - Then add memory to instance
  3. cpu-remove    - Remove CPU from instance first
  4. cpu-add       - Then add CPU to instance
"""

import sys
import copy
from typing import Optional, List
import click

from ..runtime import DeviceTreeManager
from ..models import InstanceResources
from ..resources import (
    validate_cpu_allocation,
    validate_memory_allocation,
    find_available_memory_base,
)
from ..exceptions import ValidationError, KernelInterfaceError, ResourceError, ParseError
from ..create.main import parse_cpu_spec, parse_memory_spec, parse_memory_base


def dump_overlay_for_debug(
    manager: DeviceTreeManager,
    instance_name: str,
    old_instance,
    new_instance,
    suffix: str = ""
) -> None:
    """Dump overlay DTS source to stdout for debugging when --debug is enabled."""
    dtbo_data = manager.overlay_gen.generate_update_overlay(instance_name, old_instance, new_instance)

    try:
        from ..dtc.parser import DeviceTreeParser
        import libfdt
        
        fdt = libfdt.Fdt(dtbo_data)
        parser = DeviceTreeParser()
        parser.fdt = fdt
        dts_lines = parser._fdt_to_dts_recursive(0, 0)
        dts_content = '\n'.join(dts_lines)
        
        click.echo(f"Debug: Overlay DTS source for '{instance_name}'{suffix}:")
        click.echo("─" * 70)
        click.echo(dts_content)
        click.echo("─" * 70)
    except Exception as e:
        click.echo(f"Debug: Failed to convert overlay to DTS: {e}", err=True)


@click.command(name='update')
@click.argument('name', required=True)
@click.option('--cpus', '-c',
              help='Update CPU allocation: CPU IDs (e.g., "4-7" for range, "4,5,6,7" for list)')
@click.option('--memory', '-m',
              help='Update memory allocation (e.g., "2GB", "2048MB")')
@click.option('--memory-base',
              help='Update memory base address (hex: 0x80000000 or decimal, auto-assigned if not specified with --memory)')
@click.option('--dry-run', is_flag=True, help='Validate without applying to kernel')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.pass_context
def update(
    ctx: click.Context,
    name: str,
    cpus: Optional[str],
    memory: Optional[str],
    memory_base: Optional[str],
    dry_run: bool,
    verbose: bool
):
    """
    Update resources of an existing kernel instance.
    
    This command updates CPU and/or memory resources for an instance.
    Operations are processed in order: memory-remove, memory-add, cpu-remove, cpu-add.
    
    At least one of --cpus or --memory must be specified.
    
    Examples:
    
        # Update CPUs only
        kerf update web-server --cpus=8-15
        
        # Update memory only (auto-assign base address)
        kerf update web-server --memory=4GB
        
        # Update memory with specific base address
        kerf update web-server --memory=4GB --memory-base=0x200000000
        
        # Update both CPUs and memory
        kerf update web-server --cpus=8-15 --memory=4GB
        
        # Validate without applying
        kerf update web-server --cpus=8-15 --memory=4GB --dry-run
    """
    try:
        if not cpus and not memory:
            click.echo("Error: At least one of --cpus or --memory must be specified", err=True)
            sys.exit(2)
        
        if memory_base and not memory:
            click.echo("Error: --memory-base requires --memory", err=True)
            sys.exit(2)
        
        debug = ctx.obj.get('debug', False) if ctx and ctx.obj else False
        
        manager = DeviceTreeManager()
        
        if not manager.has_instance(name):
            click.echo(f"Error: Instance '{name}' does not exist", err=True)
            sys.exit(1)
        
        cpu_list = None
        if cpus:
            try:
                cpu_list = parse_cpu_spec(cpus)
            except ValueError as e:
                click.echo(f"Error: Invalid CPU specification '{cpus}': {e}", err=True)
                sys.exit(2)
        
        memory_bytes = None
        if memory:
            try:
                memory_bytes = parse_memory_spec(memory)
            except ValueError as e:
                click.echo(f"Error: Invalid memory specification '{memory}': {e}", err=True)
                sys.exit(2)
        
        memory_base_addr = None
        if memory_base:
            try:
                memory_base_addr = parse_memory_base(memory_base)
            except ValueError as e:
                click.echo(f"Error: Invalid memory base '{memory_base}': {e}", err=True)
                sys.exit(2)
        
        def update_instance_operation(current):
            """Operation function to update instance resources. Returns (old_instance, new_instance)."""
            nonlocal memory_base_addr
            
            from pathlib import Path
            import libfdt
            import struct
            from ..models import Instance, InstanceResources
            
            instance_dt_path = Path(f'/sys/fs/multikernel/instances/{name}/device_tree')
            if not instance_dt_path.exists():
                raise ResourceError(f"Instance '{name}' device_tree not found at {instance_dt_path}")
            
            try:
                with open(instance_dt_path, 'rb') as f:
                    instance_dtb_data = f.read()
                
                fdt = libfdt.Fdt(instance_dtb_data)
                
                instance_offset = 0
                instance_node_name = fdt.get_name(instance_offset)

                instance_id_prop = fdt.getprop(instance_offset, 'id')
                instance_id = struct.unpack('>I', instance_id_prop)[0]

                resources_offset = fdt.first_subnode(instance_offset)
                if resources_offset < 0:
                    raise ResourceError(f"No resources node found for instance '{instance_node_name}'")
                
                cpus_prop = fdt.getprop(resources_offset, 'cpus')
                cpus = list(struct.unpack(f'>{len(cpus_prop)//4}I', cpus_prop))
                
                memory_base_prop = fdt.getprop(resources_offset, 'memory-base')
                memory_base = struct.unpack('>Q', memory_base_prop)[0]
                
                memory_bytes_prop = fdt.getprop(resources_offset, 'memory-bytes')
                memory_bytes_val = struct.unpack('>Q', memory_bytes_prop)[0]
                
                try:
                    device_names_prop = fdt.getprop(resources_offset, 'device-names')
                    device_names = device_names_prop.as_str().rstrip('\0').split()
                except libfdt.FdtException:
                    device_names = []
                
                existing_instance = Instance(
                    name=instance_node_name,
                    id=instance_id,
                    resources=InstanceResources(
                        cpus=cpus,
                        memory_base=memory_base,
                        memory_bytes=memory_bytes_val,
                        devices=device_names
                    )
                )
                
            except libfdt.FdtException as e:
                raise ResourceError(f"Failed to parse instance '{name}' device_tree: {e}")
            except Exception as e:
                raise ResourceError(f"Failed to read instance '{name}' device_tree: {e}")
            
            modified = copy.deepcopy(current)
            modified.instances[instance_node_name] = copy.deepcopy(existing_instance)
            
            if cpu_list is not None:
                current_cpus = set(existing_instance.resources.cpus)
                requested_cpus = set(cpu_list)
                new_cpus = requested_cpus - current_cpus
                
                if new_cpus:
                    validate_cpu_allocation(modified, sorted(new_cpus), exclude_instance=instance_node_name)
            
            if memory_bytes is not None:
                if memory_base_addr is None:
                    found_base = find_available_memory_base(modified, memory_bytes)
                    if found_base is None:
                        raise ResourceError(
                            f"No available memory region found for {memory_bytes} bytes. "
                            "Try specifying --memory-base or reduce memory size."
                        )
                    memory_base_addr = found_base
                else:
                    validate_memory_allocation(
                        modified, memory_base_addr, memory_bytes, exclude_instance=instance_node_name
                    )

            updated_instance = copy.deepcopy(existing_instance)
            if cpu_list is not None:
                updated_instance.resources.cpus = cpu_list
            if memory_bytes is not None:
                updated_instance.resources.memory_base = memory_base_addr
                updated_instance.resources.memory_bytes = memory_bytes

            return (existing_instance, updated_instance)
        
        if dry_run:
            try:
                current = manager.read_baseline()
                old_instance, new_instance = update_instance_operation(current)
                
                click.echo(f"✓ Validation passed for updating instance '{name}'")
                if cpu_list is not None:
                    click.echo(f"  CPUs: {', '.join(map(str, new_instance.resources.cpus))}")
                if memory_bytes is not None:
                    click.echo(f"  Memory: {memory} at {hex(new_instance.resources.memory_base)}")
                
                if debug:
                    dump_overlay_for_debug(manager, name, old_instance, new_instance, suffix="_dryrun")
                    click.echo()
                
                click.echo("\n✓ Instance would be updated (dry-run mode)")
                click.echo("  Remove --dry-run to apply overlay to kernel")
            except (ResourceError, ValidationError) as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            except (KernelInterfaceError, ParseError) as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)
            return
        
        try:
            if debug:
                current = manager.read_baseline()
                old_instance, new_instance = update_instance_operation(current)
                dump_overlay_for_debug(manager, name, old_instance, new_instance)
            
            def apply_update_operation(current):
                old_instance, new_instance = update_instance_operation(current)
                dtbo_data = manager.overlay_gen.generate_update_overlay(name, old_instance, new_instance)
                return dtbo_data
            
            with manager._acquire_lock():
                current = manager.read_baseline()
                dtbo_data = apply_update_operation(current)
                
                try:
                    if not manager.overlays_new.exists():
                        raise KernelInterfaceError(
                            f"Overlay interface not found: {manager.overlays_new}"
                        )
                    
                    with open(manager.overlays_new, 'wb') as f:
                        f.write(dtbo_data)
                    
                    tx_id = manager._find_latest_transaction()
                    if not tx_id:
                        raise KernelInterfaceError(
                            "Overlay written but kernel did not create transaction directory"
                        )
                    
                    tx_dir = manager.overlays_dir / f"tx_{tx_id}"
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
                                            instance_name_from_tx = f.read().strip()
                                        error_msg += f" (instance: {instance_name_from_tx})"
                                    except OSError:
                                        pass
                                raise KernelInterfaceError(error_msg)
                        except OSError:
                            pass
                    
                except OSError as e:
                    raise KernelInterfaceError(
                        f"Failed to write overlay to {manager.overlays_new}: {e}"
                    ) from e
            
            click.echo(f"✓ Updated instance '{name}' (transaction {tx_id})")
            if verbose:
                current = manager.read_baseline()
                _, new_instance = update_instance_operation(current)
                click.echo(f"  Instance ID: {new_instance.id}")
                click.echo(f"  CPUs: {', '.join(map(str, new_instance.resources.cpus))}")
                click.echo(f"  Memory: {new_instance.resources.memory_bytes} bytes at {hex(new_instance.resources.memory_base)}")
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
    update()

