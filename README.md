# Kerf: Multikernel Management System

## Overview

`kerf` is a comprehensive multikernel management system designed to orchestrate and manage multiple kernel instances on a single host. Starting with advanced device tree compilation and validation, `kerf` provides the foundation for complete multikernel lifecycle management.

Unlike standard tools that only perform basic format conversion, `kerf` understands multikernel semantics and **always validates** resource allocations and detects conflicts. The system is architected to evolve into a complete multikernel runtime environment.

## Vision & Roadmap

### Current Phase: Device Tree Foundation
`kerf` currently provides the essential device tree compilation and validation capabilities needed for multikernel systems:

1. **Resource Conflict Detection**: Multiple instances might accidentally be allocated the same CPUs, overlapping memory regions, or the same devices
2. **Over-Allocation**: The sum of all allocations might exceed available resources
3. **Invalid References**: Instances might reference non-existent hardware or devices reserved for the host
4. **Atomicity**: All allocations should be validated together before deployment

### Future Phases: Complete Multikernel Runtime
The `kerf` system is designed to evolve into a comprehensive multikernel management platform:

- **Kernel Loading & Execution**: Load and execute multiple kernel instances with proper isolation
- **Resource Management**: Dynamic allocation and deallocation of system resources
- **Instance Lifecycle**: Start, stop, pause, and migrate kernel instances
- **Monitoring & Debugging**: Real-time monitoring of kernel instances and system health
- **Security & Isolation**: Advanced security policies and isolation mechanisms
- **Orchestration**: High-level orchestration of complex multikernel workloads

The current device tree foundation provides the critical infrastructure needed for these advanced capabilities.

## Architecture

### Design Philosophy

The `kerf` system is built on foundational principles that support both current device tree capabilities and future multikernel runtime features:

1. **Single Source of Truth**: Baseline DTS describes hardware resources available for allocation
2. **Mandatory Validation**: Every operation validates the configuration - validation is not optional
3. **Fail-Fast**: Catch resource conflicts immediately, never produce invalid output
4. **Overlay-based Management**: Dynamic instance changes are managed via device tree overlays
5. **Extensible Architecture**: Designed to support future kernel loading, execution, and management capabilities
6. **Developer-Friendly**: Clear error messages with suggestions for fixing problems
7. **Runtime-Ready**: Current design anticipates future kernel execution and lifecycle management needs

### Compilation Model

**Baseline initialization:**
```
Input: Baseline DTS (resources only)
         │
         ▼
    ┌─────────┐
    │ kerf    │ ← Always validates
    │  init   │
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
- **Baseline contains only resources**: Hardware inventory available for allocation, loaded once via `kerf init`
- **Instances created via overlays**: Dynamic instance lifecycle managed through device tree overlays (DTBO)
- **Overlay generation**: Computes delta between current and modified state, generates minimal DTBO
- **Transactional overlays**: Each overlay is a transaction with rollback support via `rmdir`
- **Validation is mandatory**: Always validates full state (baseline + all overlays) before applying
- **Single source of truth**: Baseline DTB is the authoritative resource configuration, overlays add instances dynamically

## Current Capabilities

### Device Tree Management & Validation
- **Advanced Validation**: Comprehensive resource conflict detection and validation
- **Baseline Management**: Initialize and manage baseline device tree containing hardware resources
- **Format Support**: DTS to DTB compilation for baseline configuration
- **Error Reporting**: Detailed error messages with actionable suggestions
- **Resource Analysis**: Complete resource utilization reporting
- **CPU & NUMA Topology**: Full support for CPU topology and NUMA-aware resource allocation

### Command Line Interface
```bash
# Initialize baseline device tree (resources only)
kerf init --input=baseline.dts --apply

# Validate baseline with detailed report
kerf init --input=baseline.dts --report --format=text

# Validate baseline (dry-run, no kernel update)
kerf init --input=baseline.dts --verbose

# Load kernel image with initrd and boot parameters
kerf load --kernel=/boot/vmlinuz --initrd=/boot/initrd.img \
          --cmdline="root=/dev/sda1 ro" --id=1

# Load kernel with multikernel ID
kerf load -k /boot/vmlinuz -i /boot/initrd.img -c "console=ttyS0" \
          --id=2 --verbose

# Create kernel instance with explicit CPU allocation
kerf create web-server --cpus=4-7 --memory=2GB

# Create instance with auto-allocated CPU count
kerf create database --cpu-count=8 --memory=16GB

# Create with topology-aware auto-allocation
kerf create compute --cpu-count=16 --memory=32GB --numa-nodes=0,1 --cpu-affinity=spread --memory-policy=interleave

# Validate instance creation without applying
kerf create test-instance --cpu-count=4 --memory=2GB --dry-run
```

### Modular Architecture
The `kerf` system is designed with a modular architecture that supports incremental development:

- **`kerf init`**: Initialize baseline device tree (resources only) (current)
- **`kerf create`**: Create a kernel instance (current)
- **`kerf load`**: Kernel loading via kexec_file_load syscall (current)
- **`kerf exec`**: Kernel execution (future)
- **`kerf update`**: Update a kernel instance (future)
- **`kerf kill`**: Kill a kernel instance (future)
- **`kerf delete`**: Delete a kernel instance (future)

This modular design allows users to adopt `kerf` incrementally, starting with device tree validation and expanding to full multikernel management as features become available.

### Technical Foundation
The current device tree foundation provides essential building blocks for future multikernel capabilities:

- **Resource Validation**: Ensures safe resource allocation before kernel execution
- **Instance Isolation**: Provides the foundation for secure kernel isolation
- **Configuration Management**: Enables consistent and validated system configurations
- **Error Handling**: Establishes patterns for robust error reporting and recovery
- **Extensible Architecture**: Designed to support future kernel management APIs

These foundational capabilities are essential for safe and reliable multikernel execution, making `kerf` the ideal platform for building comprehensive multikernel management systems.

## Future Roadmap

### Phase 2: Kernel Loading & Execution
- **Kernel Image Management**: Load and manage multiple kernel images
- **Instance Boot**: Start kernel instances with validated device trees
- **Resource Binding**: Bind allocated resources to running instances
- **Instance Monitoring**: Basic health monitoring of running instances

### Phase 3: Advanced Management
- **Dynamic Resource Allocation**: Runtime resource reallocation
- **Instance Migration**: Move instances between hosts
- **Advanced Security**: Enhanced isolation and security policies
- **Orchestration APIs**: High-level management interfaces

## Global Device Tree Format

### Structure Overview

The global device tree contains three main sections that map directly to the kernfs hierarchy:

1. **Resources** (`/resources`): Complete description of all physical resources
2. **Instances** (`/instances`): Resource assignments for each spawn kernel
3. **Device References**: Linkage between instances and hardware devices

### Complete Example

```dts
/multikernel-v1/;

/ {
    compatible = "linux,multikernel-host";
    
    // ========== MAPS TO /sys/kernel/multikernel/device_tree ==========
    resources {
        cpus {
            total = <32>;
            host-reserved = <0 1 2 3>;
            available = <4 5 6 7 8 9 10 11 12 13 14 15 
                        16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31>;
        };
        
        memory {
            total-bytes = <0x0 0x400000000>;      // 16GB
            host-reserved-bytes = <0x0 0x80000000>; // 2GB
            memory-pool-base = <0x80000000>;
            memory-pool-bytes = <0x0 0x380000000>;  // 14GB
        };
        
        devices {
            eth0: ethernet@0 {
                compatible = "intel,i40e";
                pci-id = "0000:01:00.0";
                sriov-vfs = <8>;
                host-reserved-vf = <0>;
                available-vfs = <1 2 3 4 5 6 7>;
            };
            
            nvme0: storage@0 {
                compatible = "nvme";
                pci-id = "0000:02:00.0";
                namespaces = <4>;
                host-reserved-ns = <1>;
                available-ns = <2 3 4>;
            };
        };
    };
    
    // ========== MAPS TO /sys/kernel/multikernel/instances/ ==========
    instances {
        // Maps to /sys/kernel/multikernel/instances/web-server/
        web-server {
            id = <1>;
            
            resources {
                cpus = <4 5 6 7>;
                memory-base = <0x80000000>;
                memory-bytes = <0x80000000>;  // 2GB
                devices = <&eth0_vf1>;
            };
            
        };
        
        // Maps to /sys/kernel/multikernel/instances/database/
        database {
            id = <2>;
            
            resources {
                cpus = <8 9 10 11 12 13 14 15>;
                memory-base = <0x100000000>;
                memory-bytes = <0x200000000>;  // 8GB
                devices = <&eth0_vf2>, <&nvme0_ns2>;
            };
            
        };
        
        // Maps to /sys/kernel/multikernel/instances/compute/
        compute {
            id = <3>;
            
            resources {
                cpus = <16 17 18 19 20 21 22 23>;
                memory-base = <0x300000000>;
                memory-bytes = <0x100000000>;  // 4GB
            };
            
        };
    };
    
    // ========== DEVICE REFERENCES (phandle targets) ==========
    eth0_vf1: ethernet-vf@1 {
        parent = <&eth0>;
        vf-id = <1>;
    };
    
    eth0_vf2: ethernet-vf@2 {
        parent = <&eth0>;
        vf-id = <2>;
    };
    
    nvme0_ns2: nvme-ns@2 {
        parent = <&nvme0>;
        namespace-id = <2>;
    };
};
```

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
- Instance directories and `device_tree_source` files are auto-generated by the kernel from the global device tree

## Validation Rules

### Validation is Mandatory

**All `kerf` operations perform validation automatically:**
- Compiling DTS to DTB → validates
- Converting formats → validates
- Generating reports → validates first

**Validation cannot be disabled or skipped.**

### CPU Allocation Validation

**Rules:**
1. All CPUs must exist in hardware inventory (0 to `total-1`)
2. CPUs must be in the `available` list (not `host-reserved`)
3. No CPU can be allocated to multiple instances
4. CPU lists should be explicitly enumerated

**Error Examples:**
```
ERROR: Instance database: CPU 35 does not exist (hardware has 0-31)
ERROR: Instance web-server: CPU 2 is reserved for host kernel
ERROR: Instance web-server and compute: CPU overlap detected (CPUs 8-11)
```

### Memory Allocation Validation

**Rules:**
1. All memory regions must be within memory pool bounds
2. Memory regions cannot overlap between instances
3. Sum of all allocations must not exceed memory pool size
4. Memory base addresses must be page-aligned (4KB = 0x1000)

**Error Examples:**
```
ERROR: Instance database: Memory [0x50000000-0x60000000] outside memory pool
ERROR: Instance web-server and database: Memory overlap [0x100000000-0x120000000]
ERROR: Total memory allocation (16GB) exceeds memory pool (14GB)
WARNING: Instance compute: Memory base 0x80000001 not page-aligned
```

### Device Allocation Validation

**Rules:**
1. Referenced devices must exist in hardware inventory
2. Devices can only be allocated to one instance (exclusive access)
3. Device references must be valid (no dangling phandles)
4. SR-IOV VF numbers must be within available range
5. Namespace IDs must be within available range

**Error Examples:**
```
ERROR: Instance database: Reference to non-existent device 'nvme1'
ERROR: Instance web-server and compute: Both allocated eth0:vf2
ERROR: Instance database: VF ID 10 exceeds available VFs (1-7)
ERROR: Instance database: Namespace 1 is reserved for host kernel
```

### Global Resource Validation

**Rules:**
1. Instance names must be unique
2. Instance IDs must be unique
3. All phandle references must resolve
4. Hardware inventory must be complete and consistent

**Error Examples:**
```
ERROR: Duplicate instance name: "web-server" appears twice
ERROR: Duplicate instance ID: 2 assigned to both database and compute
ERROR: Dangling phandle reference: eth0_vf99 not defined
WARNING: 12 CPUs (43% of memory pool) are unallocated
```


## Command-Line Interface

### Basic Commands

```bash
# Initialize baseline device tree (resources only)
kerf init --input=baseline.dts --apply

# Validate baseline without applying
kerf init --input=baseline.dts

# Generate detailed validation report
kerf init --input=baseline.dts --report

# Validate with verbose output
kerf init --input=baseline.dts --verbose
```

### Report Formats

```bash
# Human-readable text (default)
kerf init --input=baseline.dts --report

# JSON for tooling integration
kerf init --input=baseline.dts --report --format=json

# YAML for configuration management
kerf init --input=baseline.dts --report --format=yaml
```

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

### Workflow: Initial Setup

```bash
# Step 1: Write baseline DTS describing hardware resources only
vim baseline.dts
# Baseline contains only /resources - no instances

# Step 2: Initialize baseline device tree
kerf init --input=baseline.dts --apply
# Output:
#   ✓ Baseline validation passed
#   ✓ Baseline applied to kernel successfully
#   Baseline: /sys/fs/multikernel/device_tree

# Step 3: Create kernel instances via overlays
kerf create web-server --cpus=4-7 --memory=2GB
kerf create database --cpus=8-15 --memory=8GB

# Kernel now has baseline configuration:
# - Resources defined and available for allocation
# - Instances created via overlays
# - Ready for kernel loading via 'kerf load'
```

### Workflow: Dynamic Updates

```bash
# Create new kernel instance via overlay
# Instance name is a positional argument (can appear anywhere after 'create')
kerf create web-server --cpus=4-7 --memory=2GB
# This applies an overlay adding the instance to the device tree

# Instance name can also appear after options
kerf create --cpus=8-15 --memory=8GB database

# Create with explicit CPU allocation (CPU 8)
kerf create compute --cpus=8 --memory=16GB

# Create with auto-allocated CPU count (topology-aware)
kerf create compute --cpu-count=8 --memory=16GB --numa-nodes=0 --cpu-affinity=compact --memory-policy=local

# Validate before applying (dry-run, auto-allocate 4 CPUs)
kerf create web-server --cpu-count=4 --memory=2GB --dry-run

# Update instance resources via overlay (future)
kerf update database --cpus=8-19 --memory=8GB
# This applies an overlay updating the instance configuration

# Delete instance via overlay removal (future)
kerf delete compute
# This removes the overlay transaction, reverting the change
```


## Validation Output Examples

### Successful Baseline Validation

```
$ kerf init --input=baseline.dts --apply --report

Multikernel Device Tree Validation Report
==========================================
Status: ✓ VALID

Hardware Inventory:
  CPUs: 32 total
    Host reserved: 0-3 (4 CPUs, 12%)
    Memory pool: 4-31 (28 CPUs, 88%)
  Memory: 16GB total
    Host reserved: 2GB (12%)
    Memory pool: 14GB at 0x80000000 (88%)
  Devices: 2 network, 1 storage

✓ Baseline validation passed
✓ Baseline applied to kernel successfully
  Baseline: /sys/fs/multikernel/device_tree
```

### Failed Baseline Validation

```
$ kerf init --input=bad_baseline.dts --report

Multikernel Device Tree Validation Report
==========================================
Status: ✗ INVALID

ERROR: Baseline must not contain instances. Instances should be created via overlays.
  Baseline must contain:
    ✓ /resources (hardware inventory)
    ✗ /instances (must be empty or absent)

  Suggestion: Remove instances section from baseline
  Instances should be created via 'kerf create' using overlays
  
  In file bad_baseline.dts:
    Line 45: instances { web-server { ... } }

✗ Validation failed with 1 error
Exit code: 1
```

## Error Messages and Suggestions

### Design Principles

Error messages should be:
1. **Clear**: Explain what's wrong in simple terms
2. **Actionable**: Suggest how to fix the problem
3. **Contextual**: Show relevant configuration and file locations
4. **Non-judgmental**: Help, don't blame
5. **Educational**: Help users understand multikernel constraints

### Error Message Format

```
ERROR: <Instance>: <Problem Category>
  <Detailed explanation>
  Current state: <What is currently configured>
  Conflict/Issue: <What's wrong with it>
  
  Suggestion: <Primary fix recommendation>
  Alternative: <Alternative fix if applicable>
  
  In file <filename>:
    Line <N>: <relevant source line>
```

### Detailed Error Examples

**CPU Overlap Error:**
```
ERROR: database: CPU allocation conflict with web-server
  Instance web-server uses CPUs: 4-11
  Instance database requested CPUs: 8-15
  Overlapping CPUs: 8, 9, 10, 11
  
  Suggestion: Change database to use CPUs 12-19
  Alternative: Reduce web-server allocation to CPUs 4-7
  
  In file system.dts:
    Line 35: web-server { cpus = <4 5 6 7 8 9 10 11>; }
    Line 58: database { cpus = <8 9 10 11 12 13 14 15>; }
```

**Memory Overflow Error:**
```
ERROR: compute: Memory allocation exceeds memory pool
  Memory pool range: 0x80000000 - 0x400000000 (14GB available)
  Instance compute requested: 0x400000000 - 0x500000000 (4GB)
  Overflow: Exceeds pool end by 4GB (0x100000000 bytes)
  
  Suggestion: Change memory-base to 0x300000000
  Note: This would place memory at end of memory pool
  
  In file system.dts:
    Line 78: memory-base = <0x400000000>;
    
  Context:
    Memory pool ends at: 0x400000000
    Requested start: 0x400000000 (exactly at pool end)
    Requested size: 0x100000000
    Would end at: 0x500000000 (outside pool)
```

**Device Not Found Error:**
```
ERROR: database: Invalid device reference
  Requested device: nvme1:ns1
  Available storage devices: nvme0
  Available namespaces on nvme0: ns2, ns3, ns4
  Note: Namespace ns1 is reserved for host kernel
  
  Suggestion: Change device reference to nvme0:ns2
  
  In file system.dts:
    Line 64: devices = <&nvme1_ns1>;
    
  Did you mean:
    - nvme0:ns2 (available)
    - nvme0:ns3 (available)
    - nvme0:ns4 (available)
```

**Memory Overlap Error:**
```
ERROR: database and compute: Memory region overlap detected
  database memory: 0x100000000 - 0x300000000 (8GB)
  compute memory:  0x280000000 - 0x380000000 (4GB)
  Overlapping region: 0x280000000 - 0x300000000 (2GB overlap)
  
  Suggestion: Move compute memory to 0x300000000
  Alternative: Reduce database memory size to 6GB (end at 0x280000000)
  
  In file system.dts:
    Line 62: database { memory-base = <0x100000000>; memory-bytes = <0x200000000>; }
    Line 82: compute { memory-base = <0x280000000>; memory-bytes = <0x100000000>; }
```

**Duplicate Instance Name Error:**
```
ERROR: Duplicate instance name: "web-server"
  Instance name "web-server" appears multiple times in configuration
  Found at:
    Line 28: instances { web-server { id = <1>; ... } }
    Line 95: instances { web-server { id = <4>; ... } }
  
  Suggestion: Rename the second instance to "web-server-2" or another unique name
  Note: Instance names must be unique across the entire system
```

**Misaligned Memory Error:**
```
WARNING: web-server: Memory base address not page-aligned
  Requested base: 0x80000001
  Page size: 4KB (0x1000)
  Alignment requirement: Address must be multiple of 0x1000
  
  Suggestion: Use base address 0x80000000 (already aligned)
  Note: Misaligned addresses may cause performance issues or boot failures
  
  In file system.dts:
    Line 38: memory-base = <0x80000001>;
```


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

### Example NUMA Configuration
```dts
resources {
    cpus {
        total = <32>;
        host-reserved = <0 1 2 3>;
        available = <4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 
                    20 21 22 23 24 25 26 27 28 29 30 31>;
    };
    
    topology {
        numa-nodes {
            node@0 {
                node-id = <0>;
                memory-base = <0x0 0x0>;
                memory-size = <0x0 0x800000000>;  // 16GB
                cpus = <0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15>;
            };
            
            node@1 {
                node-id = <1>;
                memory-base = <0x0 0x800000000>;
                memory-size = <0x0 0x800000000>;  // 16GB
                cpus = <16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31>;
            };
        };
    };
};

instances {
    web-server {
        resources {
            cpus = <4 5 6 7 8 9 10 11>;  // NUMA node 0
            memory-base = <0x0 0x800000000>;
            memory-bytes = <0x0 0x200000000>;  // 8GB
            numa-nodes = <0>;
            cpu-affinity = "compact";
            memory-policy = "local";
        };
    };
};
```

For detailed information about CPU and NUMA topology support, see [CPU_NUMA_TOPOLOGY.md](docs/CPU_NUMA_TOPOLOGY.md).

## References

- **Device Tree Specification**: https://devicetree-specification.readthedocs.io/
- **libfdt Documentation**: https://git.kernel.org/pub/scm/utils/dtc/dtc.git/tree/Documentation

