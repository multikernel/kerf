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
Delete kernel instance command.

This command removes a kernel instance from the device tree using an overlay.
The instance must not have a kernel loaded (must be in EMPTY or READY state).
"""

import sys
import copy
from typing import Optional
import click

from ..runtime import DeviceTreeManager
from ..models import InstanceState
from ..exceptions import ResourceError, ValidationError, KernelInterfaceError, ParseError
from ..utils import get_instance_id_from_name, get_instance_status


@click.command(name='delete')
@click.argument('name', required=False)
@click.option('--id', type=int, help='Multikernel instance ID to delete (alternative to name)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--dry-run', is_flag=True, help='Validate without applying to kernel')
@click.pass_context
def delete(ctx: click.Context, name: Optional[str], id: Optional[int], verbose: bool, dry_run: bool):
    """
    Delete a kernel instance from the device tree.
    
    This command removes a kernel instance using a device tree overlay. The instance
    must not have a kernel loaded (must be in EMPTY or READY state). If a kernel
    is loaded, use 'kerf unload' first.
    
    Examples:
    
        kerf delete web-server
        kerf delete --id=1
        kerf delete web-server --verbose
        kerf delete web-server --dry-run
    """
    try:
        if not name and id is None:
            click.echo(
                "Error: Either instance name or --id must be provided",
                err=True
            )
            click.echo(
                "Usage: kerf delete <name>  or  kerf delete --id=<id>",
                err=True
            )
            sys.exit(2)
        
        debug = ctx.obj.get('debug', False) if ctx and ctx.obj else False
        
        manager = DeviceTreeManager()
        instance_name = None
        instance_id = None
        
        if name:
            instance_name = name
            if not manager.has_instance(name):
                current = manager.read_baseline()
                available_instances = list(current.instances.keys())
                
                click.echo(
                    f"Error: Instance '{name}' does not exist in device tree",
                    err=True
                )
                if available_instances:
                    click.echo(
                        f"Available instances: {', '.join(available_instances)}",
                        err=True
                    )
                else:
                    click.echo(
                        "No instances found in device tree",
                        err=True
                    )
                sys.exit(1)
            
            # Try to get ID from kernel filesystem (if instance directory exists)
            instance_id = get_instance_id_from_name(name)
            
            if verbose:
                if instance_id:
                    click.echo(f"Instance name: {name} (ID: {instance_id})")
                else:
                    click.echo(f"Instance name: {name} (ID: from device tree)")
        else:
            # Use ID directly, need to find name from device tree
            instance_id = id
            
            if instance_id < 1 or instance_id > 511:
                click.echo(
                    f"Error: --id must be between 1 and 511 (got {instance_id})",
                    err=True
                )
                sys.exit(2)
            
            # Find instance name from device tree by ID
            current = manager.read_baseline()
            for inst_name, inst in current.instances.items():
                if inst.id == instance_id:
                    instance_name = inst_name
                    break
            
            if not instance_name:
                click.echo(
                    f"Error: Instance with ID {instance_id} does not exist in device tree",
                    err=True
                )
                sys.exit(1)
        
        status = get_instance_status(instance_name)
        
        if status is None:
            if verbose:
                click.echo(f"Warning: Status file not found for instance '{instance_name}'")
        else:
            status_lower = status.lower()
            
            if status_lower == InstanceState.LOADED.value:
                click.echo(
                    f"Error: Cannot delete instance '{instance_name}' (ID: {instance_id})",
                    err=True
                )
                click.echo(
                    f"Instance has a kernel loaded (status: {status}).",
                    err=True
                )
                click.echo(
                    f"Please unload the kernel first using: kerf unload {instance_name}",
                    err=True
                )
                sys.exit(1)
            
            if status_lower == InstanceState.ACTIVE.value:
                click.echo(
                    f"Error: Cannot delete instance '{instance_name}' (ID: {instance_id})",
                    err=True
                )
                click.echo(
                    f"Instance is currently ACTIVE (running, status: {status}).",
                    err=True
                )
                click.echo(
                    f"Please stop and unload the kernel first.",
                    err=True
                )
                sys.exit(1)
            
            if verbose:
                click.echo(f"Instance status: '{status}' (OK to delete)")
        
        def delete_instance_operation(current):
            """Operation function to delete instance from device tree."""
            if instance_name not in current.instances:
                raise ResourceError(f"Instance '{instance_name}' does not exist in device tree")
            
            modified = copy.deepcopy(current)
            del modified.instances[instance_name]
            
            return modified
        
        # Validate only (dry-run)
        if dry_run:
            try:
                current = manager.read_baseline()
                
                if instance_name not in current.instances:
                    click.echo(f"Error: Instance '{instance_name}' does not exist in device tree", err=True)
                    sys.exit(1)
                
                # Get instance ID from device tree if not available from kernel
                if instance_id is None:
                    instance_id = current.instances[instance_name].id
                
                modified = delete_instance_operation(current)
                
                click.echo(f"✓ Validation passed for deletion of instance '{instance_name}'")
                click.echo(f"  Instance ID: {instance_id}")
                
                if debug:
                    from ..dtc.parser import DeviceTreeParser
                    import libfdt
                    
                    dtbo_data = manager.overlay_gen.generate_overlay(current, modified)
                    fdt = libfdt.Fdt(dtbo_data)
                    parser = DeviceTreeParser()
                    parser.fdt = fdt
                    dts_lines = parser._fdt_to_dts_recursive(0, 0)
                    dts_content = '\n'.join(dts_lines)
                    
                    click.echo(f"\nDebug: Overlay DTS source for deletion of '{instance_name}' (dry-run):")
                    click.echo("─" * 70)
                    click.echo(dts_content)
                    click.echo("─" * 70)
                
                click.echo("\n✓ Instance would be deleted (dry-run mode)")
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
                modified = delete_instance_operation(current)
                
                from ..dtc.parser import DeviceTreeParser
                import libfdt
                
                dtbo_data = manager.overlay_gen.generate_overlay(current, modified)
                fdt = libfdt.Fdt(dtbo_data)
                parser = DeviceTreeParser()
                parser.fdt = fdt
                dts_lines = parser._fdt_to_dts_recursive(0, 0)
                dts_content = '\n'.join(dts_lines)
                
                click.echo(f"Debug: Overlay DTS source for deletion of '{instance_name}':")
                click.echo("─" * 70)
                click.echo(dts_content)
                click.echo("─" * 70)
            
            tx_id = manager.apply_operation(delete_instance_operation)
            
            click.echo(f"✓ Deleted instance '{instance_name}' (transaction {tx_id})")
            if verbose:
                click.echo(f"  Instance ID: {instance_id}")
        except ResourceError as e:
            click.echo(f"Error: {e}", err=True)
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
    delete()

