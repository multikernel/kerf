# Copyright 2026 Multikernel Technologies, Inc.
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
Naming of pool PCI devices.

A pool device is identified by its PCI address and nothing else: host
names such as nvme0n1 or enp9s0 are probe-order or udev artifacts that
differ between kernels, while the address is fixed by the fabric. The
device node is named after the address, and a standard device tree
/aliases entry gives the device a stable name: the name the device has in
the kernel that owns it, which a spawned kernel then applies ("nvme0"
stays nvme0 and its namespaces nvme0n1, "enp9s0" stays enp9s0).
"""

import re
from typing import Optional, Tuple

PCI_ID_RE = re.compile(r'^([0-9a-fA-F]{1,4}):([0-9a-fA-F]{1,2}):([0-9a-fA-F]{1,2})\.([0-9a-fA-F])$')
PCI_NODE_RE = re.compile(r'^pci_([0-9a-fA-F]{1,4})_([0-9a-fA-F]{1,2})_([0-9a-fA-F]{1,2})_([0-9a-fA-F])$')
ALIAS_RE = re.compile(r'^[a-z0-9-]{1,31}$')
NVME_NAME_RE = re.compile(r'^(nvme\d+)(?:n\d+(p\d+)?)?$')
IFNAMSIZ = 16


def normalize_pci_id(text: str) -> Optional[str]:
    """Canonical "dddd:bb:ss.f" for a PCI address or the node name derived from one."""
    match = PCI_ID_RE.match(text) or PCI_NODE_RE.match(text)
    if not match:
        return None
    domain, bus, slot, func = (int(part, 16) for part in match.groups())
    return f"{domain:04x}:{bus:02x}:{slot:02x}.{func:x}"


def pci_node_name(pci_id: str) -> str:
    """Device node name for a PCI address, as the kernel derives it."""
    normalized = normalize_pci_id(pci_id)
    if normalized is None:
        raise ValueError(f"Invalid PCI ID '{pci_id}', expected DDDD:BB:SS.F")
    return "pci_" + normalized.replace(":", "_").replace(".", "_")


def split_alias(entry: str) -> Tuple[str, Optional[str]]:
    """Split a "device" or "device=alias" request into its parts."""
    name, sep, alias = entry.partition("=")
    if not sep:
        return name, None
    if not ALIAS_RE.match(alias):
        raise ValueError(
            f"Invalid alias '{alias}' for device '{name}': "
            "use 1 to 31 characters from a-z, 0-9 and '-', like nvme0 or enp9s0"
        )
    return name, alias


def is_partition(host_name: str) -> bool:
    match = NVME_NAME_RE.match(host_name)
    return bool(match and match.group(2))


def default_alias(host_name: str, compatible: Optional[str]) -> Optional[str]:
    """
    The alias a host device name implies, None when it implies nothing.

    An NVMe name carries its controller name, which the namespaces follow.
    A network interface name is the alias itself, when the kernel can
    apply it. A raw PCI address implies no name at all.
    """
    if normalize_pci_id(host_name):
        return None
    match = NVME_NAME_RE.match(host_name)
    if match:
        return match.group(1)
    if compatible == "pci-network" and ALIAS_RE.match(host_name) and len(host_name) < IFNAMSIZ:
        return host_name
    return None
