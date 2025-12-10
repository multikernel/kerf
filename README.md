![Kerf Logo](logo.png)

# Kerf: Multikernel Management System

## Overview

`kerf` is a comprehensive multikernel management system designed to orchestrate and manage multiple kernel instances on a single host. Starting with advanced device tree compilation and validation, `kerf` provides the foundation for complete multikernel lifecycle management.

Unlike standard tools that only perform basic format conversion, `kerf` understands multikernel semantics and **always validates** resource allocations and detects conflicts. The system is architected to evolve into a complete multikernel runtime environment.

## Features

`kerf` is a comprehensive multikernel management platform with the following capabilities:

- **Resource Pool Initialization**: Initialize hardware resource pools available for multikernel allocation
- **Resource Conflict Detection**: Detect and prevent allocation conflicts for CPUs, memory regions, and devices
- **Resource Validation**: Ensure allocations don't exceed available resources and references are valid
- **Atomicity**: Validate all allocations together before deployment
- **Kernel Loading & Execution**: Load and execute multiple kernel instances with proper isolation
- **Instance Lifecycle**: Create, delete, and manage kernel instances
- **Dynamic Resource Management**: Allocation and deallocation of system resources
- **Monitoring & Debugging**: Real-time monitoring of kernel instances and system health
- **Security & Isolation**: Advanced security policies and isolation mechanisms
- **Orchestration**: High-level orchestration of complex multikernel workloads

## Architecture

### Design Philosophy

The `kerf` system is built on foundational principles that support both current resource pool management and future multikernel runtime features:

1. **Single Source of Truth**: Baseline DTS describes hardware resources available for allocation
2. **Mandatory Validation**: Every operation validates the configuration - validation is not optional
3. **Fail-Fast**: Catch resource conflicts immediately, never produce invalid output
4. **Overlay-based Management**: Dynamic instance changes are managed via device tree overlays
5. **Extensible Architecture**: Designed to support future kernel loading, execution, and management capabilities
6. **Developer-Friendly**: Clear error messages with suggestions for fixing problems
7. **Runtime-Ready**: Current design anticipates future kernel execution and lifecycle management needs

### Compilation Model

**Resource pool initialization:**
```
Input: Baseline DTS (resources only)
         │
         ▼
    ┌─────────┐
    │ kerf    │ ← Initializes resource pool
    │  init   │   and validates
    └─────────┘
         │
         ▼
    Baseline DTB
    (resources only)
    → /sys/fs/multikernel/device_tree
```

**Overlay-based dynamic changes:**
```
Current State              Modified State
(Baseline + Overlays)      (After change)
         │                       │
         ├───────────────────────┤
         │                       │
         ▼                       ▼
    ┌─────────┐             ┌─────────┐
    │ Compute │             │ Compute │
    │   Delta │             │  Delta  │
    └─────────┘             └─────────┘
         │                       │
         └───────────┬───────────┘
                     │
                     ▼
              ┌─────────────┐
              │ kerf        │ ← Validates full state
              │ (create/    │   before generating overlay
              │  update/    │
              │  delete)    │
              └─────────────┘
                     │
                     ▼
                 DTBO Overlay
                     │
                     ▼
    → /sys/fs/multikernel/overlays/new
                     │
                     ▼
              Applied Overlay
    → /sys/fs/multikernel/overlays/tx_XXX/
```

**Complete system state:**
```
Baseline DTB (static)
         │
         ├─── Overlay tx_101 (instance: web-server)
         ├─── Overlay tx_102 (instance: database)
         └─── Overlay tx_103 (update: web-server resources)
                    │
                    ▼
         Effective Device Tree
    (Baseline + All Applied Overlays)
                    │
                    ▼
         Kernel Instance Views
    /sys/fs/multikernel/instances/*
```

**Key Points:**
- **Baseline contains only resources**: Hardware resources available for allocation, loaded once via `kerf init`
- **Instances created via overlays**: Dynamic instance lifecycle managed through device tree overlays (DTBO)
- **Overlay generation**: Computes delta between current and modified state, generates minimal DTBO
- **Transactional overlays**: Each overlay is a transaction with rollback support via `rmdir`
- **Validation is mandatory**: Always validates full state (baseline + all overlays) before applying
- **Single source of truth**: Baseline DTB is the authoritative resource configuration, overlays add instances dynamically

## Current Capabilities

### Resource Pool Management & Validation
- **Resource Pool Initialization**: Initialize hardware resource pools for multikernel allocation
- **Advanced Validation**: Comprehensive resource conflict detection and validation
- **Baseline Management**: Initialize and manage baseline device tree containing hardware resources
- **Format Support**: DTS to DTB compilation for baseline configuration
- **Error Reporting**: Detailed error messages with actionable suggestions
- **Resource Analysis**: Complete resource utilization reporting
- **CPU & NUMA Topology**: Full support for CPU topology and NUMA-aware resource allocation

### Command Line Interface
```bash
# Initialize resource pool with CPUs (memory parsed from /proc/iomem)
kerf init --cpus=4-7

# Initialize with CPUs and devices
kerf init --cpus=4-31 --devices=enp9s0_dev,nvme0

# Create kernel instance with resource allocation
kerf create web-server --cpus=4-7 --memory=2GB
kerf create database --cpu-count=8 --memory=16GB

# Load kernel image with initrd and boot parameters
kerf load --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \
          --cmdline="root=/dev/sda1 ro" --id=1

# Boot a kernel instance
kerf exec web-server

# Show kernel instance information
kerf show
kerf show web-server

# Shutdown a running kernel instance
kerf kill web-server

# Unload kernel image from an instance
kerf unload web-server

# Delete a kernel instance
kerf delete web-server

# Use --help for detailed options and usage
kerf --help
kerf <command> --help
```

### Technical Foundation
The current resource pool management provides essential building blocks for future multikernel capabilities:

- **Resource Pool Initialization**: Initializes hardware resource pools for safe multikernel allocation
- **Resource Validation**: Ensures safe resource allocation before kernel execution
- **Instance Isolation**: Provides the foundation for secure kernel isolation
- **Configuration Management**: Enables consistent and validated system configurations
- **Error Handling**: Establishes patterns for robust error reporting and recovery
- **Extensible Architecture**: Designed to support future kernel management APIs

These foundational capabilities are essential for safe and reliable multikernel execution, making `kerf` the ideal platform for building comprehensive multikernel management systems.


## Global Device Tree Format

### Structure Overview

The baseline device tree contains only the **Resources** section, which describes all physical hardware available for allocation. Instances and device references are added dynamically via overlays when using `kerf create`.

1. **Resources** (`/resources`): Complete description of all physical resources (baseline only)
2. **Instances** (`/instances`): Resource assignments for each spawn kernel (added via overlays)
3. **Device References**: Linkage between instances and hardware devices (added via overlays)

### Baseline Example

The baseline contains hardware resources used for allocation. Resources are typically passed via command line arguments during the `kerf init` command. Instances are created dynamically via overlays using `kerf create`.

### Mapping to Kernel Filesystem Interface

**Device Tree Structure → Kernel Filesystem Interface:**

```
DTS: /resources                          →  /sys/kernel/multikernel/device_tree (writable, single source of truth)
DTS: /instances/web-server               →  /sys/kernel/multikernelinstances/web-server/ (read-only)
DTS: /instances/database                 →  /sys/kernel/multikernel/instances/database/ (read-only)
DTS: /instances/compute                  →  /sys/kernel/multikernel/instances/compute/ (read-only)
```

**Name-based addressing:**
- Instance node name in DTS (`web-server`) = directory name in kernel filesystem (`instances/web-server/`)
- Kernel assigns numeric IDs, but users reference by name
- No manual ID coordination needed
- Instance directories are auto-generated by the kernel from the global device tree

## Validation Rules

### Validation is Mandatory

**All `kerf` operations perform validation automatically:**
- Compiling DTS to DTB → validates
- Converting formats → validates
- Generating reports → validates first

**Validation cannot be disabled or skipped.**

### CPU Allocation Validation

**Rules:**
1. CPUs must be defined in the baseline resource pool
2. No CPU can be allocated to multiple instances
3. CPU lists should be explicitly enumerated

### Memory Allocation Validation

**Rules:**
1. Memory regions must be within the baseline memory pool
2. Memory regions cannot overlap between instances
3. Sum of all allocations must not exceed baseline memory pool size
4. Memory base addresses must be page-aligned (4KB = 0x1000)


### Device Allocation Validation

**Rules:**
1. Referenced devices must be defined in the baseline
2. Devices can only be allocated to one instance (exclusive access)
3. Device references must be valid (no dangling phandles)
4. SR-IOV VF numbers must be within device limits
5. Namespace IDs must be within device limits

### Global Resource Validation

**Rules:**
1. Instance names must be unique
2. All phandle references must resolve
3. Baseline resource configuration must be complete and consistent


## Integration with Kernel

### Kernel Interface

The kernel exposes a filesystem interface (mounted at `/sys/fs/multikernel/`) that manages baseline resources and overlay-based instance changes:

**Kernel Interface Structure:**
```
/sys/fs/multikernel/
├── device_tree              # Baseline DTB (resources only, writable via kerf init)
├── overlays/                # Overlay subsystem
│   ├── new                 # Write DTBO here to apply overlay
│   ├── tx_101/             # Applied overlay transaction
│   │   ├── id              # Transaction ID: "101"
│   │   ├── status          # "applied" | "failed" | "removed"
│   │   ├── dtbo            # Original overlay blob (binary)
│   │   └── ...
│   └── tx_102/
│       └── ...
└── instances/              # Runtime kernel instances (read-only)
    ├── web-server/
    │   ├── id              # Instance ID
    │   ├── status          # Instance status
    │   └── ...
    └── ...
```

**Key Design Principles:**
- **Baseline Separation**: Baseline (`device_tree`) contains only resources - no instances
- **Overlay-based Changes**: All dynamic changes (create, update, delete instances) via overlays
- **Rollback Support**: Remove overlay transaction directory (`rmdir /sys/fs/multikernel/overlays/tx_XXX/`) to rollback changes
- **Kernel-Generated**: Instance directories auto-generated from baseline + applied overlays


## Dependencies

### Required Dependencies

```toml
[tool.poetry.dependencies]
python = "^3.8"
pylibfdt = "^1.7.0"      # Device tree parsing (from dtc project)
```

### Installation

```bash
# From source (recommended for development)
git clone https://github.com/multikernel/kerf.git
cd kerf
# Installs 'kerf' command to ~/.local/bin/kerf
pip install -e .

# Installs 'kerf' command to the system Python's scripts directory
# (typically /usr/local/bin/kerf, or /usr/bin/kerf if using system Python)
sudo pip install .

```

### Getting Started

```bash
# Install in development mode
pip install -e .

# Test the installation
kerf --help
kerf init --help

# Try with example baseline configuration
kerf init --input=examples/baseline.dts --report
```

## Examples

The `examples/` directory contains sample baseline Device Tree Source (DTS) files demonstrating various hardware resource configurations:

- **`baseline.dts`** - Complete baseline with CPU, memory, and device resources (32 CPUs, 16GB memory)
- **`minimal.dts`** - Simple baseline for testing and development (8 CPUs, 8GB memory)
- **`edge_computing.dts`** - Edge computing baseline with GPU support for AI inference (16 CPUs, 32GB memory)
- **`numa_topology.dts`** - Advanced NUMA topology baseline with 4 NUMA nodes and topology-aware allocation
- **`system.dts`** - Example baseline with various device configurations
- **`conflict_example.dts`** - Intentionally invalid baseline demonstrating common validation errors

**Note**: All baseline files contain **only** hardware resources - no instances. Instances are created dynamically via overlays using `kerf create` command.

## CPU and NUMA Topology Support

Kerf provides comprehensive support for CPU and NUMA topology management:

### Key Features
- **CPU Topology**: Socket, core, and thread mapping with SMT/hyperthreading support
- **NUMA Awareness**: NUMA node definition with memory regions and CPU assignments
- **Topology Policies**: CPU affinity (`compact`, `spread`, `local`) and memory policies (`local`, `interleave`, `bind`)
- **Performance Validation**: Automatic validation of topology constraints and performance warnings

For detailed information about CPU and NUMA topology support, see [CPU_NUMA_TOPOLOGY.md](docs/CPU_NUMA_TOPOLOGY.md).

## References

- **Device Tree Specification**: https://devicetree-specification.readthedocs.io/
- **libfdt Documentation**: https://git.kernel.org/pub/scm/utils/dtc/dtc.git/tree/Documentation

