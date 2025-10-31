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
Device Tree Compiler subcommand implementation.
"""

import click
import sys
from pathlib import Path
from typing import Optional
from ..exceptions import ParseError, ValidationError
from .parser import DeviceTreeParser
from .validator import MultikernelValidator
from .extractor import InstanceExtractor
from .reporter import ValidationReporter


@click.command()
@click.option('--input', '-i', required=True, help='Input DTS or DTB file')
@click.option('--output', '-o', help='Output file')
@click.option('--output-dir', help='Output directory (only for --extract-all)')
@click.option('--extract', help='Extract specific instance by name')
@click.option('--extract-all', is_flag=True, help='Extract all instances')
@click.option('--list-instances', is_flag=True, help='List all instances in DTB')
@click.option('--report', is_flag=True, help='Generate validation report')
@click.option('--format', type=click.Choice(['dts', 'dtb', 'json', 'yaml', 'text']), 
              default='dtb', help='Output format')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--dry-run', is_flag=True, help='Validate without generating output')
def dtc(input: str, output: Optional[str], output_dir: Optional[str], 
        extract: Optional[str], extract_all: bool, list_instances: bool,
        report: bool, format: str, verbose: bool, dry_run: bool):
    """Device Tree Compiler and Validator."""
    
    try:
        # Parse input file
        parser = DeviceTreeParser()
        input_path = Path(input)
        
        if not input_path.exists():
            click.echo(f"Error: Input file '{input}' does not exist", err=True)
            sys.exit(3)  # File I/O error
        
        # Handle DTS format conversion first (no validation needed)
        if format == 'dts':
            if input_path.suffix == '.dtb':
                dts_content = parser.dtb_to_dts(str(input_path))
                output_path = Path(output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w') as f:
                    f.write(dts_content)
                click.echo(f"Generated: {output_path}")
                return
            else:
                # Input is DTS, just copy it
                with open(input_path, 'r') as f:
                    dts_content = f.read()
                output_path = Path(output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w') as f:
                    f.write(dts_content)
                click.echo(f"Generated: {output_path}")
                return
        
        # Determine input format and parse
        if input_path.suffix == '.dts':
            # Parse DTS file
            with open(input_path, 'r') as f:
                dts_content = f.read()
            tree = parser.parse_dts(dts_content)
        elif input_path.suffix == '.dtb':
            tree = parser.parse_dtb(str(input_path))
        else:
            click.echo(f"Error: Unsupported input format: {input_path.suffix}", err=True)
            sys.exit(2)  # Invalid command-line arguments
        
        # Validate configuration
        validator = MultikernelValidator()
        
        # Set DTS context for enhanced error messages
        if input_path.suffix == '.dts':
            with open(input_path, 'r') as f:
                dts_content = f.read()
            validator.set_dts_context(dts_content, str(input_path))
        
        validation_result = validator.validate(tree)
        
        # Generate validation report
        reporter = ValidationReporter()
        report_text = reporter.generate_report(validation_result, tree, verbose)
        
        if report:
            click.echo(report_text)
            return
        
        if verbose:
            click.echo(report_text)
        
        # Handle list instances command
        if list_instances:
            for name, instance in tree.instances.items():
                click.echo(f"{name} (ID: {instance.id})")
            return
        
        # Check validation results
        if not validation_result.is_valid:
            click.echo("Validation failed:", err=True)
            for error in validation_result.errors:
                click.echo(f"ERROR: {error}", err=True)
            sys.exit(1)  # Validation failed with errors
        
        if dry_run:
            click.echo("✓ Validation passed (dry run)")
            return
        
        # Generate outputs
        extractor = InstanceExtractor()
        
        if extract:
            # Extract single instance
            if extract not in tree.instances:
                click.echo(f"Error: Instance '{extract}' not found", err=True)
                click.echo("Available instances:", err=True)
                for name in tree.instances.keys():
                    click.echo(f"  {name}", err=True)
                sys.exit(2)  # Invalid command-line arguments
            
            instance_dtb = extractor.extract_instance(tree, extract)
            
            if output:
                output_path = Path(output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(instance_dtb)
                click.echo(f"Generated: {output_path}")
            else:
                click.echo(f"Generated instance DTB for '{extract}' ({len(instance_dtb)} bytes)")
        
        elif extract_all:
            # Extract all instances
            if not output_dir:
                click.echo("Error: --output-dir required for --extract-all", err=True)
                sys.exit(2)  # Invalid command-line arguments
            
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            # Generate global DTB
            global_dtb = extractor.generate_global_dtb(tree)
            global_path = output_path / "global.dtb"
            with open(global_path, 'wb') as f:
                f.write(global_dtb)
            click.echo(f"Generated: {global_path}")
            
            # Generate instance DTBs
            instances = extractor.extract_all_instances(tree)
            for name, instance_dtb in instances.items():
                instance_path = output_path / f"{name}.dtb"
                with open(instance_path, 'wb') as f:
                    f.write(instance_dtb)
                click.echo(f"Generated: {instance_path}")
        
        else:
            # Generate single output (global DTB or DTS)
            if not output:
                click.echo("Error: --output required", err=True)
                sys.exit(2)  # Invalid command-line arguments
            
            if output_dir:
                click.echo("Error: --output-dir can only be used with --extract-all", err=True)
                sys.exit(2)  # Invalid command-line arguments
            
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Generate DTB
            global_dtb = extractor.generate_global_dtb(tree)
            with open(output_path, 'wb') as f:
                f.write(global_dtb)
            click.echo(f"Generated: {output_path}")
        
        click.echo("✓ All operations completed successfully")
        
    except ParseError as e:
        click.echo(f"Parse error: {e}", err=True)
        sys.exit(4)  # DTB/DTS parsing error
    except ValidationError as e:
        click.echo(f"Validation error: {e}", err=True)
        sys.exit(1)  # Validation failed with errors
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)  # Validation failed with errors
