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


class _FakeUdevDevice:
    def __init__(self, sys_name, sys_path=None, parent=None, subsystem=None):
        self.sys_name = sys_name
        self.sys_path = sys_path
        self.subsystem = subsystem
        self._parent = parent

    def find_parent(self, subsystem):
        node = self._parent
        while node is not None:
            if node.subsystem == subsystem:
                return node
            node = node._parent
        return None


def test_block_device_resolves_to_the_nearest_pci_ancestor(tmp_path, monkeypatch):
    """A disk behind a root port must resolve to the endpoint, not the port."""
    import types
    from kerf.init import main as init_main

    endpoint_path = tmp_path / "0000:50:00.0"
    endpoint_path.mkdir()
    (endpoint_path / "vendor").write_text("0x144d\n")
    (endpoint_path / "device").write_text("0xa80a\n")
    (endpoint_path / "class").write_text("0x010802\n")

    root_port = _FakeUdevDevice("0000:4f:01.0", subsystem="pci")
    endpoint = _FakeUdevDevice("0000:50:00.0", sys_path=str(endpoint_path),
                               parent=root_port, subsystem="pci")
    controller = _FakeUdevDevice("nvme0", parent=endpoint, subsystem="nvme")
    disk = _FakeUdevDevice("nvme0n1", parent=controller, subsystem="block")
    partition = _FakeUdevDevice("nvme0n1p1", parent=disk, subsystem="block")

    class NotFound(Exception):
        pass

    def from_name(_context, subsystem, name):
        if subsystem == "block" and name == "nvme0n1p1":
            return partition
        raise NotFound(name)

    fake_pyudev = types.SimpleNamespace(
        Context=lambda: types.SimpleNamespace(list_devices=lambda **kw: iter(())),
        Devices=types.SimpleNamespace(from_name=from_name, from_path=lambda *a: None),
        DeviceNotFoundError=NotFound,
    )
    monkeypatch.setattr(init_main, "pyudev", fake_pyudev)

    info = init_main.detect_pci_device("nvme0n1p1")
    assert info is not None
    assert info.pci_id == "0000:50:00.0"
    assert info.compatible == "pci-storage"
    assert info.vendor_id == 0x144D
