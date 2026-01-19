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

import ctypes
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

import click
import libfdt

try:
    import pyudev
except ImportError:
    pyudev = None

from ..baseline import BaselineManager
from ..create.main import parse_cpu_spec, parse_device_list
from ..dtc.parser import DeviceTreeParser
from ..dtc.reporter import ValidationReporter
from ..dtc.validator import MultikernelValidator
from ..exceptions import KernelInterfaceError, ParseError, ValidationError
from ..models import (
    CPUAllocation,
    DeviceInfo,
    GlobalDeviceTree,
    HardwareInventory,
    MemoryAllocation,
)


MULTIKERNEL_MOUNT_POINT = "/sys/fs/multikernel"

def is_multikernel_mounted() -> bool:
    mount_point = Path(MULTIKERNEL_MOUNT_POINT)
    if not mount_point.exists():
        return False

    try:
        with open('/proc/mounts', 'r', encoding='utf-8') as f:
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
        ) from e

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
        click.echo("✓ Successfully mounted multikernel filesystem")


def get_multikernel_memory_pool_from_iomem() -> Optional[Tuple[int, int]]:
    """
    Get multikernel memory pool region from /proc/iomem.
    Returns (base_address, size_bytes) or None if not found.
    """
    try:
        iomem_path = Path('/proc/iomem')
        if not iomem_path.exists():
            return None
        with open(iomem_path, 'r', encoding='utf-8') as f:
            for line in f:
                if 'Multikernel Memory Pool' in line:
                    match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
                    if match:
                        base = int(match.group(1), 16)
                        end = int(match.group(2), 16)
                        size = end - base + 1
                        return (base, size)
    except (OSError, IOError, ValueError):
        pass

    return None


def get_total_memory_from_system() -> Optional[int]:
    """
    Get total system memory from /proc/meminfo.
    Returns total memory in bytes or None if not available.
    """
    try:
        meminfo_path = Path('/proc/meminfo')
        if not meminfo_path.exists():
            return None
        with open(meminfo_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    match = re.search(r'(\d+)', line)
                    if match:
                        # Convert from KB to bytes
                        return int(match.group(1)) * 1024
    except (OSError, IOError, ValueError):
        pass

    return None


def detect_pci_device(device_name: str) -> Optional[DeviceInfo]:
    """
    Detect PCI device information from the system.

    Args:
        device_name: Device name (e.g., "enp9s0" or PCI BDF "0000:09:00.0")

    Returns:
        DeviceInfo with detected PCI information, or None if not found
    """
    if pyudev is None:
        raise KernelInterfaceError(
            "pyudev is required for device detection. Please install it: pip install pyudev"
        )

    try:
        context = pyudev.Context()
        pci_device = None
        pci_slot = None

        if re.match(r'^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]$', device_name):
            pci_slot = device_name
            try:
                pci_device = pyudev.Devices.from_path(context, f'/sys/bus/pci/devices/{pci_slot}')
            except (ValueError, pyudev.DeviceNotFoundError):
                return None
        else:
            try:
                net_device = pyudev.Devices.from_name(context, 'net', device_name)
                pci_device = net_device.find_parent('pci')
                if pci_device:
                    pci_slot = pci_device.sys_name
            except (ValueError, pyudev.DeviceNotFoundError):
                pass

            if not pci_device:
                for device in context.list_devices(subsystem='pci'):
                    try:
                        for child in device.children:
                            if child.sys_name == device_name:
                                pci_device = device
                                pci_slot = device.sys_name
                                break
                        if pci_device:
                            break
                    except (OSError, AttributeError):
                        continue

        if not pci_device or not pci_slot:
            return None

        vendor_id = None
        device_id = None
        pci_device_path = Path(pci_device.sys_path)

        vendor_file = pci_device_path / 'vendor'
        if vendor_file.exists():
            try:
                with open(vendor_file, 'r', encoding='utf-8') as f:
                    vendor_id = int(f.read().strip(), 16)
            except (ValueError, IOError):
                pass

        device_file = pci_device_path / 'device'
        if device_file.exists():
            try:
                with open(device_file, 'r', encoding='utf-8') as f:
                    device_id = int(f.read().strip(), 16)
            except (ValueError, IOError):
                pass

        compatible = "pci-device"  # Default
        class_file = pci_device_path / 'class'
        if class_file.exists():
            try:
                with open(class_file, 'r', encoding='utf-8') as f:
                    pci_class = int(f.read().strip(), 16)
                    class_code = (pci_class >> 16) & 0xFF
                    if class_code == 0x02:  # Network controller
                        compatible = "pci-network"
                    elif class_code == 0x01:  # Mass storage controller
                        compatible = "pci-storage"
                    elif class_code == 0x03:  # Display controller
                        compatible = "pci-display"
                    elif class_code == 0x0c:  # Serial bus controller
                        compatible = "pci-serial"
            except (ValueError, IOError):
                pass

        return DeviceInfo(
            name=device_name,
            compatible=compatible,
            device_type="pci",
            pci_id=pci_slot,
            vendor_id=vendor_id,
            device_id=device_id
        )
    except (OSError, IOError, ValueError, AttributeError, pyudev.DeviceNotFoundError):
        return None


def detect_platform_device(device_name: str) -> Optional[DeviceInfo]:
    """
    Detect platform device information from the system.

    Args:
        device_name: Device name (e.g., "serial_console")

    Returns:
        DeviceInfo with detected platform information, or None if not found
    """
    try:
        platform_devices = Path('/sys/devices/platform')
        if not platform_devices.exists():
            return None

        if 'serial' in device_name.lower() or 'console' in device_name.lower():
            serial_path = platform_devices / 'serial8250'
            if serial_path.exists():
                return DeviceInfo(
                    name=device_name,
                    compatible="ns16550",
                    device_type="platform",
                    device_name="serial8250"
                )

        for platform_dev in platform_devices.iterdir():
            if platform_dev.name in device_name or device_name in platform_dev.name:
                return DeviceInfo(
                    name=device_name,
                    compatible="platform-device",
                    device_type="platform",
                    device_name=platform_dev.name
                )

        return None
    except (OSError, IOError, ValueError):
        return None


def detect_device_from_system(device_name: str) -> Optional[DeviceInfo]:
    """
    Detect device information from the system.
    Tries PCI first, then platform devices.

    Args:
        device_name: Device name to detect

    Returns:
        DeviceInfo with detected information, or None if not found
    """
    pci_device = detect_pci_device(device_name)
    if pci_device:
        return pci_device

    platform_device = detect_platform_device(device_name)
    if platform_device:
        return platform_device

    return None


def get_total_cpus_from_system() -> Optional[int]:
    """
    Get total number of logical CPUs from the system via sysfs.
    Returns total CPU count or None if not available.
    """
    try:
        cpu_dir = Path('/sys/devices/system/cpu')
        if cpu_dir.exists():
            cpu_files = [f for f in cpu_dir.iterdir() if f.name.startswith('cpu') and f.name[3:].isdigit()]
            if cpu_files:
                cpu_numbers = [int(f.name[3:]) for f in cpu_files]
                return max(cpu_numbers) + 1
    except (OSError, ValueError):
        pass

    return None


def get_valid_apic_ids_from_system() -> Optional[set]:
    """
    Get set of valid APIC IDs from the system via sysfs.
    Reads /sys/devices/system/cpu/cpuN/topology/apic_id for each CPU.
    Returns set of valid APIC IDs or None if not available.
    """
    try:
        cpu_dir = Path('/sys/devices/system/cpu')
        if not cpu_dir.exists():
            return None

        apic_ids = set()
        cpu_files = [f for f in cpu_dir.iterdir() if f.name.startswith('cpu') and f.name[3:].isdigit()]

        for cpu_path in cpu_files:
            apic_id_file = cpu_path / 'topology' / 'apic_id'
            if apic_id_file.exists():
                try:
                    with open(apic_id_file, 'r', encoding='utf-8') as f:
                        apic_id = int(f.read().strip())
                        apic_ids.add(apic_id)
                except (ValueError, IOError):
                    pass

        return apic_ids if apic_ids else None
    except (OSError, ValueError):
        pass

    return None


def build_baseline_from_cmdline(
    cpus: str,
    devices: Optional[str] = None,
    verbose: bool = False
) -> GlobalDeviceTree:
    """
    Build a GlobalDeviceTree from command line arguments.

    Args:
        cpus: CPU specification string (e.g., "4-7" or "4,5,6,7")
        devices: Optional device names (comma-separated, e.g., "enp9s0_dev,nvme0")
        verbose: Whether to print verbose output

    Returns:
        GlobalDeviceTree with resources only (no instances)

    Raises:
        ValueError: If CPU specification is invalid
        KernelInterfaceError: If memory cannot be determined from /proc/iomem
    """
    try:
        cpu_list = parse_cpu_spec(cpus)
    except ValueError as e:
        raise ValueError(f"Invalid CPU specification '{cpus}': {e}") from e

    # Validate against valid APIC IDs on the system
    valid_apic_ids = get_valid_apic_ids_from_system()
    if valid_apic_ids is None:
        raise KernelInterfaceError(
            "Could not read APIC IDs from /sys/devices/system/cpu/*/topology/apic_id. "
            "Ensure the system exposes CPU topology information."
        )

    invalid_cpus = set(cpu_list) - valid_apic_ids
    if invalid_cpus:
        raise ValueError(
            f"Invalid APIC ID(s) specified: {sorted(invalid_cpus)}. "
            f"Valid APIC IDs on this system: {sorted(valid_apic_ids)}"
        )

    # Total CPUs is based on the max APIC ID + 1 for sizing purposes
    total_cpus = max(valid_apic_ids) + 1
    # Host reserved are all valid APIC IDs not in the available list
    available_cpus = set(cpu_list)
    host_reserved_cpus = sorted(list(valid_apic_ids - available_cpus))

    if 0 in available_cpus and len(host_reserved_cpus) == 0:
        if verbose:
            click.echo("Warning: APIC ID 0 is in available list but no host-reserved CPUs. Moving APIC ID 0 to host-reserved.", err=True)
        available_cpus.discard(0)
        host_reserved_cpus = [0]
        cpu_list = sorted(list(available_cpus))

    memory_pool = get_multikernel_memory_pool_from_iomem()
    if memory_pool is None:
        raise KernelInterfaceError(
            "Could not find multikernel memory pool in /proc/iomem. "
            "Please ensure the multikernel kernel module is loaded and memory is reserved."
        )
    memory_pool_base, memory_pool_bytes = memory_pool

    total_bytes = memory_pool_base + memory_pool_bytes
    host_reserved_bytes = memory_pool_base
    if verbose:
        click.echo(f"Parsed APIC ID specification: {cpus}")
        click.echo(f"  Valid APIC IDs on system: {sorted(valid_apic_ids)}")
        click.echo(f"  Host-reserved APIC IDs: {host_reserved_cpus}")
        click.echo(f"  Available APIC IDs: {cpu_list}")
        click.echo("Memory pool from /proc/iomem:")
        click.echo(f"  Base: {hex(memory_pool_base)}")
        click.echo(f"  Size: {memory_pool_bytes} bytes ({memory_pool_bytes / (1024**3):.2f} GB)")
        click.echo(f"  Total bytes: {total_bytes} bytes ({total_bytes / (1024**3):.2f} GB)")
        click.echo(f"  Host-reserved: {host_reserved_bytes} bytes ({host_reserved_bytes / (1024**3):.2f} GB)")

    cpu_allocation = CPUAllocation(
        total=total_cpus,
        host_reserved=host_reserved_cpus,
        available=cpu_list
    )

    memory_allocation = MemoryAllocation(
        total_bytes=total_bytes,
        host_reserved_bytes=host_reserved_bytes,
        memory_pool_base=memory_pool_base,
        memory_pool_bytes=memory_pool_bytes
    )

    device_dict = {}
    if devices:
        device_names = parse_device_list(devices)
        for device_name in device_names:
            device_info = detect_device_from_system(device_name)
            if device_info:
                device_dict[device_name] = device_info
                if verbose:
                    click.echo(f"Detected device '{device_name}':")
                    click.echo(f"  Type: {device_info.device_type}")
                    click.echo(f"  Compatible: {device_info.compatible}")
                    if device_info.pci_id:
                        click.echo(f"  PCI ID: {device_info.pci_id}")
                    if device_info.vendor_id is not None:
                        click.echo(f"  Vendor ID: 0x{device_info.vendor_id:04x}")
                    if device_info.device_id is not None:
                        click.echo(f"  Device ID: 0x{device_info.device_id:04x}")
                    if device_info.device_name:
                        click.echo(f"  Device Name: {device_info.device_name}")
            else:
                raise KernelInterfaceError(
                    f"Could not detect device '{device_name}' from system. "
                    f"Please ensure the device exists and is accessible, or use --input with a DTS file to specify device details."
                )

    hardware = HardwareInventory(
        cpus=cpu_allocation,
        memory=memory_allocation,
        devices=device_dict
    )

    tree = GlobalDeviceTree(
        hardware=hardware,
        instances={},
        device_references={}
    )

    return tree


@click.command()
@click.pass_context
@click.option('--input', '-i', help='Input DTS or DTB file containing all resources. Mutually exclusive with --cpus and --devices. When used, all resources must come from the file.')
@click.option('--cpus', '-c', help='APIC ID specification for baseline (e.g., "128-134" or "128,130,132"). Use physical APIC IDs, not logical CPU numbers. Mutually exclusive with --input. Memory will be parsed from /proc/iomem.')
@click.option('--devices', '-d', help='Device names (comma-separated, e.g., "enp9s0_dev,nvme0"). Mutually exclusive with --input. Creates minimal device entries in baseline.')
@click.option('--dry-run', is_flag=True, help='Validate without applying')
@click.option('--report', is_flag=True, help='Generate detailed validation report')
@click.option('--format', type=click.Choice(['text', 'json', 'yaml']),
              default='text', help='Report format (default: text)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def init(ctx: click.Context, input: Optional[str], cpus: Optional[str], devices: Optional[str], dry_run: bool, report: bool, format: str, verbose: bool):
    """
    Initialize baseline device tree configuration.

    Sets up the baseline device tree which describes hardware resources
    available for allocation. The baseline must contain ONLY resources
    (no instances). Instances are created via 'kerf create' using overlays.

    By default, the baseline is applied to the kernel after validation.
    Use --dry-run to validate without applying.

    You can either provide a DTS/DTB file via --input, or construct the
    baseline from command line arguments using --cpus. These options are
    mutually exclusive - when using --input, all resources must come from
    the DTS file. Memory will be automatically parsed from /proc/iomem
    when using --cpus.

    Examples:
        # Initialize from DTS file (all resources from file)
        kerf init --input=hardware.dts

        # Initialize from command line (APIC IDs 128-134, memory from /proc/iomem)
        kerf init --cpus=128-134

        # Initialize with APIC IDs and devices
        kerf init --cpus=128,130,132 --devices=enp9s0_dev,nvme0

        # Validate baseline without applying
        kerf init --input=hardware.dts --dry-run
    """
    try:
        # Validate that --input and resource specification options are mutually exclusive
        # When using --input, all resources must come from the DTS file
        if input and (cpus or devices):
            conflicting = []
            if cpus:
                conflicting.append("--cpus")
            if devices:
                conflicting.append("--devices")
            click.echo(f"Error: --input is mutually exclusive with {', '.join(conflicting)}.", err=True)
            click.echo("When using --input, all resources must come from the DTS/DTB file.", err=True)
            click.echo("Use either --input for a complete DTS/DTB file, or command-line options to construct baseline.", err=True)
            sys.exit(2)

        if not input and not cpus:
            click.echo("Error: Either --input or --cpus must be specified", err=True)
            click.echo("\nUsage:", err=True)
            click.echo("  kerf init --input=hardware.dts", err=True)
            click.echo("  kerf init --cpus=4-7", err=True)
            click.echo("  kerf init --cpus=4-7 --devices=enp9s0_dev", err=True)
            sys.exit(2)

        parser = DeviceTreeParser()
        dts_content = None

        if input:
            # Parse from input file
            input_path = Path(input)

            if not input_path.exists():
                click.echo(f"Error: Input file '{input}' does not exist", err=True)
                sys.exit(3)

            if input_path.suffix == '.dts':
                with open(input_path, 'r', encoding='utf-8') as f:
                    dts_content = f.read()
                tree = parser.parse_dts(dts_content)
            elif input_path.suffix == '.dtb':
                tree = parser.parse_dtb(str(input_path))
            else:
                click.echo(f"Error: Unsupported input format: {input_path.suffix}", err=True)
                click.echo("Supported formats: .dts, .dtb", err=True)
                sys.exit(2)
        else:
            # Build from command line arguments
            try:
                tree = build_baseline_from_cmdline(cpus, devices=devices, verbose=verbose)
            except ValueError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(2)
            except KernelInterfaceError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)

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
        if dts_content is not None:
            input_path_str = str(input) if input else "command-line"
            validator.set_dts_context(dts_content, input_path_str)

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

        debug = ctx.obj.get('debug', False) if ctx and ctx.obj else False

        if dry_run:
            click.echo(" Baseline validation passed")
            click.echo(" Baseline would be applied (dry-run mode)")
        else:
            try:
                mount_multikernel_fs(verbose=verbose)

                if debug:
                    try:
                        dtb_data = baseline_mgr.extractor.generate_global_dtb(tree)
                        fdt = libfdt.Fdt(dtb_data)
                        dts_parser = DeviceTreeParser()
                        dts_parser.fdt = fdt
                        dts_lines = dts_parser._fdt_to_dts_recursive(0, 0)  # pylint: disable=protected-access
                        dts_content = '\n'.join(dts_lines)

                        click.echo("Debug: Baseline DTS source being written to kernel:")
                        click.echo("─" * 70)
                        click.echo(dts_content)
                        click.echo("─" * 70)
                    except Exception as e:
                        click.echo(f"Debug: Failed to convert baseline DTB to DTS: {e}", err=True)

                if verbose:
                    click.echo("Writing baseline to kernel...")
                baseline_mgr.write_baseline(tree)
                click.echo("✓ Baseline applied to kernel successfully")
                click.echo("  Baseline: /sys/fs/multikernel/device_tree")
            except KernelInterfaceError as e:
                click.echo(f"Error: Failed to apply baseline: {e}", err=True)
                if verbose:
                    import traceback
                    traceback.print_exc()
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
