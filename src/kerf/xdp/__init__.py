"""Attach and detach the XDP fast path between a NIC and an instance's netdevs.

One BPF object serves one NIC: its NIC program demultiplexes incoming frames
to the instances' netdevs by IPv4 address, its netdev program learns each
instance's addresses and hides the instance behind the NIC's own MAC. State
lives in bpffs, not in kerf: the NIC's programs and maps are pinned under
/sys/fs/bpf/mk/nic/<nic>, and every routed netdev has a directory
/sys/fs/bpf/mk/vnet/<netdev>/<nic> (bpffs holds only pins and directories,
so the NIC's name is a directory). kerf delete reads them back.
"""
import fcntl
import shutil
import socket
import struct
from pathlib import Path

from .libbpf import Libbpf, LibbpfError, XDP_FLAGS_DRV_MODE, XDP_FLAGS_SKB_MODE

BPF_DIR = Path(__file__).parent / "bpf"
PIN_ROOT = Path("/sys/fs/bpf/mk")
SYSFS_NET = Path("/sys/class/net")
PROC_SYS = Path("/proc/sys")

SPAWN_ENTRY = "<I6s2x"		# struct mk_spawn: ifindex, mac
UPLINK_ENTRY = "<I6s2x"		# struct mk_uplink: ifindex, mac
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 1


def netdev_name(instance, index):
    """The kernel names an instance's netdev "mk-<first 9 chars>-<index>"."""
    return f"mk-{instance[:9]}-{index}"


def _u32(n):
    return struct.pack("<I", n)


def link_up(dev):
    """Set IFF_UP on a device through the flags ioctl; a redirect target must be up."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        req = struct.pack("16sH14x", dev.encode(), 0)
        flags = struct.unpack_from("H", fcntl.ioctl(sock, SIOCGIFFLAGS, req), 16)[0]
        if not flags & IFF_UP:
            fcntl.ioctl(sock, SIOCSIFFLAGS, struct.pack("16sH14x", dev.encode(), flags | IFF_UP))


class XdpError(Exception):
    pass


class Xdp:
    def __init__(self, lib=None, pin_root=PIN_ROOT, sysfs_net=SYSFS_NET, bpf_dir=BPF_DIR,
                 proc_sys=PROC_SYS, link_up=link_up):
        self.lib = lib or Libbpf()
        self.pin_root = Path(pin_root)
        self.sysfs_net = Path(sysfs_net)
        self.bpf_dir = Path(bpf_dir)
        self.proc_sys = Path(proc_sys)
        self.link_up = link_up

    def _object(self):
        path = self.bpf_dir / "mk_xdp.o"
        if not path.exists():
            raise XdpError(f"{path} is missing: kerf was installed without its XDP object; "
                           f"build the wheel on a machine with clang, or run make in {self.bpf_dir}")
        return str(path)

    def _ifindex(self, dev):
        try:
            return int((self.sysfs_net / dev / "ifindex").read_text())
        except OSError as err:
            raise XdpError(f"no network device {dev}") from err

    def _mac(self, dev):
        return bytes.fromhex((self.sysfs_net / dev / "address").read_text().strip().replace(":", ""))

    def _quiet_netdev(self, dev):
        """An instance's netdev is a redirect endpoint: up, and silent on IPv6,
        which masquerading does not carry and the host need not speak on it."""
        self.link_up(dev)
        disable = self.proc_sys / "net/ipv6/conf" / dev / "disable_ipv6"
        if disable.exists():
            disable.write_text("1\n")

    def _nic(self, nic):
        """The NIC's programs and maps, loaded and attached on first use.
        Returns (pins, mode) with mode 'native', 'generic', or 'attached'."""
        pin = self.pin_root / "nic" / nic
        if (pin / "nic_prog").exists():
            return pin, "attached"
        pin.mkdir(parents=True, exist_ok=True)
        try:
            progs, maps = self.lib.load_object(self._object(), ["mk_xdp_nic", "mk_xdp_vnet"],
                                               ["spawn_ips", "uplink"])
            ifindex = self._ifindex(nic)
            self.lib.map_update(maps["uplink"], _u32(0),
                                struct.pack(UPLINK_ENTRY, ifindex, self._mac(nic)))
            try:
                self.lib.xdp_attach(ifindex, progs["mk_xdp_nic"], XDP_FLAGS_DRV_MODE)
                mode = "native"
            except LibbpfError:
                self.lib.xdp_attach(ifindex, progs["mk_xdp_nic"], XDP_FLAGS_SKB_MODE)
                mode = "generic"
        except Exception:
            shutil.rmtree(pin, ignore_errors=True)
            raise
        pins = {"mk_xdp_nic": "nic_prog", "mk_xdp_vnet": "vnet_prog",
                "spawn_ips": "spawn_ips", "uplink": "uplink"}
        for name, fd in list(progs.items()) + list(maps.items()):
            self.lib.pin(fd, str(pin / pins[name]))
            self.lib.close(fd)
        return pin, mode

    def attach(self, instance, index, nic):
        """Route the instance's netdev through the NIC. Returns the NIC's XDP mode."""
        dev = netdev_name(instance, index)
        dev_if = self._ifindex(dev)
        nic_pin, mode = self._nic(nic)
        self._quiet_netdev(dev)

        pin = self.pin_root / "vnet" / dev
        if _routed_nic(pin) is None:
            prog_fd = self.lib.get_pinned(str(nic_pin / "vnet_prog"))
            self.lib.xdp_attach(dev_if, prog_fd, XDP_FLAGS_DRV_MODE)
            self.lib.close(prog_fd)
            (pin / nic).mkdir(parents=True, exist_ok=True)
        return mode

    def seed(self, instance, index, ip):
        """Tell the NIC program the instance's IPv4 address ahead of any traffic.
        The program learns addresses from what the instance sends, but an
        instance that has not spoken yet would otherwise be unreachable."""
        dev = netdev_name(instance, index)
        nic = _routed_nic(self.pin_root / "vnet" / dev)
        if nic is None:
            return False
        ips = self.lib.get_pinned(str(self.pin_root / "nic" / nic / "spawn_ips"))
        self.lib.map_update(ips, socket.inet_aton(ip),
                            struct.pack(SPAWN_ENTRY, self._ifindex(dev), self._mac(dev)))
        self.lib.close(ips)
        return True

    def detach(self, instance):
        """Undo attach for every netdev of the instance; a no-op when none was attached."""
        released = []
        for dev in attached_netdevs(instance, self.pin_root):
            pin = self.pin_root / "vnet" / dev
            nic = _routed_nic(pin)
            try:
                self.lib.xdp_detach(self._ifindex(dev))
            except (XdpError, LibbpfError):
                pass
            shutil.rmtree(pin, ignore_errors=True)
            if nic:
                self._release_from_nic(nic, dev)
            released.append((dev, nic))
        return released

    def _release_from_nic(self, nic, dev):
        pin = self.pin_root / "nic" / nic
        if not (pin / "nic_prog").exists():
            return
        try:
            dev_if = self._ifindex(dev)
        except XdpError:
            dev_if = None
        ips = self.lib.get_pinned(str(pin / "spawn_ips"))
        for key in self.lib.map_keys(ips, 4):
            value = self.lib.map_lookup(ips, key, struct.calcsize(SPAWN_ENTRY))
            if value is None:
                continue
            if dev_if is None or struct.unpack(SPAWN_ENTRY, value)[0] == dev_if:
                self.lib.map_delete(ips, key)
        self.lib.close(ips)
        vnet_root = self.pin_root / "vnet"
        still_routed = vnet_root.exists() and any(
            _routed_nic(p) == nic for p in vnet_root.iterdir())
        if not still_routed:
            try:
                self.lib.xdp_detach(self._ifindex(nic))
            except (XdpError, LibbpfError):
                pass
            shutil.rmtree(pin, ignore_errors=True)


def _routed_nic(vnet_pin):
    """The NIC a netdev's pin directory names, or None."""
    if not vnet_pin.exists():
        return None
    return next((p.name for p in vnet_pin.iterdir() if p.is_dir()), None)


def attached_netdevs(instance, pin_root=PIN_ROOT):
    """The instance's netdevs routed through a NIC, without touching libbpf."""
    vnet_root = Path(pin_root) / "vnet"
    if not vnet_root.exists():
        return []
    return sorted(p.name for p in vnet_root.glob(f"{netdev_name(instance, 0)[:-1]}*"))


def parse_virtio(spec):
    """"net,blk" or "net:enp9s0" into (kinds, {index: uplink})."""
    kinds, uplinks = [], {}
    for index, item in enumerate(i.strip() for i in spec.split(",") if i.strip()):
        kind, _, uplink = item.partition(":")
        kinds.append(kind)
        if uplink:
            if kind != "net":
                raise ValueError(f"only net devices take an uplink, not {kind}")
            uplinks[index] = uplink
    return kinds, uplinks
