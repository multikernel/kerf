"""The few libbpf entry points kerf needs, through ctypes.

libbpf.so.1 is the library bpftool and iproute2 use; kerf calls it
directly so that loading, pinning, and attaching need no external tool.
"""
import ctypes
import ctypes.util
import os

XDP_FLAGS_UPDATE_IF_NOEXIST = 1 << 0
XDP_FLAGS_SKB_MODE = 1 << 1
XDP_FLAGS_DRV_MODE = 1 << 2


class LibbpfError(OSError):
    pass


class Libbpf:
    """One loaded libbpf; every method raises LibbpfError with errno on failure.

    Works with libbpf 1.x and 0.x: the XDP attach entry points were renamed
    in 1.0, so the older spellings are used when the newer ones are absent.
    """

    def __init__(self, lib=None):
        if lib is None:
            name = ctypes.util.find_library("bpf")
            if not name:
                raise LibbpfError(0, "libbpf not found; install libbpf")
            lib = ctypes.CDLL(name, use_errno=True)
        self._lib = lib
        self._modern_xdp = hasattr(lib, "bpf_xdp_attach")
        lib.bpf_object__open_file.restype = ctypes.c_void_p
        lib.bpf_object__open_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        lib.bpf_object__load.argtypes = [ctypes.c_void_p]
        lib.bpf_object__close.argtypes = [ctypes.c_void_p]
        lib.bpf_object__find_program_by_name.restype = ctypes.c_void_p
        lib.bpf_object__find_program_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.bpf_program__fd.argtypes = [ctypes.c_void_p]
        lib.bpf_object__find_map_by_name.restype = ctypes.c_void_p
        lib.bpf_object__find_map_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.bpf_map__fd.argtypes = [ctypes.c_void_p]
        lib.bpf_obj_pin.argtypes = [ctypes.c_int, ctypes.c_char_p]
        lib.bpf_obj_get.argtypes = [ctypes.c_char_p]
        lib.bpf_map_update_elem.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_uint64]
        lib.bpf_map_delete_elem.argtypes = [ctypes.c_int, ctypes.c_void_p]
        lib.bpf_map_lookup_elem.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        lib.bpf_map_get_next_key.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        if self._modern_xdp:
            lib.bpf_xdp_attach.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
                                           ctypes.c_void_p]
            lib.bpf_xdp_detach.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p]
            lib.bpf_xdp_query_id.argtypes = [ctypes.c_int, ctypes.c_int,
                                             ctypes.POINTER(ctypes.c_uint32)]
        else:
            lib.bpf_set_link_xdp_fd.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
            lib.bpf_get_link_xdp_id.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint32),
                                                ctypes.c_uint32]

    @staticmethod
    def _check(ret, what):
        if ret < 0:
            raise LibbpfError(-ret, f"{what}: {os.strerror(-ret)}")
        return ret

    def load_object(self, path, prog_names, map_names):
        """Load an object; return ({program name: fd}, {map name: fd}). The
        programs and maps stay alive through the fds, so the object is closed."""
        obj = self._lib.bpf_object__open_file(path.encode(), None)
        if not obj:
            raise LibbpfError(ctypes.get_errno(), f"open {path}")
        try:
            self._check(self._lib.bpf_object__load(obj), f"load {path}")
            progs = {}
            for name in prog_names:
                prog = self._lib.bpf_object__find_program_by_name(obj, name.encode())
                if not prog:
                    raise LibbpfError(0, f"{path}: no program {name}")
                progs[name] = os.dup(self._check(self._lib.bpf_program__fd(prog), f"{name} fd"))
            maps = {}
            for name in map_names:
                m = self._lib.bpf_object__find_map_by_name(obj, name.encode())
                if not m:
                    raise LibbpfError(0, f"{path}: no map {name}")
                maps[name] = os.dup(self._check(self._lib.bpf_map__fd(m), f"map {name} fd"))
            return progs, maps
        finally:
            self._lib.bpf_object__close(obj)

    @staticmethod
    def close(fd):
        os.close(fd)

    def pin(self, fd, path):
        self._check(self._lib.bpf_obj_pin(fd, path.encode()), f"pin {path}")

    def get_pinned(self, path):
        return self._check(self._lib.bpf_obj_get(path.encode()), f"open pinned {path}")

    def map_update(self, fd, key, value):
        self._check(self._lib.bpf_map_update_elem(fd, key, value, 0), "map update")

    def map_delete(self, fd, key):
        self._check(self._lib.bpf_map_delete_elem(fd, key), "map delete")

    def map_lookup(self, fd, key, value_size):
        value = ctypes.create_string_buffer(value_size)
        ret = self._lib.bpf_map_lookup_elem(fd, key, value)
        if ret < 0:
            return None
        return value.raw

    def map_keys(self, fd, key_size):
        keys = []
        prev = None
        while True:
            nxt = ctypes.create_string_buffer(key_size)
            if self._lib.bpf_map_get_next_key(fd, prev, nxt) < 0:
                return keys
            keys.append(nxt.raw)
            prev = nxt

    def xdp_attach(self, ifindex, prog_fd, flags):
        if self._modern_xdp:
            ret = self._lib.bpf_xdp_attach(ifindex, prog_fd, flags, None)
        else:
            ret = self._lib.bpf_set_link_xdp_fd(ifindex, prog_fd, flags)
        self._check(ret, "xdp attach")

    def xdp_detach(self, ifindex, flags=0):
        if self._modern_xdp:
            ret = self._lib.bpf_xdp_detach(ifindex, flags, None)
        else:
            ret = self._lib.bpf_set_link_xdp_fd(ifindex, -1, flags)
        self._check(ret, "xdp detach")

    def xdp_prog_id(self, ifindex):
        prog_id = ctypes.c_uint32(0)
        if self._modern_xdp:
            ret = self._lib.bpf_xdp_query_id(ifindex, 0, ctypes.byref(prog_id))
        else:
            ret = self._lib.bpf_get_link_xdp_id(ifindex, ctypes.byref(prog_id), 0)
        self._check(ret, "xdp query")
        return prog_id.value
