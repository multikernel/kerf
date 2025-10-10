# CPU and NUMA Topology Support in Kerf

## Overview

Kerf now provides comprehensive support for CPU and NUMA topology management in multikernel systems. This enables optimal resource allocation based on hardware topology, ensuring that kernel instances are placed on appropriate CPUs and memory regions for maximum performance.

## Key Features

### 1. CPU Topology Awareness
- **Socket identification**: Track which socket each CPU belongs to
- **Core mapping**: Understand CPU core relationships and SMT/hyperthreading
- **Cache hierarchy**: Model CPU cache levels and sizes
- **NUMA node association**: Map CPUs to their NUMA nodes

### 2. NUMA Topology Support
- **NUMA node definition**: Specify memory regions and CPU assignments per NUMA node
- **Distance matrix**: Model NUMA node distances for optimal placement
- **Memory types**: Support different memory types (DRAM, HBM, CXL)
- **Memory locality**: Ensure memory and CPU allocations are co-located

### 3. Topology-Aware Allocation Policies
- **CPU affinity**: `compact`, `spread`, `local` policies for CPU placement
- **Memory policy**: `local`, `interleave`, `bind` policies for memory allocation
- **NUMA constraints**: Specify preferred NUMA nodes for instances
- **Performance optimization**: Automatic validation of topology constraints

## Device Tree Format

### Basic NUMA Topology

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
```

### Advanced CPU Topology

```dts
resources {
    cpus {
        total = <32>;
        host-reserved = <0 1 2 3>;
        available = <4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 
                    20 21 22 23 24 25 26 27 28 29 30 31>;
        
        // CPU core topology (SMT/Hyperthreading)
        cores {
            // Socket 0, NUMA node 0 cores
            core@0 { cpus = <0 1>; };    // SMT siblings
            core@1 { cpus = <2 3>; };
            core@2 { cpus = <4 5>; };
            core@3 { cpus = <6 7>; };
            // ... more cores ...
        };
    };
    
    topology {
        numa-nodes {
            node@0 {
                node-id = <0>;
                memory-base = <0x0 0x0>;
                memory-size = <0x0 0x800000000>;
                cpus = <0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15>;
            };
            
            node@1 {
                node-id = <1>;
                memory-base = <0x0 0x800000000>;
                memory-size = <0x0 0x800000000>;
                cpus = <16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31>;
            };
        };
    };
};
```

### Instance Topology Configuration

```dts
instances {
    web-server {
        id = <1>;
        resources {
            cpus = <4 5 6 7 8 9 10 11>;  // NUMA node 0
            memory-base = <0x0 0x800000000>;
            memory-bytes = <0x0 0x200000000>;  // 8GB
            numa-nodes = <0>;                    // Preferred NUMA nodes
            cpu-affinity = "compact";           // CPU placement policy
            memory-policy = "local";            // Memory allocation policy
            devices = <&eth0_vf1>;
        };
    };
    
    database {
        id = <2>;
        resources {src/kerf/dtc/__pycache__/
            cpus = <20 21 22 23 24 25 26 27>;  // NUMA node 1
            memory-base = <0x0 0x800000000>;
            memory-bytes = <0x0 0x400000000>;  // 16GB
            numa-nodes = <1>;
            cpu-affinity = "compact";
            memory-policy = "local";
            devices = <&eth1_vf1>;
        };
    };
    
    compute {
        id = <3>;
        resources {
            cpus = <12 13 14 15 16 17 18 19 28 29 30 31>;  // Cross-NUMA
            memory-base = <0x0 0xA00000000>;
            memory-bytes = <0x0 0x200000000>;  // 8GB
            numa-nodes = <0 1>;                 // Multiple NUMA nodes
            cpu-affinity = "spread";            // Spread across NUMA nodes
            memory-policy = "interleave";       // Interleave memory allocation
        };
    };
};
```

## CPU Affinity Policies

### `compact`
- **Purpose**: Minimize NUMA node crossings and maximize cache locality
- **Behavior**: Allocates CPUs from the same NUMA node and preferably the same core
- **Use case**: High-performance, latency-sensitive workloads
- **Example**: Database workloads, real-time applications

### `spread`
- **Purpose**: Distribute workload across multiple NUMA nodes
- **Behavior**: Allocates CPUs from different NUMA nodes
- **Use case**: Throughput-oriented workloads that can benefit from parallel processing
- **Example**: Batch processing, analytics workloads

### `local`
- **Purpose**: Co-locate CPUs and memory on the same NUMA node
- **Behavior**: Ensures CPUs and memory are from the same NUMA node
- **Use case**: Memory-intensive workloads requiring low latency access
- **Example**: In-memory databases, high-performance computing

## Memory Policies

### `local`
- **Purpose**: Allocate memory from the same NUMA node as CPUs
- **Behavior**: Ensures memory is local to the CPU cores
- **Use case**: Performance-critical applications
- **Benefits**: Lowest memory access latency

### `interleave`
- **Purpose**: Distribute memory allocation across multiple NUMA nodes
- **Behavior**: Memory is allocated from different NUMA nodes
- **Use case**: Large memory allocations that exceed single NUMA node capacity
- **Benefits**: Higher total memory bandwidth

### `bind`
- **Purpose**: Bind memory allocation to specific NUMA nodes
- **Behavior**: Memory is allocated only from specified NUMA nodes
- **Use case**: Workloads with specific memory requirements
- **Benefits**: Predictable memory placement

## Validation and Error Detection

Kerf automatically validates topology constraints and provides detailed error messages:

### NUMA Constraint Validation
```
ERROR: Instance database: NUMA node 3 does not exist in hardware topology
WARNING: Instance web-server: CPU 8 is in NUMA node 1, but instance is configured for NUMA nodes [0]. This may cause performance issues due to remote memory access.
```

### CPU Affinity Validation
```
WARNING: Instance compute: Compact CPU affinity requested but CPUs span multiple NUMA nodes: [0, 1]
WARNING: Instance analytics: Spread CPU affinity requested but CPUs are from single NUMA node 0
```

### Memory Policy Validation
```
WARNING: Instance database: Local memory policy requested but CPUs are from NUMA nodes [1] while memory is on NUMA node 0
```

## Performance Considerations

### NUMA Locality
- **Local access**: Memory access within the same NUMA node (fastest)
- **Remote access**: Memory access across NUMA nodes (slower)
- **Cross-socket access**: Memory access across different sockets (slowest)

### CPU Placement Strategies
1. **Compact placement**: Best for single-threaded or small multi-threaded workloads
2. **Spread placement**: Best for large multi-threaded workloads
3. **Local placement**: Best for memory-intensive workloads

### Memory Allocation Strategies
1. **Local memory**: Fastest access, limited by NUMA node capacity
2. **Interleaved memory**: Higher bandwidth, higher latency
3. **Bound memory**: Predictable placement, may limit flexibility

## Best Practices

### 1. Workload Analysis
- **CPU-bound**: Use `compact` affinity with `local` memory policy
- **Memory-bound**: Use `local` affinity with `local` memory policy
- **I/O-bound**: Use `spread` affinity with `interleave` memory policy

### 2. Resource Planning
- **Small instances**: Prefer single NUMA node allocation
- **Large instances**: Consider multi-NUMA node allocation
- **Critical instances**: Use `local` policies for best performance

### 3. Topology Awareness
- **Understand your hardware**: Know your NUMA topology before configuration
- **Test configurations**: Validate performance with different topology settings
- **Monitor utilization**: Use system tools to verify optimal placement

## Example Configurations

### High-Performance Database
```dts
database {
    resources {
        cpus = <4 5 6 7 8 9 10 11>;  // Single NUMA node
        memory-base = <0x0 0x800000000>;
        memory-bytes = <0x0 0x400000000>;  // 16GB
        numa-nodes = <0>;
        cpu-affinity = "compact";
        memory-policy = "local";
    };
};
```

### Distributed Analytics
```dts
analytics {
    resources {
        cpus = <12 13 14 15 16 17 18 19 28 29 30 31>;  // Cross-NUMA
        memory-base = <0x0 0xA00000000>;
        memory-bytes = <0x0 0x800000000>;  // 32GB
        numa-nodes = <0 1>;
        cpu-affinity = "spread";
        memory-policy = "interleave";
    };
};
```

### Real-Time Processing
```dts
realtime {
    resources {
        cpus = <20 21 22 23>;  // Single core, single NUMA node
        memory-base = <0x0 0x1000000000>;
        memory-bytes = <0x0 0x200000000>;  // 8GB
        numa-nodes = <1>;
        cpu-affinity = "compact";
        memory-policy = "local";
    };
};
```

## Troubleshooting

### Common Issues

1. **NUMA node mismatch**: CPUs and memory on different NUMA nodes
   - **Solution**: Use `local` affinity and memory policy
   - **Check**: Verify NUMA node assignments in configuration

2. **Performance degradation**: Remote memory access
   - **Solution**: Ensure memory and CPUs are co-located
   - **Check**: Use `numactl` to verify actual NUMA placement

3. **Resource conflicts**: Multiple instances on same NUMA node
   - **Solution**: Distribute instances across NUMA nodes
   - **Check**: Monitor NUMA node utilization

### Debugging Commands

```bash
# Check NUMA topology
numactl --hardware

# Check CPU topology
lscpu

# Check memory allocation
numactl --show

# Monitor NUMA statistics
cat /proc/vmstat | grep numa
```

## Future Enhancements

### Planned Features
- **Dynamic topology discovery**: Automatic hardware topology detection
- **Performance profiling**: Integration with performance monitoring tools
- **Advanced policies**: More sophisticated allocation algorithms
- **Migration support**: Runtime topology-aware instance migration

### Research Areas
- **Machine learning**: AI-driven topology optimization
- **Workload characterization**: Automatic policy selection based on workload patterns
- **Energy efficiency**: Power-aware topology management
- **Heterogeneous systems**: Support for different CPU types and memory hierarchies
