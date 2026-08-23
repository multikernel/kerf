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
from typing import Dict, List, Optional, Tuple

import click
import libfdt

try:
    import pyudev
except ImportError:
    pyudev = None

from ..baseline import BaselineManager
from ..create.main import parse_cpu_spec, parse_device_list, parse_memory_spec
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
    PoolMemoryRegion,
)
from ..pool_diff import ANY_NODE, PoolDiff, compute_pool_diff
from ..resources import get_busy_chunks_from_iomem
from ..runtime import DeviceTreeManager
from ..topology import cpu_numa_nodes, node_for_cpus


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
    Get set of valid APIC IDs from the system via /proc/cpuinfo.
    Returns set of valid APIC IDs or None if not available.
    """
    try:
        cpuinfo_path = Path('/proc/cpuinfo')
        if not cpuinfo_path.exists():
            return None

        apic_ids = set()
        with open(cpuinfo_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('apicid'):
                    parts = line.split(':')
                    if len(parts) == 2:
                        try:
                            apic_id = int(parts[1].strip())
                            apic_ids.add(apic_id)
                        except ValueError:
                            pass

        return apic_ids if apic_ids else None
    except (OSError, IOError):
        pass

    return None


_NODE_SPEC = re.compile(r"^(.+)@(.*)$")

PAGE_SIZE = 4096


NO_RESOURCE = "none"


def spec_is_empty(spec: str, option: str, synonyms: Tuple[str, ...] = ()) -> bool:
    """
    Whether an option spells "none of this resource".

    Args:
        spec: The option's value as typed on the command line
        option: The option's name, for the error message
        synonyms: Extra spellings that mean the same as "none"

    Returns:
        True if the option asks for nothing

    Raises:
        ValueError: If an empty spelling is mixed with real entries
    """
    empty = {NO_RESOURCE} | set(synonyms)
    parts = [p.strip().lower() for p in spec.split(",") if p.strip()]
    asked = [p for p in parts if p in empty]
    if not asked:
        return False
    if len(parts) > 1:
        raise ValueError(f"{option}={asked[0]} cannot be combined with other entries")
    return True


def parse_cpu_request(spec: str) -> List[int]:
    """
    Parse the pool CPU request, where "none" asks for no pool CPUs.

    Args:
        spec: APIC ID specification, or "none"

    Returns:
        The requested APIC IDs, empty for "none"

    Raises:
        ValueError: If the specification is malformed
    """
    if spec_is_empty(spec, "--cpus"):
        return []
    try:
        return parse_cpu_spec(spec)
    except ValueError as e:
        raise ValueError(f"Invalid CPU specification '{spec}': {e}") from e


def parse_device_request(spec: Optional[str]) -> List[str]:
    """
    Parse the pool device request, where "none" asks for no devices.

    Args:
        spec: Comma-separated device names, "none", or None

    Returns:
        The requested device names, empty for "none" and for no request

    Raises:
        ValueError: If "none" is mixed with device names
    """
    if not spec or spec_is_empty(spec, "--devices"):
        return []
    return parse_device_list(spec)


def validate_memory_request(requested: Dict[int, int]) -> None:
    """
    Reject pool sizes the kernel cannot honour, however they were asked for.

    Args:
        requested: Mapping of NUMA node id (ANY_NODE if not resolved yet) to size

    Raises:
        ValueError: If a size is zero, negative or not page aligned
    """
    for node, size in sorted(requested.items()):
        where = "the unpinned request" if node == ANY_NODE else f"node {node}"
        if size <= 0:
            raise ValueError(f"memory size for {where} must be greater than zero")
        if size % PAGE_SIZE:
            raise ValueError(
                f"memory size for {where} must be a multiple of {PAGE_SIZE} bytes"
            )


def parse_memory_request(spec: str) -> Dict[int, int]:
    """
    Parse a pool memory request into per-NUMA-node sizes.

    "2GB" asks for 2GB without naming a node, which the caller then
    resolves to the node of the requested CPUs; "8GB@0,8GB@1" asks for a
    specific amount per node, mirroring the device-tree unit address
    convention. The two forms cannot be mixed. "none", or its synonym
    "0", asks for no pool memory at all.

    Args:
        spec: Memory specification string

    Returns:
        Mapping of NUMA node id (-1 when the node is left to kerf) to size
        in bytes, empty for "none"

    Raises:
        ValueError: If the specification is malformed or a size is zero
                    or not page aligned
    """
    if spec_is_empty(spec, "--memory", synonyms=("0",)):
        return {}

    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts:
        raise ValueError("empty memory specification")

    requested: Dict[int, int] = {}
    for part in parts:
        match = _NODE_SPEC.match(part)
        if match:
            size_part, node_part = match.group(1), match.group(2)
            try:
                node = int(node_part)
            except ValueError as exc:
                raise ValueError(
                    f"invalid NUMA node '{node_part}' in '{part}' (expected SIZE@N)"
                ) from exc
            if node < 0:
                raise ValueError(f"NUMA node in '{part}' must not be negative")
            size = parse_memory_spec(size_part)
        else:
            node, size = ANY_NODE, parse_memory_spec(part)
        if node in requested:
            raise ValueError(f"node {node} specified twice")
        requested[node] = size

    if ANY_NODE in requested and len(requested) > 1:
        raise ValueError("cannot mix a plain size with SIZE@N entries")
    validate_memory_request(requested)
    return requested


def _cpu_ranges(cpus: List[int]) -> str:
    """Render APIC ids the way they are written on the command line."""
    parts = []
    for cpu in sorted(set(cpus)):
        if parts and cpu == parts[-1][1] + 1:
            parts[-1][1] = cpu
        else:
            parts.append([cpu, cpu])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in parts)


def pick_memory_node(cpu_list: List[int],
                     pool_cpus: Optional[set] = None,
                     pool_regions: Optional[List[PoolMemoryRegion]] = None
                     ) -> Tuple[int, str]:
    """
    Choose the NUMA node for a memory request that did not name one.

    Placement is policy, so kerf decides it and the kernel is only ever
    handed an explicit node.

    Args:
        cpu_list: APIC IDs the pool is being asked for
        pool_cpus: APIC IDs the pool already holds
        pool_regions: Chunks the pool already holds

    Returns:
        The chosen node and a short reason to show the user
    """
    node = node_for_cpus(cpu_list, cpu_numa_nodes())
    if node is not None:
        return node, f"from CPUs {_cpu_ranges(cpu_list)}"

    # A CPU the pool already took is offline, so neither sysfs nor
    # /proc/cpuinfo places it any more; its chunks still do.
    if set(cpu_list) & set(pool_cpus or ()):
        nodes = {r.node for r in (pool_regions or []) if r.node != ANY_NODE}
        if len(nodes) == 1:
            return nodes.pop(), "from the chunks the pool already holds"

    return 0, "no NUMA topology available, defaulting to node 0"


def resolve_memory_nodes(requested: Dict[int, int],
                         cpu_list: List[int],
                         pool_cpus: Optional[set] = None,
                         pool_regions: Optional[List[PoolMemoryRegion]] = None
                         ) -> Tuple[Dict[int, int], Optional[str]]:
    """
    Pin an unpinned memory request to a node before the kernel sees it.

    Args:
        requested: Mapping of NUMA node id (ANY_NODE for unpinned) to size
        cpu_list: APIC IDs the pool is being asked for
        pool_cpus: APIC IDs the pool already holds
        pool_regions: Chunks the pool already holds

    Returns:
        The request with every size on an explicit node, and a line
        describing the choice, or None if nothing had to be resolved
    """
    resolved = dict(requested)
    size = resolved.pop(ANY_NODE, None)
    if size is None:
        return resolved, None

    node, why = pick_memory_node(cpu_list, pool_cpus, pool_regions)
    resolved[node] = resolved.get(node, 0) + size
    return resolved, f"Memory: {size >> 20} MB on node {node} ({why})"


def build_baseline_from_cmdline(
    cpus: str,
    memory: Optional[str] = None,
    devices: Optional[str] = None,
    verbose: bool = False,
    pool_cpus: Optional[set] = None,
    pool_regions: Optional[List[PoolMemoryRegion]] = None
) -> GlobalDeviceTree:
    """
    Build a GlobalDeviceTree from command line arguments.

    Args:
        cpus: CPU specification string (e.g., "4-7" or "4,5,6,7"), or "none"
        memory: Pool memory request, either "2GB" on the node of the
                requested CPUs, "8GB@0,8GB@1" for specific nodes, or "none"
        devices: Optional device names (comma-separated, e.g., "enp9s0_dev,nvme0"),
                 or "none"
        verbose: Whether to print verbose output
        pool_cpus: APIC IDs the pool already holds
        pool_regions: Chunks the pool already holds

    Returns:
        GlobalDeviceTree with resources only (no instances)

    Raises:
        ValueError: If the CPU or memory specification is invalid
        KernelInterfaceError: If the system topology cannot be read
    """
    cpu_list = parse_cpu_request(cpus)

    # Silence about memory would otherwise read as "give it all back".
    if not memory:
        raise ValueError("--memory is required")
    requested = parse_memory_request(memory)

    # Validate against valid APIC IDs on the system
    valid_apic_ids = get_valid_apic_ids_from_system()
    if valid_apic_ids is None:
        raise KernelInterfaceError(
            "Could not read APIC IDs from /proc/cpuinfo. "
            "Ensure the system exposes CPU topology information."
        )

    # The host stops listing a CPU in /proc/cpuinfo once the pool takes it,
    # so a re-init has to count the pool's own CPUs as valid.
    valid_apic_ids = valid_apic_ids | set(pool_cpus or ())

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

    requested, note = resolve_memory_nodes(requested, cpu_list, pool_cpus, pool_regions)
    if note:
        click.echo(note)

    total_bytes = sum(requested.values())
    if verbose:
        click.echo(f"Parsed APIC ID specification: {cpus}")
        click.echo(f"  Valid APIC IDs on system: {sorted(valid_apic_ids)}")
        click.echo(f"  Host-reserved APIC IDs: {host_reserved_cpus}")
        click.echo(f"  Available APIC IDs: {cpu_list}")
        click.echo("Requested pool memory:")
        for node, size in sorted(requested.items()):
            click.echo(f"  node {node}: {size} bytes ({size / (1024**3):.2f} GB)")

    cpu_allocation = CPUAllocation(
        total=total_cpus,
        host_reserved=host_reserved_cpus,
        available=cpu_list
    )

    memory_allocation = MemoryAllocation(
        total_bytes=total_bytes,
        host_reserved_bytes=0,
        requested=requested
    )

    device_dict = {}
    device_names = parse_device_request(devices)
    if device_names:
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
                    f"Please ensure the device exists and is accessible, or use --input with a dumped DTB to specify device details."
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


INSTANCES_DIR = "/sys/fs/multikernel/instances"


def list_instance_names(instances_dir: str = INSTANCES_DIR) -> List[str]:
    """Names of the instances the kernel currently holds."""
    path = Path(instances_dir)
    if not path.exists():
        return []
    try:
        return sorted(child.name for child in path.iterdir() if child.is_dir())
    except OSError:
        return []


def pool_is_live(current: Optional[GlobalDeviceTree]) -> bool:
    """
    Whether the kernel already holds pool resources we have to diff against.

    A non-empty cpus list is not evidence: before a pool exists the root
    read-back lists the host's own online CPUs there. Only the pool branch
    of the kernel's device tree emits cpus-available, memory@N or devices.
    """
    if current is None:
        return False
    hardware = current.hardware
    return bool(hardware.cpus.available_free is not None
                or hardware.memory.regions
                or hardware.devices)


def pool_apic_ids(current: Optional[GlobalDeviceTree]) -> set:
    """APIC IDs the live pool holds, which the host no longer reports."""
    if not pool_is_live(current):
        return set()
    return set(current.hardware.cpus.available or [])


def pool_memory_regions(current: Optional[GlobalDeviceTree]) -> List[PoolMemoryRegion]:
    """Chunks the live pool holds, which place CPUs the host no longer reports."""
    if not pool_is_live(current):
        return []
    return list(current.hardware.memory.regions)


def read_current_pool(baseline_mgr) -> Optional[GlobalDeviceTree]:
    """
    Read the live pool back from the kernel.

    Before any pool exists the kernel still publishes a root device tree,
    but one that describes the host rather than a pool and that carries no
    memory node, so a failed read means "no pool yet", not an error.

    Args:
        baseline_mgr: BaselineManager to read through

    Returns:
        The baseline the kernel reports, or None if there is no pool yet
    """
    try:
        return baseline_mgr.read_baseline()
    except (ParseError, KernelInterfaceError):
        return None


def request_is_empty(requested: GlobalDeviceTree) -> bool:
    """Whether the request asks for nothing at all, so the pool goes away."""
    hardware = requested.hardware
    return not (hardware.cpus.available or hardware.memory.requested or hardware.devices)


def _print_diff(diff: PoolDiff) -> None:
    """Show what would move between the host and the pool."""
    def line(label, items):
        if items:
            click.echo(f"  {label}: {', '.join(str(i) for i in items)}")

    line("CPUs to pool", diff.cpus_to_pool)
    line("CPUs to host", diff.cpus_to_host)
    line("Memory to pool", [
        f"{size >> 20} MB" + ("" if node == ANY_NODE else f" on node {node}")
        for node, size in diff.memory_to_pool
    ])
    line("Memory to host", [f"{hex(r.base)} ({r.size >> 20} MB)" for r in diff.memory_to_host])
    line("Devices to pool", diff.devices_to_pool)
    line("Devices to host", diff.devices_to_host)


def _report_shortfall(live: GlobalDeviceTree, requested: GlobalDeviceTree) -> None:
    """Warn when the kernel could not shrink the pool all the way down."""
    for node, want in requested.hardware.memory.requested.items():
        if node == ANY_NODE:
            have = live.hardware.memory.memory_pool_bytes
        else:
            have = live.hardware.memory.bytes_on_node(node)
        if have > want:
            where = "any" if node == ANY_NODE else node
            click.echo(
                f"Note: node {where} still holds {(have - want) >> 20} MB more than "
                "requested; only whole idle chunks can be returned",
                err=True,
            )


def reconcile_pool(
    current: Optional[GlobalDeviceTree],
    requested: GlobalDeviceTree,
    busy_chunks: set,
    dry_run: bool,
    manager,
    baseline_mgr,
) -> Optional[PoolDiff]:
    """
    Bring the live pool in line with the requested baseline.

    An empty pool takes the baseline write; a live pool is reconciled with
    a /resources overlay transaction, since the kernel refuses a baseline
    write once it owns resources.

    Args:
        current: Baseline read back from the kernel, or None if there is none
        requested: Requested state
        busy_chunks: Bases of pool chunks that still hold an allocation
        dry_run: Report the plan without touching the kernel
        manager: DeviceTreeManager used to apply the overlay
        baseline_mgr: BaselineManager used for the initial write

    Returns:
        The applied (or planned) difference, or None if a baseline was written

    Raises:
        KernelInterfaceError: If the kernel rejects the write or the overlay
        ValidationError: If emptying the pool would strand running instances
    """
    if not pool_is_live(current):
        # The kernel rejects a baseline with no memory@N node, and there is
        # nothing to hand back anyway.
        if request_is_empty(requested):
            click.echo("Pool is already empty; nothing to do")
            return None
        if dry_run:
            click.echo("Baseline validation passed; would write the initial baseline (dry-run)")
            return None
        baseline_mgr.write_baseline(requested)
        click.echo("✓ Baseline applied to kernel successfully")
        return None

    diff = compute_pool_diff(current, requested, busy_chunks=busy_chunks)
    _print_diff(diff)
    if diff.is_empty():
        click.echo("✓ Pool already matches the request; nothing to do")
        _report_shortfall(current, requested)
        return diff
    if dry_run:
        click.echo("Would apply the changes above (dry-run)")
        _report_shortfall(current, requested)
        return diff

    if request_is_empty(requested):
        held = list_instance_names()
        if held:
            raise ValidationError(
                f"The pool still runs {len(held)} instance(s): "
                f"delete instances {', '.join(held)} first"
            )

    with manager.lock():
        tx_id = manager.apply_dtbo(manager.overlay_gen.generate_pool_overlay(diff))
    click.echo(f"✓ Pool updated (transaction {tx_id})")

    if request_is_empty(requested):
        # /resources carries no memory@N once the pool is gone, so there is
        # nothing left to read back or compare against.
        return diff

    try:
        live = baseline_mgr.read_baseline()
    except (ParseError, KernelInterfaceError) as e:
        # The transaction already landed; a failed read-back is worth a word,
        # not a failure.
        click.echo(f"Note: could not read the pool back after the transaction: {e}", err=True)
    else:
        _report_shortfall(live, requested)
    return diff


def _dump_baseline_dts(baseline_mgr: BaselineManager, tree: GlobalDeviceTree) -> None:
    """Print the DTS the baseline write would carry."""
    try:
        dtb_data = baseline_mgr.extractor.generate_global_dtb(tree)
        fdt = libfdt.Fdt(dtb_data)
        dts_parser = DeviceTreeParser()
        dts_parser.fdt = fdt
        dts_lines = dts_parser._fdt_to_dts_recursive(0, 0)  # pylint: disable=protected-access

        click.echo("Debug: Baseline DTS source being written to kernel:")
        click.echo("─" * 70)
        click.echo('\n'.join(dts_lines))
        click.echo("─" * 70)
    except Exception as e:  # pylint: disable=broad-except
        click.echo(f"Debug: Failed to convert baseline DTB to DTS: {e}", err=True)


@click.command()
@click.pass_context
@click.option('--input', '-i', help='Baseline DTB to replay, as written by "kerf dump". Mutually exclusive with --cpus, --memory and --devices.')
@click.option('--cpus', '-c', help='APIC ID specification for baseline (e.g., "128-134" or "128,130,132"), or "none" for no pool CPUs. Use physical APIC IDs, not logical CPU numbers. Mutually exclusive with --input.')
@click.option('--memory', '-m', help='Pool memory: SIZE (e.g. "2GB") on the node of the requested CPUs, per-node "8GB@0,8GB@1", or "none" for no pool memory. Required with --cpus, mutually exclusive with --input.')
@click.option('--devices', '-d', help='Device names (comma-separated, e.g., "enp9s0_dev,nvme0"), or "none" for no devices. Mutually exclusive with --input. Creates minimal device entries in baseline.')
@click.option('--dry-run', is_flag=True, help='Report the plan without applying it. Still reads the pool from the kernel, so it needs root.')
@click.option('--report', is_flag=True, help='Generate detailed validation report. Ignored when the request asks for no memory.')
@click.option('--format', type=click.Choice(['text', 'json', 'yaml']),
              default='text', help='Report format (default: text)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def init(ctx: click.Context, input: Optional[str], cpus: Optional[str], memory: Optional[str], devices: Optional[str], dry_run: bool, report: bool, format: str, verbose: bool):
    """
    Initialize baseline device tree configuration.

    Sets up the baseline device tree which describes hardware resources
    available for allocation. The baseline must contain ONLY resources
    (no instances). Instances are created via 'kerf create' using overlays.

    The command is idempotent: the requested state is compared against the
    pool the kernel reports, and only the difference is applied. An empty
    pool takes the baseline write; a live pool is reconciled with a
    /resources overlay transaction. Use --dry-run to see the plan without
    applying it. Even --dry-run reads the pool from the kernel, so every
    form needs root.

    You can either replay a baseline dumped with 'kerf dump' via --input,
    or construct the request from command line arguments using --cpus and
    --memory. These options are mutually exclusive.

    Every resource is spelled out: --cpus=none, --memory=none (or
    --memory=0) and --devices=none ask for none of that resource, and a
    request that asks for nothing returns the whole pool to the host.

    A --memory size that names no node is placed on the NUMA node of the
    requested CPUs. Kerf resolves it here so the kernel is always handed an
    explicit node and never picks the placement itself.

    Examples:
        # Replay a baseline captured with 'kerf dump -o host.dtb'
        kerf init --input=host.dtb

        # Request 1GB of pool memory on the node of the requested CPUs
        kerf init --cpus=128-134 --memory=1GB

        # Request memory per NUMA node
        kerf init --cpus=128-134 --memory=8GB@0,8GB@1

        # Shrink the pool back to 1GB and 2 CPUs
        kerf init --cpus=128,129 --memory=1GB

        # Keep the CPUs but hand every chunk back
        kerf init --cpus=128,129 --memory=none

        # Return every pool resource to the host
        kerf init --cpus=none --memory=none

        # Show what would change without applying
        kerf init --cpus=128-134 --memory=1GB --dry-run
    """
    try:
        # Validate that --input and resource specification options are mutually exclusive
        # When using --input, all resources must come from the file
        if input and (cpus or memory or devices):
            conflicting = []
            if cpus:
                conflicting.append("--cpus")
            if memory:
                conflicting.append("--memory")
            if devices:
                conflicting.append("--devices")
            click.echo(f"Error: --input is mutually exclusive with {', '.join(conflicting)}.", err=True)
            click.echo("When using --input, all resources must come from the file.", err=True)
            click.echo("Use either --input for a complete DTB, or command-line options to construct baseline.", err=True)
            sys.exit(2)

        if not input and not cpus:
            click.echo("Error: Either --input or --cpus must be specified", err=True)
            click.echo("\nUsage:", err=True)
            click.echo("  kerf init --input=host.dtb", err=True)
            click.echo("  kerf init --cpus=4-7 --memory=1GB", err=True)
            click.echo("  kerf init --cpus=4-7 --memory=1GB@0 --devices=enp9s0_dev", err=True)
            click.echo("  kerf init --cpus=none --memory=none", err=True)
            sys.exit(2)

        parser = DeviceTreeParser()

        baseline_mgr = BaselineManager()
        manager = DeviceTreeManager()
        mount_multikernel_fs(verbose=verbose)
        current = read_current_pool(baseline_mgr)
        live_cpus = pool_apic_ids(current)
        live_regions = pool_memory_regions(current)

        if input:
            # Parse from input file
            input_path = Path(input)

            if not input_path.exists():
                click.echo(f"Error: Input file '{input}' does not exist", err=True)
                sys.exit(3)

            try:
                tree = parser.parse_dtb(str(input_path))
            except ParseError as e:
                click.echo(f"Error: {e}", err=True)
                click.echo("--input takes a DTB as written by 'kerf dump'.", err=True)
                sys.exit(2)

            try:
                validate_memory_request(tree.hardware.memory.requested)
            except ValueError as e:
                click.echo(f"Error: {input}: {e}", err=True)
                sys.exit(2)

            tree.hardware.memory.requested, note = resolve_memory_nodes(
                tree.hardware.memory.requested, tree.hardware.cpus.available,
                live_cpus, live_regions)
            if note:
                click.echo(note)
        else:
            # Build from command line arguments
            try:
                tree = build_baseline_from_cmdline(cpus, memory=memory, devices=devices,
                                                   verbose=verbose, pool_cpus=live_cpus,
                                                   pool_regions=live_regions)
            except ValueError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(2)
            except KernelInterfaceError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)

        try:
            baseline_mgr.validate_baseline(tree)
        except ValidationError as e:
            click.echo(f"Error: Invalid baseline configuration: {e}", err=True)
            click.echo("\nBaseline must contain:", err=True)
            click.echo("   /resources (hardware inventory)", err=True)
            click.echo("   /instances (must be empty or absent)", err=True)
            click.echo("\nInstances should be created via 'kerf create'", err=True)
            sys.exit(1)

        # The resource validator reads a pool with no memory as unusable,
        # which is exactly what a request for no memory asks for.
        if tree.hardware.memory.requested:
            validator = MultikernelValidator()

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

        if debug and not pool_is_live(current):
            _dump_baseline_dts(baseline_mgr, tree)

        try:
            reconcile_pool(current, tree, get_busy_chunks_from_iomem(), dry_run,
                           manager, baseline_mgr)
        except ValidationError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except KernelInterfaceError as e:
            click.echo(f"Error: {e}", err=True)
            click.echo("Run 'dmesg | tail' for the kernel's reason (it names the busy CPU or chunk).", err=True)
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
