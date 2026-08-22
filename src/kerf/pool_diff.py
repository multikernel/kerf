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

"""Difference between the live resource pool and a requested baseline."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .models import GlobalDeviceTree, PoolMemoryRegion

ANY_NODE = -1


@dataclass
class PoolDiff:
    cpus_to_pool: List[int] = field(default_factory=list)
    cpus_to_host: List[int] = field(default_factory=list)
    devices_to_pool: List[str] = field(default_factory=list)
    devices_to_host: List[str] = field(default_factory=list)
    memory_to_pool: List[Tuple[int, int]] = field(default_factory=list)
    memory_to_host: List[PoolMemoryRegion] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any((self.cpus_to_pool, self.cpus_to_host, self.devices_to_pool,
                        self.devices_to_host, self.memory_to_pool, self.memory_to_host))


def _pci_ids(tree: GlobalDeviceTree) -> Set[str]:
    return {d.pci_id for d in tree.hardware.devices.values() if d.pci_id}


def _memory_diff(regions: List[PoolMemoryRegion], requested: Dict[int, int],
                  busy: Set[int], diff: PoolDiff) -> None:
    remaining = list(regions)

    for node, want in requested.items():
        if node == ANY_NODE:
            continue
        have = sum(r.size for r in remaining if r.node == node)
        if want > have:
            diff.memory_to_pool.append((node, want - have))
        elif want < have:
            _release(remaining, lambda r, node=node: r.node == node, have - want, busy, diff)

    # Chunks on nodes nobody asked for are surplus, unless an any-node request absorbs them.
    explicit = {n for n in requested if n != ANY_NODE}
    any_want = requested.get(ANY_NODE, 0)
    unclaimed = [r for r in remaining if r.node not in explicit]
    have = sum(r.size for r in unclaimed)
    if any_want > have:
        diff.memory_to_pool.append((ANY_NODE, any_want - have))
    elif any_want < have:
        _release(remaining, lambda r: r.node not in explicit, have - any_want, busy, diff)


def _release(remaining: List[PoolMemoryRegion], pred, surplus: int,
             busy: Set[int], diff: PoolDiff) -> None:
    # A chunk that still holds an allocation cannot go back to the host, and
    # asking anyway fails the whole transaction.
    candidates = sorted((r for r in remaining if pred(r) and r.base not in busy),
                         key=lambda r: -r.size)
    for r in candidates:
        if surplus <= 0:
            break
        if r.size > surplus:
            continue
        diff.memory_to_host.append(r)
        remaining.remove(r)
        surplus -= r.size


def compute_pool_diff(current: GlobalDeviceTree, requested: GlobalDeviceTree,
                       busy_chunks: Optional[Set[int]] = None) -> PoolDiff:
    """The request is the desired state; memory on nodes it omits counts as surplus."""
    diff = PoolDiff()
    cur_cpus = set(current.hardware.cpus.available)
    req_cpus = set(requested.hardware.cpus.available)
    diff.cpus_to_pool = sorted(req_cpus - cur_cpus)
    diff.cpus_to_host = sorted(cur_cpus - req_cpus)

    cur_dev, req_dev = _pci_ids(current), _pci_ids(requested)
    diff.devices_to_pool = sorted(req_dev - cur_dev)
    diff.devices_to_host = sorted(cur_dev - req_dev)

    _memory_diff(current.hardware.memory.regions, requested.hardware.memory.requested,
                 busy_chunks or set(), diff)
    return diff
