import struct

import pytest

from kerf.xdp import SPAWN_ENTRY, UPLINK_ENTRY, Xdp, XdpError, netdev_name, parse_virtio
from kerf.xdp.libbpf import Libbpf, LibbpfError, XDP_FLAGS_DRV_MODE, XDP_FLAGS_SKB_MODE


class FakeLib:
    """Records calls; maps are dicts keyed by fd; pins create files like bpf_obj_pin."""

    def __init__(self, native=True):
        self.calls = []
        self.maps = {}
        self.next_fd = 10
        self.native = native
        self.pinned = {}
        self.attached = {}

    def load_object(self, path, prog_names, map_names):
        self.calls.append(("load", path.split("/")[-1], tuple(prog_names)))
        progs, maps = {}, {}
        for name in prog_names:
            progs[name] = self.next_fd
            self.next_fd += 1
        for name in map_names:
            maps[name] = self.next_fd
            self.maps[self.next_fd] = {}
            self.next_fd += 1
        return progs, maps

    def close(self, fd):
        self.calls.append(("close", fd))

    def pin(self, fd, path):
        open(path, "w").close()
        self.pinned[path] = fd
        self.calls.append(("pin", path.split("/")[-1]))

    def get_pinned(self, path):
        return self.pinned[path]

    def map_update(self, fd, key, value):
        self.maps[fd][bytes(key)] = bytes(value)

    def map_delete(self, fd, key):
        self.maps[fd].pop(bytes(key), None)

    def map_lookup(self, fd, key, value_size):
        return self.maps[fd].get(bytes(key))

    def map_keys(self, fd, key_size):
        return list(self.maps[fd])

    def xdp_attach(self, ifindex, prog_fd, flags):
        # only the NIC (ifindex 3) can lack native XDP; mk_vnet always has it
        if flags == XDP_FLAGS_DRV_MODE and not self.native and ifindex == 3:
            raise LibbpfError(95, "no native xdp")
        self.attached[ifindex] = (prog_fd, flags)
        self.calls.append(("attach", ifindex, flags))

    def xdp_detach(self, ifindex, flags=0):
        self.attached.pop(ifindex, None)
        self.calls.append(("detach", ifindex))


DEVICES = {"enp9s0": (3, "52:54:00:00:00:01"),
           "mk-web-0": (5, "02:00:00:00:00:05"),
           "mk-db-0": (6, "02:00:00:00:00:06")}


def _env(tmp_path, native=True, devices=DEVICES):
    net = tmp_path / "net"
    for name, (ifindex, mac) in devices.items():
        (net / name).mkdir(parents=True)
        (net / name / "ifindex").write_text(f"{ifindex}\n")
        (net / name / "address").write_text(f"{mac}\n")
        (tmp_path / "sys/net/ipv6/conf" / name).mkdir(parents=True)
        (tmp_path / "sys/net/ipv6/conf" / name / "disable_ipv6").write_text("0\n")
    (tmp_path / "mk_xdp.o").write_bytes(b"")
    lib = FakeLib(native=native)
    ups = []
    xdp = Xdp(lib=lib, pin_root=tmp_path / "bpf", sysfs_net=net, bpf_dir=tmp_path,
              proc_sys=tmp_path / "sys", link_up=ups.append)
    return lib, xdp, ups


def test_netdev_name_follows_the_kernel():
    assert netdev_name("web", 0) == "mk-web-0"
    assert netdev_name("a-very-long-name", 2) == "mk-a-very-lo-2"


def test_parse_virtio():
    assert parse_virtio("net") == (["net"], {})
    assert parse_virtio("net:enp9s0,blk") == (["net", "blk"], {0: "enp9s0"})
    with pytest.raises(ValueError):
        parse_virtio("blk:enp9s0")


def test_attach_loads_the_nic_object_once(tmp_path):
    lib, xdp, ups = _env(tmp_path)
    assert xdp.attach("web", 0, "enp9s0") == "native"
    assert xdp.attach("db", 0, "enp9s0") == "attached"
    assert [c for c in lib.calls if c[0] == "load"] == [
        ("load", "mk_xdp.o", ("mk_xdp_nic", "mk_xdp_vnet"))]
    nic_prog = lib.pinned[str(xdp.pin_root / "nic/enp9s0/nic_prog")]
    vnet_prog = lib.pinned[str(xdp.pin_root / "nic/enp9s0/vnet_prog")]
    assert lib.attached[3] == (nic_prog, XDP_FLAGS_DRV_MODE)
    assert lib.attached[5] == (vnet_prog, XDP_FLAGS_DRV_MODE)
    assert lib.attached[6] == (vnet_prog, XDP_FLAGS_DRV_MODE)
    uplink = lib.maps[lib.pinned[str(xdp.pin_root / "nic/enp9s0/uplink")]]
    assert uplink == {struct.pack("<I", 0): struct.pack(UPLINK_ENTRY, 3, bytes.fromhex("525400000001"))}
    assert (xdp.pin_root / "vnet/mk-web-0/enp9s0").is_dir()


def test_attach_brings_the_netdev_up_and_silences_ipv6(tmp_path):
    lib, xdp, ups = _env(tmp_path)
    xdp.attach("web", 0, "enp9s0")
    assert ups == ["mk-web-0"]
    assert (tmp_path / "sys/net/ipv6/conf/mk-web-0/disable_ipv6").read_text().strip() == "1"
    assert (tmp_path / "sys/net/ipv6/conf/enp9s0/disable_ipv6").read_text().strip() == "0"


def test_generic_fallback(tmp_path):
    lib, xdp, _ = _env(tmp_path, native=False)
    assert xdp.attach("web", 0, "enp9s0") == "generic"
    assert lib.attached[3][1] == XDP_FLAGS_SKB_MODE


def test_detach_forgets_learned_addresses_and_releases_the_nic_last(tmp_path):
    lib, xdp, _ = _env(tmp_path)
    xdp.attach("web", 0, "enp9s0")
    xdp.attach("db", 0, "enp9s0")
    ips = lib.maps[lib.pinned[str(xdp.pin_root / "nic/enp9s0/spawn_ips")]]
    ips[bytes([10, 0, 0, 5])] = struct.pack(SPAWN_ENTRY, 5, bytes.fromhex("020000000005"))
    ips[bytes([10, 0, 0, 6])] = struct.pack(SPAWN_ENTRY, 6, bytes.fromhex("020000000006"))
    assert xdp.detach("web") == [("mk-web-0", "enp9s0")]
    assert 5 not in lib.attached and 3 in lib.attached
    assert list(ips) == [bytes([10, 0, 0, 6])]
    assert xdp.detach("db") == [("mk-db-0", "enp9s0")]
    assert 3 not in lib.attached
    assert not (xdp.pin_root / "nic/enp9s0").exists()


def test_seed_names_the_address_before_the_instance_speaks(tmp_path):
    lib, xdp, _ = _env(tmp_path)
    assert xdp.seed("web", 0, "10.0.0.5") is False
    xdp.attach("web", 0, "enp9s0")
    assert xdp.seed("web", 0, "10.0.0.5") is True
    ips = lib.maps[lib.pinned[str(xdp.pin_root / "nic/enp9s0/spawn_ips")]]
    assert ips == {bytes([10, 0, 0, 5]): struct.pack(SPAWN_ENTRY, 5, bytes.fromhex("020000000005"))}


def test_detach_survives_a_half_finished_attach(tmp_path):
    lib, xdp, _ = _env(tmp_path)
    (xdp.pin_root / "vnet/mk-web-0").mkdir(parents=True)
    assert xdp.detach("web") == [("mk-web-0", None)]
    assert not (xdp.pin_root / "vnet/mk-web-0").exists()


def test_detach_without_attach_is_noop(tmp_path):
    _, xdp, _ = _env(tmp_path)
    assert xdp.detach("web") == []


def test_missing_device(tmp_path):
    _, xdp, _ = _env(tmp_path)
    with pytest.raises(XdpError):
        xdp.attach("web", 0, "nosuchnic")


def test_missing_object_names_the_wheel_build(tmp_path):
    _, xdp, _ = _env(tmp_path)
    xdp.bpf_dir = tmp_path / "nowhere"
    with pytest.raises(XdpError, match="clang"):
        xdp.attach("web", 0, "enp9s0")


class _Func:
    """A ctypes-like function stub that records its calls."""

    def __init__(self, log, name, ret=0):
        self.log, self.name, self.ret = log, name, ret
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.log.append((self.name,) + tuple(a if isinstance(a, int) else type(a).__name__ for a in args))
        return self.ret


class _OldLibbpf:
    """libbpf 0.x: no bpf_xdp_attach, only bpf_set_link_xdp_fd and bpf_get_link_xdp_id."""

    def __init__(self):
        self.log = []
        for name in ("bpf_object__open_file", "bpf_object__load", "bpf_object__close",
                     "bpf_object__find_program_by_name", "bpf_program__fd",
                     "bpf_object__find_map_by_name", "bpf_map__fd", "bpf_obj_pin", "bpf_obj_get",
                     "bpf_map_update_elem", "bpf_map_delete_elem", "bpf_map_lookup_elem",
                     "bpf_map_get_next_key", "bpf_set_link_xdp_fd", "bpf_get_link_xdp_id"):
            setattr(self, name, _Func(self.log, name))


def test_binding_falls_back_to_libbpf_0x_xdp_api():
    old = _OldLibbpf()
    lib = Libbpf(lib=old)
    lib.xdp_attach(3, 7, XDP_FLAGS_DRV_MODE)
    lib.xdp_detach(3)
    assert lib.xdp_prog_id(3) == 0
    assert [c[0] for c in old.log] == ["bpf_set_link_xdp_fd", "bpf_set_link_xdp_fd", "bpf_get_link_xdp_id"]
    assert old.log[0][1:] == (3, 7, XDP_FLAGS_DRV_MODE)
    assert old.log[1][1:] == (3, -1, 0)
