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
Initialize baseline device tree configuration.

This command sets up the baseline device tree which describes the hardware
resources available for allocation to kernel instances. The baseline must
contain only resources (no instances).
"""

import sys
import os
import ctypes
from pathlib import Path
import click

from ..baseline import BaselineManager
from ..dtc.parser import DeviceTreeParser
from ..dtc.validator import MultikernelValidator
from ..dtc.reporter import ValidationReporter
from ..exceptions import ValidationError, KernelInterfaceError, ParseError


MULTIKERNEL_MOUNT_POINT = "/sys/fs/multikernel"

def is_multikernel_mounted() -> bool:
    mount_point = Path(MULTIKERNEL_MOUNT_POINT)
    if not mount_point.exists():
        return False
    
    try:
        with open('/proc/mounts', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == MULTIKERNEL_MOUNT_POINT:
                    return True
    except (OSError, IOError):
        pass
    
    return False


def mount_multikernel_fs(verbose: bool = False) -> None:
    if is_multikernel_mounted():
        if verbose:
            click.echo(f"✓ Multikernel filesystem already mounted at {MULTIKERNEL_MOUNT_POINT}")
        return
    
    mount_point = Path(MULTIKERNEL_MOUNT_POINT)
    try:
        mount_point.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise KernelInterfaceError(
            f"Failed to create mount point {MULTIKERNEL_MOUNT_POINT}: {e}"
        )
    
    libc = ctypes.CDLL(None, use_errno=True)
    
    # mount() signature: int mount(const char *source, const char *target,
    #                              const char *filesystemtype, unsigned long mountflags,
    #                              const void *data);
    libc.mount.argtypes = [
        ctypes.c_char_p,  # source
        ctypes.c_char_p,  # target
        ctypes.c_char_p,  # filesystemtype
        ctypes.c_ulong,   # mountflags
        ctypes.c_void_p   # data
    ]
    libc.mount.restype = ctypes.c_int

    source = b"none"
    target = MULTIKERNEL_MOUNT_POINT.encode('utf-8')
    fstype = b"multikernel"
    mountflags = 0
    data = None
    
    if verbose:
        click.echo(f"Mounting multikernel filesystem at {MULTIKERNEL_MOUNT_POINT}...")
    
    result = libc.mount(source, target, fstype, mountflags, data)
    
    if result != 0:
        errno = ctypes.get_errno()
        error_msg = os.strerror(errno)
        raise KernelInterfaceError(
            f"Failed to mount multikernel filesystem: {error_msg} (errno: {errno})\n"
            f"Make sure the multikernel kernel module is loaded and you have root privileges."
        )
    
    if verbose:
        click.echo(f"✓ Successfully mounted multikernel filesystem")


@click.command()
@click.option('--input', '-i', required=True, help='Input DTS or DTB file containing resources only')
@click.option('--dry-run', is_flag=True, help='Validate without applying')
@click.option('--report', is_flag=True, help='Generate detailed validation report')
@click.option('--format', type=click.Choice(['text', 'json', 'yaml']), 
              default='text', help='Report format (default: text)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def init(input: str, dry_run: bool, report: bool, format: str, verbose: bool):
    """
    Initialize baseline device tree configuration.
    
    Sets up the baseline device tree which describes hardware resources
    available for allocation. The baseline must contain ONLY resources
    (no instances). Instances are created via 'kerf create' using overlays.
    
    By default, the baseline is applied to the kernel after validation.
    Use --dry-run to validate without applying.
    
    Example:
    
        # Validate and apply to kernel (default behavior)
        kerf init --input=hardware.dts
        
        # Validate baseline without applying
        kerf init --input=hardware.dts --dry-run
    """
    try:
        input_path = Path(input)
        
        if not input_path.exists():
            click.echo(f"Error: Input file '{input}' does not exist", err=True)
            sys.exit(3)
        
        # Parse input file
        parser = DeviceTreeParser()
        
        if input_path.suffix == '.dts':
            with open(input_path, 'r') as f:
                dts_content = f.read()
            tree = parser.parse_dts(dts_content)
        elif input_path.suffix == '.dtb':
            tree = parser.parse_dtb(str(input_path))
        else:
            click.echo(f"Error: Unsupported input format: {input_path.suffix}", err=True)
            click.echo("Supported formats: .dts, .dtb", err=True)
            sys.exit(2)
        
        baseline_mgr = BaselineManager()
        
        try:
            baseline_mgr.validate_baseline(tree)
        except ValidationError as e:
            click.echo(f"Error: Invalid baseline configuration: {e}", err=True)
            click.echo("\nBaseline must contain:", err=True)
            click.echo("   /resources (hardware inventory)", err=True)
            click.echo("   /instances (must be empty or absent)", err=True)
            click.echo("\nInstances should be created via 'kerf create'", err=True)
            sys.exit(1)
        
        validator = MultikernelValidator()
        if input_path.suffix == '.dts':
            validator.set_dts_context(dts_content, str(input_path))
        
        validation_result = validator.validate(tree)
        
        if report:
            reporter = ValidationReporter()
            report_text = reporter.generate_report(validation_result, tree, verbose, format)
            click.echo(report_text)
            if not validation_result.is_valid:
                sys.exit(1)
            return
        
        if not validation_result.is_valid:
            click.echo("Validation failed:", err=True)
            for error in validation_result.errors:
                click.echo(f"  ✗ {error}", err=True)
            if validation_result.warnings:
                click.echo("\nWarnings:", err=True)
                for warning in validation_result.warnings:
                    click.echo(f"  ⚠ {warning}", err=True)
            sys.exit(1)
        
        if verbose:
            click.echo("✓ Baseline validation passed")
            if validation_result.warnings:
                click.echo("\nWarnings:")
                for warning in validation_result.warnings:
                    click.echo(f"  ⚠ {warning}")
        
        if dry_run:
            click.echo(" Baseline validation passed")
            click.echo(" Baseline would be applied (dry-run mode)")
        else:
            try:
                mount_multikernel_fs(verbose=verbose)

                baseline_mgr.write_baseline(tree)
                click.echo("✓ Baseline applied to kernel successfully")
                click.echo(f"  Baseline: /sys/fs/multikernel/device_tree")
            except KernelInterfaceError as e:
                click.echo(f"Error: Failed to apply baseline: {e}", err=True)
                sys.exit(1)
        
    except ParseError as e:
        click.echo(f"Error: Failed to parse input file: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

