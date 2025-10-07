# Kerf: Multikernel Management System

## Overview

`kerf` is a comprehensive multikernel management system designed to orchestrate and manage multiple kernel instances on a single host. Starting with advanced device tree compilation and validation, `kerf` provides the foundation for complete multikernel lifecycle management.

Unlike standard tools that only perform basic format conversion, `kerf` understands multikernel semantics and **always validates** resource allocations, detects conflicts, and extracts instance-specific device trees from global configurations. The system is architected to evolve into a complete multikernel runtime environment.

## Vision & Roadmap

### Current Phase: Device Tree Foundation
`kerf` currently provides the essential device tree compilation and validation capabilities needed for multikernel systems:

1. **Resource Conflict Detection**: Multiple instances might accidentally be allocated the same CPUs, overlapping memory regions, or the same devices
2. **Over-Allocation**: The sum of all allocations might exceed available resources
3. **Invalid References**: Instances might reference non-existent hardware or devices reserved for the host
4. **Atomicity**: All allocations should be validated together before deployment
5. **Instance Extraction**: Each spawned kernel needs a device tree showing only its allocated resources, not the entire system

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

1. **Single Source of Truth**: One global DTS describes the entire system - hardware inventory, host reservations, and all spawn kernel allocations
2. **Multiple Binary Outputs**: kerf compiles one DTS into multiple DTB files (global + per-instance)
3. **Mandatory Validation**: Every operation validates the configuration - validation is not optional
4. **Fail-Fast**: Catch resource conflicts immediately, never produce invalid output
5. **Instance Isolation**: Generate minimal, instance-specific device trees that show only what each kernel should see
6. **Extensible Architecture**: Designed to support future kernel loading, execution, and management capabilities
7. **Developer-Friendly**: Clear error messages with suggestions for fixing problems
8. **Runtime-Ready**: Current design anticipates future kernel execution and lifecycle management needs

### Compilation Model

```
Input: Single Global DTS
         │
         ▼
    ┌─────────┐
    │ kerf    │ ← Always validates
    │ Compiler│
    └─────────┘
         │
         ├──────────────┬──────────────┬──────────────┐
         ▼              ▼              ▼              ▼
    global.dtb   instance1.dtb   instance2.dtb   instance3.dtb
    (complete)   (minimal)       (minimal)       (minimal)
```

**Key Points:**
- **One DTS input** describing entire system
- **Multiple DTB outputs** (1 global + N instance-specific)
- **Validation happens once** during compilation
- **All outputs are pre-validated** and guaranteed consistent

## Current Capabilities

### Device Tree Compilation & Validation
- **Advanced Validation**: Comprehensive resource conflict detection and validation
- **Instance Extraction**: Generate minimal, instance-specific device trees
- **Format Support**: DTS to DTB compilation with multiple output formats
- **Error Reporting**: Detailed error messages with actionable suggestions
- **Resource Analysis**: Complete resource utilization reporting

### Command Line Interface
```bash
# Compile and validate system configuration
kerf dtc --input=system.dts --output-dir=build/

# Extract specific kernel instance
kerf dtc --input=global.dtb --extract=web-server --output=web-server.dtb

# Generate validation reports
kerf dtc --input=system.dts --report --verbose
```

### Modular Architecture
The `kerf` system is designed with a modular architecture that supports incremental development:

- **`kerf dtc`**: Device tree compilation and validation (current)
- **`kerf load`**: Kernel loading (future)
- **`kerf exec`**: Kernel execuation (future)
- **`kerf update`**: Update a kernel instawnce (future)
- **`kerf kill`**: Kill a kernel instance (future)

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

The global device tree contains three main sections that map directly to the sysfs hierarchy:

1. **Resources** (`/resources`): Complete description of all physical resources
2. **Instances** (`/instances`): Resource assignments for each spawn kernel
3. **Device References**: Linkage between instances and hardware devices

### Complete Example

```dts
/dts-v1/;

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

### Mapping to Sysfs Hierarchy

**Device Tree Structure → Sysfs Hierarchy:**

```
DTS: /resources/cpus                →  /sys/kernel/multikernel/resources/cpus/
DTS: /resources/memory              →  /sys/kernel/multikernel/resources/memory/
DTS: /resources/devices             →  /sys/kernel/multikernel/resources/devices/
DTS: /instances/web-server           →  /sys/kernel/multikernel/instances/web-server/
DTS: /instances/database             →  /sys/kernel/multikernel/instances/database/
```

**Name-based addressing:**
- Instance node name in DTS (`web-server`) = directory name in sysfs (`/instances/web-server/`)
- Kernel assigns numeric IDs, but users reference by name
- No manual ID coordination needed

## Validation Rules

### Validation is Mandatory

**All `kerf` operations perform validation automatically:**
- Compiling DTS to DTB → validates
- Extracting an instance → validates first
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

## Instance Extraction

### Purpose

From a global device tree containing all system information, `kerf` extracts a minimal device tree for each spawn kernel instance that contains:
- Only the CPUs allocated to that instance
- Only the memory region assigned to that instance
- Only the devices accessible by that instance
- Configuration and build hints specific to that instance

### Extraction Process

1. **Validate Global DTS/DTB**: Mandatory validation before extraction
2. **Locate Instance Node**: Find `/instances/{name}` node
3. **Extract Resources**: Read CPU, memory, and device allocations
4. **Build Minimal DTB**: Create new device tree with:
   - `/chosen` node with resource assignments
   - Device nodes for allocated hardware only
   - Configuration hints from instance config
5. **Generate Binary**: Output instance-specific DTB file

### Instance DTB Structure

**Example: web-server.dtb (extracted from global)**

```dts
/dts-v1/;

/ {
    compatible = "linux,multikernel-instance";
    
    chosen {
        // Resource assignments for this instance only
        linux,multikernel-cpus = <4 5 6 7>;
        linux,multikernel-memory-base = <0x80000000>;
        linux,multikernel-memory-size = <0x80000000>;
        linux,multikernel-instance-id = <1>;
        linux,multikernel-instance-name = "web-server";
    };
    
    // Only devices allocated to this instance
    ethernet@0 {
        compatible = "intel,i40e";
        reg = <0x0 0x1000>;
        vf-id = <1>;  // This is VF1 assigned to web-server
    };
    
};
```

**Key characteristics:**
- Contains **only** resources for this instance
- No knowledge of other instances
- No global hardware inventory
- Minimal and focused

## Command-Line Interface

### Basic Commands

```bash
# Compile DTS to global DTB (validates automatically)
kerf dtc --input=system.dts --output=global.dtb

# Compile DTS to global DTB and extract all instances
kerf dtc --input=system.dts --output-dir=build/
# Generates:
#   build/global.dtb
#   build/web-server.dtb
#   build/database.dtb
#   build/compute.dtb

# Extract single instance from global DTB
kerf dtc --input=global.dtb --extract=web-server --output=web-server.dtb

# Extract all instances from global DTB
kerf dtc --input=global.dtb --extract-all --output-dir=instances/

# Generate allocation report
kerf dtc --input=global.dtb --report

# Convert DTB back to DTS
kerf dtc --input=global.dtb --output=global.dts --format=dts
```

### Advanced Commands

```bash
# Verbose output (shows validation details)
kerf dtc --input=system.dts --output=global.dtb --verbose

# Dry-run: validate and show what would be generated
kerf dtc --input=system.dts --dry-run

# Extract specific instance by name (not ID)
kerf dtc --input=global.dtb --extract=database --output=db.dtb

# List all instances in global DTB
kerf dtc --input=global.dtb --list-instances
# Output:
# web-server (ID: 1)
# database (ID: 2)
# compute (ID: 3)
```

### Output Formats

```bash
# Human-readable text (default)
kerf dtc --input=global.dtb --report

# JSON for tooling integration
kerf dtc --input=global.dtb --report --format=json

# YAML for configuration management
kerf dtc --input=global.dtb --report --format=yaml

# DTS (human-readable device tree source)
kerf dtc --input=global.dtb --output=global.dts --format=dts
```

## Integration with Kernel

### Sysfs Interface

The kernel exposes a sysfs interface that mirrors the device tree structure:

```
/sys/kernel/multikernel/
├── device_tree              # Read/Write: Global DTB
├── device_tree_source       # Read-only: Global DTS (human-readable)
│
└── instances/               # Auto-generated from /instances in DTB
    ├── web-server/         # Directory name from DTS node name
    │   ├── id              # Read-only: "1"
    │   ├── device_tree     # Read-only: Instance-specific DTB
    │   ├── device_tree_source  # Read-only: Instance DTS
    │   ├── status          # Read-only: "ready", "active", "stopped"
    │
    ├── database/
    │   └── ...
    │
    └── compute/
        └── ...
```

### Workflow: Initial Setup

```bash
# Step 1: Write global DTS describing entire system
vim system.dts

# Step 2: Compile and validate with kerf dtc
kerf dtc --input=system.dts --output-dir=build/
# Output:
#   ✓ Validation passed
#   Generated: build/global.dtb
#   Generated: build/web-server.dtb
#   Generated: build/database.dtb
#   Generated: build/compute.dtb

# Step 3: Upload global DTB to kernel
cat build/global.dtb > /sys/kernel/multikernel/device_tree

# Kernel automatically:
# - Validates global DTB (defense-in-depth)
# - Creates /sys/kernel/multikernel/instances/{web-server,database,compute}/
# - Generates instance-specific DTBs internally
# - Populates /sys/kernel/multikernel/resources/

# Step 4: Verify instance creation
ls /sys/kernel/multikernel/instances/
# web-server  database  compute

# Step 5: View instance configuration
cat /sys/kernel/multikernel/instances/web-server/device_tree_source
# Shows instance-specific DTS (only web-server resources)

# Step 6: Load kernel with instance DTB (use kerf dtc generated or kernel generated)
kexec_file_load(/boot/vmlinuz, build/web-server.dtb, KEXEC_MULTIKERNEL | KEXEC_MK_ID(1))
# Or use kernel-generated:
# kexec_file_load(/boot/vmlinuz, /sys/kernel/multikernel/instances/web-server/device_tree, ...)
```

### Workflow: Dynamic Updates

```bash
# Step 1: Modify global DTS
vim system.dts
# Example: Change database CPUs from 8-15 to 8-19 (add 4 CPUs)

# Step 2: Recompile and validate
kerf dtc --input=system.dts --output-dir=build/
# Output:
#   ✓ Validation passed
#   Generated: build/global.dtb (updated)
#   Generated: build/database.dtb (updated - now has CPUs 8-19)
#   (other instances unchanged)

# Step 3: Upload updated global DTB
cat build/global.dtb > /sys/kernel/multikernel/device_tree

# Kernel automatically:
# - Validates new global DTB
# - Calculates resource deltas for all instances
# - Phase 1: Releases resources being removed
# - Phase 2: Allocates new resources
# - Updates instance DTBs in sysfs
# - Notifies spawned kernels via shared memory/interrupts

# Step 4: Verify update
cat /sys/kernel/multikernel/instances/database/device_tree_source | grep cpus
# linux,multikernel-cpus = <8 9 10 11 12 13 14 15 16 17 18 19>;
```


## Validation Output Examples

### Successful Validation

```
$ kerf dtc --input=system.dts --output-dir=build/

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

Instance Allocations:
  web-server (ID: 1):
    CPUs: 4-7 (4 CPUs, 14% of pool)
    Memory: 2GB at 0x80000000 (14% of pool)
    Devices: eth0:vf1
    Status: ✓ Valid
    
  database (ID: 2):
    CPUs: 8-15 (8 CPUs, 29% of pool)
    Memory: 8GB at 0x100000000 (57% of pool)
    Devices: eth0:vf2, nvme0:ns2
    Status: ✓ Valid
    
  compute (ID: 3):
    CPUs: 16-23 (8 CPUs, 29% of pool)
    Memory: 4GB at 0x300000000 (29% of pool)
    Devices: none
    Status: ✓ Valid

Resource Utilization:
  CPUs: 20/28 allocated (71%), 8 free
  Memory: 14/14 GB allocated (100%), 0 free
  Network: 2/7 VFs allocated (29%)
  Storage: 1/3 namespaces allocated (33%)
  
✓ All validations passed

Generated output:
  build/global.dtb (3847 bytes)
  build/web-server.dtb (1024 bytes)
  build/database.dtb (1536 bytes)
  build/compute.dtb (896 bytes)
```

### Failed Validation

```
$ kerf dtc --input=bad_system.dts --output=global.dtb

Multikernel Device Tree Validation Report
==========================================
Status: ✗ INVALID

ERROR: Instance database: CPU allocation conflict with web-server
  web-server uses CPUs: 4-11
  database requested CPUs: 8-15
  Overlapping CPUs: 8, 9, 10, 11
  
  Suggestion: Change database to use CPUs 12-19
  Alternative: Reduce web-server to CPUs 4-7
  
  In file system.dts:
    Line 45: web-server { cpus = <4 5 6 7 8 9 10 11>; }
    Line 68: database { cpus = <8 9 10 11 12 13 14 15>; }

ERROR: Instance compute: Memory allocation exceeds memory pool
  Memory pool: 0x80000000 - 0x400000000 (14GB available)
  compute requested: 0x400000000 - 0x500000000 (4GB)
  Exceeds pool end by: 4GB
  
  Suggestion: Change memory-base to 0x300000000
  Note: Requires reducing other instance allocations
  
  In file system.dts:
    Line 89: memory-base = <0x400000000>;

WARNING: Resource utilization
  12 CPUs (43% of memory pool) remain unallocated
  Consider: Allocate remaining CPUs or reduce total instances

✗ Validation failed with 2 errors, 1 warning
No output generated - fix errors first

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
# From PyPI (future)
pip install kerf

# From source (development)
git clone https://github.com/multikernel/kerf.git
cd kerf
pip install -e ".[dev,cli,formats]"

# System packages (future)
apt install kerf              # Debian/Ubuntu
dnf install kerf              # Fedora/RHEL
pacman -S kerf                # Arch Linux
```

### Getting Started

```bash
# Install in development mode
pip install -e .

# Test the installation
kerf --help
kerf dtc --help

# Try with example configuration
kerf dtc --input=examples/system.dts --output-dir=build/
```

## Examples

The `examples/` directory contains sample Device Tree Source (DTS) files demonstrating various multikernel configurations:

- **`system.dts`** - Complete multikernel system with web server, database, and compute instances (32 CPUs, 16GB memory)
- **`minimal.dts`** - Simple configuration for testing and development (8 CPUs, 8GB memory)
- **`high_performance.dts`** - Large-scale configuration for high-performance computing (128 CPUs, 64GB memory)
- **`edge_computing.dts`** - Edge computing configuration with GPU support for AI inference (16 CPUs, 32GB memory)
- **`conflict_example.dts`** - Intentionally invalid configuration demonstrating common validation errors
- **`bad_system.dts`** - Another example of invalid configuration for testing error detection

## References

- **Device Tree Specification**: https://devicetree-specification.readthedocs.io/
- **libfdt Documentation**: https://git.kernel.org/pub/scm/utils/dtc/dtc.git/tree/Documentation

