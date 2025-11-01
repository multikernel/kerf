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
from pathlib import Path
import click

from ..baseline import BaselineManager
from ..dtc.parser import DeviceTreeParser
from ..dtc.validator import MultikernelValidator
from ..dtc.reporter import ValidationReporter
from ..exceptions import ValidationError, KernelInterfaceError, ParseError


@click.command()
@click.option('--input', '-i', required=True, help='Input DTS or DTB file containing resources only')
@click.option('--apply', is_flag=True, help='Apply baseline to kernel immediately')
@click.option('--dry-run', is_flag=True, help='Validate without applying')
@click.option('--report', is_flag=True, help='Generate detailed validation report')
@click.option('--format', type=click.Choice(['text', 'json', 'yaml']), 
              default='text', help='Report format (default: text)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def init(input: str, apply: bool, dry_run: bool, report: bool, format: str, verbose: bool):
    """
    Initialize baseline device tree configuration.
    
    Sets up the baseline device tree which describes hardware resources
    available for allocation. The baseline must contain ONLY resources
    (no instances). Instances are created via 'kerf create' using overlays.
    
    Example:
    
        # Validate baseline without applying
        kerf init --input=hardware.dts
        
        # Validate and apply to kernel
        kerf init --input=hardware.dts --apply
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
        
        # Validate baseline structure
        baseline_mgr = BaselineManager()
        
        try:
            baseline_mgr.validate_baseline(tree)
        except ValidationError as e:
            click.echo(f"Error: Invalid baseline configuration: {e}", err=True)
            click.echo("\nBaseline must contain:", err=True)
            click.echo("  ✓ /resources (hardware inventory)", err=True)
            click.echo("  ✗ /instances (must be empty or absent)", err=True)
            click.echo("\nInstances should be created via 'kerf create'", err=True)
            sys.exit(1)
        
        # Validate resources are valid
        validator = MultikernelValidator()
        if input_path.suffix == '.dts':
            validator.set_dts_context(dts_content, str(input_path))
        
        validation_result = validator.validate(tree)
        
        # Generate report if requested
        if report:
            reporter = ValidationReporter()
            report_text = reporter.generate_report(validation_result, tree, verbose, format)
            click.echo(report_text)
            if not validation_result.is_valid:
                sys.exit(1)
            return
        
        # Show validation results
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
        
        # Apply to kernel if requested
        if apply and not dry_run:
            try:
                baseline_mgr.write_baseline(tree)
                click.echo("✓ Baseline applied to kernel successfully")
                click.echo(f"  Baseline: /sys/fs/multikernel/device_tree")
            except KernelInterfaceError as e:
                click.echo(f"Error: Failed to apply baseline: {e}", err=True)
                sys.exit(1)
        elif apply:
            click.echo("✓ Baseline would be applied (dry-run mode)")
        else:
            click.echo("✓ Baseline validation passed")
            click.echo("  Use --apply to write baseline to kernel")
        
    except ParseError as e:
        click.echo(f"Error: Failed to parse input file: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

