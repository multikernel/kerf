"""Pool device naming: addresses identify, aliases name."""

import pytest

from kerf.devices import default_alias, is_partition, normalize_pci_id, pci_node_name, split_alias
from kerf.models import DeviceInfo, HardwareInventory, CPUAllocation, MemoryAllocation


@pytest.mark.parametrize("text, expected", [
    ("0000:4f:01.0", "0000:4f:01.0"),
    ("0000:4F:01.0", "0000:4f:01.0"),
    ("0:9:0.0", "0000:09:00.0"),
    ("pci_0000_4f_01_0", "0000:4f:01.0"),
    ("nvme0", None),
    ("enp9s0", None),
    ("0000:4f:01", None),
])
def test_normalize_pci_id(text, expected):
    assert normalize_pci_id(text) == expected


def test_pci_node_name_matches_the_kernel_derivation():
    assert pci_node_name("0000:4f:01.0") == "pci_0000_4f_01_0"
    assert pci_node_name("0000:4F:01.0") == "pci_0000_4f_01_0"
    with pytest.raises(ValueError):
        pci_node_name("nvme0")


@pytest.mark.parametrize("entry, expected", [
    ("nvme0n1", ("nvme0n1", None)),
    ("enp9s0=ethernet0", ("enp9s0", "ethernet0")),
    ("0000:4f:01.0=nvme1", ("0000:4f:01.0", "nvme1")),
])
def test_split_alias(entry, expected):
    assert split_alias(entry) == expected


@pytest.mark.parametrize("entry", ["enp9s0=Ethernet0", "enp9s0=", "nvme0n1=nvme_0",
                                   "x=" + "a" * 32])
def test_split_alias_rejects_names_outside_the_spec(entry):
    with pytest.raises(ValueError):
        split_alias(entry)


@pytest.mark.parametrize("host_name, compatible, expected", [
    ("nvme0n1", "pci-storage", "nvme0"),
    ("nvme3n1p1", "pci-storage", "nvme3"),
    ("nvme12", "pci-storage", "nvme12"),
    ("enp9s0", "pci-network", "enp9s0"),
    ("eth0", "pci-network", "eth0"),
    ("enp129s0f0np0", "pci-network", "enp129s0f0np0"),
    ("a-name-too-long-for-an-interface", "pci-network", None),
    ("Bond_0", "pci-network", None),
    ("0000:4f:01.0", "pci-storage", None),
    ("sda", "pci-storage", None),
])
def test_default_alias(host_name, compatible, expected):
    assert default_alias(host_name, compatible) == expected


def test_is_partition():
    assert is_partition("nvme0n1p1")
    assert not is_partition("nvme0n1")
    assert not is_partition("nvme0")
    assert not is_partition("enp9s0")


def _inventory(**devices):
    return HardwareInventory(
        cpus=CPUAllocation(total=4, host_reserved=[0], available=[1, 2, 3]),
        memory=MemoryAllocation(total_bytes=1 << 30, host_reserved_bytes=0),
        devices=devices,
    )


def test_find_device_by_node_name_alias_or_address():
    inventory = _inventory(
        pci_0000_4f_01_0=DeviceInfo(name="pci_0000_4f_01_0", compatible="pci-storage",
                                    device_type="pci", pci_id="0000:4f:01.0", alias="nvme0"),
        serial_console=DeviceInfo(name="serial_console", compatible="ns16550",
                                  device_type="platform", device_name="serial8250"),
    )
    for ref in ("pci_0000_4f_01_0", "nvme0", "0000:4f:01.0", "0000:4F:01.0"):
        assert inventory.find_device(ref) == "pci_0000_4f_01_0"
    assert inventory.find_device("serial_console") == "serial_console"
    assert inventory.find_device("nvme1") is None
    assert inventory.find_device("0000:4f:01.1") is None
    assert _inventory().find_device("nvme0") is None
