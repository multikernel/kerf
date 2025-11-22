# Copyright 2025 Multikernel Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Show kernel instance information command.

This command displays information from /proc/kimage combined with
/sys/fs/multikernel/instances/* information.
"""

import sys
import re
from pathlib import Path
from typing import Optional, Dict, List
import click

from ..utils import get_instance_id_from_name, get_instance_status


def read_proc_kimage() -> str:
    """
    Read and return the contents of /proc/kimage.
    
    Returns:
        Contents of /proc/kimage as a string, or empty string if file doesn't exist
    """
    kimage_path = Path('/proc/kimage')
    
    if not kimage_path.exists():
        return ""
    
    try:
        with open(kimage_path, 'r') as f:
            return f.read()
    except (OSError, IOError):
        return ""


def get_all_instance_names() -> List[str]:
    """
    Get list of all instance names from /sys/fs/multikernel/instances/.
    
    Returns:
        List of instance names (directory names)
    """
    instances_dir = Path('/sys/fs/multikernel/instances')
    
    if not instances_dir.exists():
        return []
    
    instance_names = []
    try:
        for inst_dir in instances_dir.iterdir():
            if inst_dir.is_dir() and not inst_dir.name.startswith('.'):
                instance_names.append(inst_dir.name)
    except (OSError, IOError):
        pass
    
    return sorted(instance_names)


def read_instance_info(name: str) -> Dict[str, Optional[str]]:
    """
    Read instance information from /sys/fs/multikernel/instances/{name}/.
    
    Args:
        name: Instance name
    
    Returns:
        Dictionary with instance information (id, status, etc.)
    """
    info = {
        'name': name,
        'id': None,
        'status': None,
    }
    
    instance_dir = Path(f'/sys/fs/multikernel/instances/{name}')
    
    if not instance_dir.exists():
        return info
    
    instance_id = get_instance_id_from_name(name)
    if instance_id is not None:
        info['id'] = str(instance_id)

    status = get_instance_status(name)
    if status is not None:
        info['status'] = status
    
    # Try to read any other files in the instance directory
    try:
        for file_path in instance_dir.iterdir():
            if file_path.is_file() and file_path.name not in ['id', 'status']:
                try:
                    # Try text mode first
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read().strip()
                            if content:
                                info[file_path.name] = content
                    except (UnicodeDecodeError, ValueError):
                        # If UTF-8 decode fails, skip this file (it's likely binary)
                        pass
                except (OSError, IOError):
                    pass
    except (OSError, IOError):
        pass
    
    return info


def parse_kimage_table(kimage_content: str) -> Dict[int, Dict[str, str]]:
    """
    Parse /proc/kimage table format and extract information by MK_ID.
    
    Expected format:
    MK_ID  Type        Start Address   Segments  Mode  Cmdline
    -----  ----------  --------------  --------  ----  -------
    1      KEXEC_FILE  0x1000000        ...       ...   ...
    
    Args:
        kimage_content: Full content of /proc/kimage
    
    Returns:
        Dictionary mapping MK_ID to a dict with parsed fields
    """
    kimage_data = {}
    
    if not kimage_content:
        return kimage_data
    
    lines = kimage_content.strip().split('\n')
    
    header_line = None
    separator_line = None
    data_start_idx = 0
    
    for i, line in enumerate(lines):
        if 'MK_ID' in line and 'Type' in line:
            header_line = line
            if i + 1 < len(lines) and '-----' in lines[i + 1]:
                separator_line = lines[i + 1]
                data_start_idx = i + 2
            else:
                data_start_idx = i + 1
            break
    
    if header_line is None:
        return kimage_data

    boundaries = []
    if separator_line:
        # Each dash block represents a column
        # Find all sequences of dashes - their start positions are column boundaries
        dash_blocks = list(re.finditer(r'-+', separator_line))
        if dash_blocks:
            for match in dash_blocks:
                start_pos = match.start()
                if start_pos not in boundaries:
                    boundaries.append(start_pos)
            if dash_blocks:
                last_end = dash_blocks[-1].end()
                if last_end not in boundaries:
                    boundaries.append(last_end)
            boundaries.sort()
        else:
            # Fallback: use header line spacing
            boundaries = [0, len(separator_line)]
    else:
        # Fallback: estimate from header spacing (look for multiple spaces)
        boundaries = [0]
        i = 0
        while i < len(header_line):
            if header_line[i] == ' ':
                space_count = 0
                j = i
                while j < len(header_line) and header_line[j] == ' ':
                    space_count += 1
                    j += 1
                if space_count >= 2:  # Multiple spaces indicate column separator
                    if boundaries[-1] != i:
                        boundaries.append(i)
                    i = j
                else:
                    i += 1
            else:
                i += 1
        boundaries.append(len(header_line))
    
    columns = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(header_line)
        col_name = header_line[start:end].strip().lower().replace(' ', '_')
        if col_name:
            columns.append((col_name, start, end))
    
    for line in lines[data_start_idx:]:
        line = line.rstrip()
        if not line.strip():
            continue
        
        mk_id = None
        if columns:
            first_col_start = columns[0][1]
            first_col_end = columns[0][2] if len(columns) > 0 else len(line)
            mk_id_str = line[first_col_start:first_col_end].strip()
            try:
                mk_id = int(mk_id_str)
            except (ValueError, IndexError):
                continue
        
        if mk_id is None:
            continue
        
        # Extract all columns
        row_data = {}
        for i, (col_name, start, end) in enumerate(columns):
            # For the last column, extend to end of line to capture full value
            if i == len(columns) - 1:
                col_value = line[start:].strip()
            else:
                col_value = line[start:min(end, len(line))].strip()
            row_data[col_name] = col_value
        
        kimage_data[mk_id] = row_data
    
    return kimage_data


def display_instance_info(instance_info: Dict[str, Optional[str]], 
                         kimage_data: Optional[Dict[str, str]] = None,
                         verbose: bool = False):
    """
    Display formatted instance information.
    
    Args:
        instance_info: Instance information dictionary
        kimage_data: Optional kimage data for this instance
        verbose: Whether to show verbose information
    """
    name = instance_info.get('name', 'unknown')
    instance_id = instance_info.get('id')
    status = instance_info.get('status')
    
    # Header
    click.echo(f"\n{'=' * 80}")
    click.echo(f"Instance: {name}")
    click.echo(f"{'=' * 80}")
    
    # Basic info
    if instance_id:
        click.echo(f"  ID:              {instance_id}")
    if status:
        click.echo(f"  Status:          {status}")
    
    # Kernel image information from /proc/kimage
    if kimage_data:
        click.echo(f"\n  Kernel Image:")
        for key, value in kimage_data.items():
            # Format key nicely
            key_display = key.replace('_', ' ').title()
            click.echo(f"    {key_display:15} {value}")
    elif instance_id and verbose:
        click.echo(f"\n  Kernel Image:     (not loaded)")
    
    # Device tree source
    if 'device_tree_source' in instance_info and instance_info['device_tree_source']:
        dts = instance_info['device_tree_source']
        try:
            if isinstance(dts, bytes):
                dts = dts.decode('utf-8', errors='replace')
            elif not isinstance(dts, str):
                dts = str(dts)
            
            if dts.strip().startswith('/dts-v1/') or dts.strip().startswith('/'):
                click.echo(f"\n  Device Tree:")
                dts_lines = dts.split('\n')
                for line in dts_lines:
                    if line.strip():
                        click.echo(f"    {line}")
            else:
                pass
        except (UnicodeDecodeError, AttributeError):
            pass
    
    other_fields = {k: v for k, v in instance_info.items() 
                   if k not in ['name', 'id', 'status', 'device_tree_source'] and v
                   and isinstance(v, str) and len(v) < 1000  # Skip very long or binary data
                   and not any(ord(c) < 32 and c not in '\t\n\r' for c in v[:100])}  # Skip binary
    if other_fields:
        click.echo(f"\n  Additional Info:")
        for key, value in other_fields.items():
            key_display = key.replace('_', ' ').title()
            # Truncate long values
            if len(str(value)) > 60:
                value_str = str(value)[:57] + "..."
            else:
                value_str = str(value)
            click.echo(f"    {key_display:15} {value_str}")


@click.command(name='show')
@click.argument('name', required=False)
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def show(name: Optional[str], verbose: bool):
    """
    Show kernel instance information.
    
    This command combines information from /proc/kimage with instance information
    from /sys/fs/multikernel/instances/* in an organized format.
    
    Without an instance name, it shows all instances.
    With a specific instance name, it shows only that instance.
    
    Examples:
    
        kerf show
        kerf show web-server
        kerf show --verbose
    """
    try:
        # Read /proc/kimage content
        kimage_content = read_proc_kimage()
        kimage_table = parse_kimage_table(kimage_content)
        
        if name:
            # Show specific instance
            instance_id = get_instance_id_from_name(name)
            
            if instance_id is None:
                click.echo(
                    f"Error: Instance '{name}' not found",
                    err=True
                )
                click.echo(
                    f"Check available instances in /sys/fs/multikernel/instances/",
                    err=True
                )
                sys.exit(1)
            
            # Get instance information
            instance_info = read_instance_info(name)
            
            # Get kimage data for this instance
            kimage_data = kimage_table.get(instance_id)
            
            # Display information
            display_instance_info(instance_info, kimage_data, verbose)
        else:
            # Show all instances
            instance_names = get_all_instance_names()
            
            if not instance_names:
                click.echo("No instances found in /sys/fs/multikernel/instances/")
                if kimage_content:
                    click.echo("\n/proc/kimage:")
                    click.echo("=" * 80)
                    click.echo(kimage_content)
                sys.exit(0)
            
            # Display summary header
            click.echo("Multikernel Instances")
            click.echo("=" * 80)
            click.echo(f"Total instances: {len(instance_names)}")
            if kimage_table:
                click.echo(f"Loaded kernels: {len(kimage_table)}")
            click.echo()
            
            # Display each instance
            for inst_name in instance_names:
                instance_info = read_instance_info(inst_name)
                instance_id_str = instance_info.get('id')
                
                # Get kimage data for this instance
                kimage_data = None
                if instance_id_str:
                    try:
                        instance_id = int(instance_id_str)
                        kimage_data = kimage_table.get(instance_id)
                    except (ValueError, TypeError):
                        pass
                
                display_instance_info(instance_info, kimage_data, verbose)
            
            click.echo()
    
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
    show()

