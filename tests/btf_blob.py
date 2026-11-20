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

"""Build small BTF blobs for the kerf-init tests."""

import struct

(INT, PTR, ARRAY, STRUCT, UNION, ENUM, FWD, TYPEDEF, VOLATILE, CONST, RESTRICT,
 FUNC, FUNC_PROTO, VAR, DATASEC, FLOAT, DECL_TAG, TYPE_TAG, ENUM64) = range(1, 20)

BTF_MAGIC = 0xEB9F

# (info_sz, elem_sz) of the bytes after struct btf_type, per kind
KIND_LAYOUT = {
    INT: (4, 0), PTR: (0, 0), ARRAY: (12, 0), STRUCT: (0, 12), UNION: (0, 12),
    ENUM: (0, 8), FWD: (0, 0), TYPEDEF: (0, 0), VOLATILE: (0, 0), CONST: (0, 0),
    RESTRICT: (0, 0), FUNC: (0, 0), FUNC_PROTO: (0, 8), VAR: (4, 0),
    DATASEC: (0, 12), FLOAT: (0, 0), DECL_TAG: (4, 0), TYPE_TAG: (0, 0),
    ENUM64: (0, 12),
}


class Name(str):
    """A string in a record's trailing data, written as its string offset."""


def raw(kind, vlen=0, trailing=b"", name="", kflag=0):
    """One type record; trailing is bytes, or a list of bytes and Name items."""
    parts = [trailing] if isinstance(trailing, bytes) else list(trailing)
    return {"kind": kind, "vlen": vlen, "name": name, "kflag": kflag, "parts": parts}


def zeroed(kind, vlen=0):
    """A record of any known kind with all-zero trailing data."""
    info_sz, elem_sz = KIND_LAYOUT[kind]
    return raw(kind, vlen, b"\0" * (info_sz + vlen * elem_sz))


def func(name):
    return raw(FUNC, name=name)


def enum(name, members):
    parts = []
    for member, value in members:
        parts += [Name(member), struct.pack("<i", value)]
    return raw(ENUM, len(members), parts, name)


def enum64(name, members):
    parts = []
    for member, value in members:
        parts += [Name(member), struct.pack("<II", value & 0xFFFFFFFF, value >> 32)]
    return raw(ENUM64, len(members), parts, name)


def struct_(name, members, kflag=0):
    """A struct; members are (name, offset word) pairs, offsets in bits."""
    parts = []
    for member, offset in members:
        parts += [Name(member), struct.pack("<II", 0, offset)]
    return raw(STRUCT, len(members), parts, name, kflag)


def build(types, layout=None, hdr_pad=0):
    """
    Serialize type records, ids 1..n in order, into a BTF blob.

    layout, a list of (info_sz, elem_sz) indexed by kind, adds the layout
    section newer kernels have. hdr_pad grows the header by that many bytes.
    """
    strings = bytearray(b"\0")
    offsets = {"": 0}

    def name_off(name):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b"\0")
        return offsets[name]

    type_data = bytearray()
    for rec in types:
        info = (rec["kflag"] << 31) | (rec["kind"] << 24) | rec["vlen"]
        type_data += struct.pack("<III", name_off(rec["name"]), info, 0)
        for part in rec["parts"]:
            if isinstance(part, Name):
                type_data += struct.pack("<I", name_off(part))
            else:
                type_data += part

    layout_data = b"".join(struct.pack("<BBH", info_sz, elem_sz, 0)
                           for info_sz, elem_sz in layout or [])
    hdr_len = (24 if layout is None else 32) + hdr_pad
    header = struct.pack("<HBBIIIII", BTF_MAGIC, 1, 0, hdr_len,
                         0, len(type_data), len(type_data), len(strings))
    if layout is not None:
        header += struct.pack("<II", len(type_data) + len(strings), len(layout_data))
    return header + b"\0" * hdr_pad + bytes(type_data) + bytes(strings) + layout_data


def full_layout(extra=()):
    """A layout section for kinds 0..19, then the given extra kinds."""
    return [(0, 0)] + [KIND_LAYOUT[kind] for kind in range(1, 20)] + list(extra)


LOCKDOWN_REASONS = (
    ("LOCKDOWN_NONE", 0),
    ("LOCKDOWN_KEXEC", 4),
    ("LOCKDOWN_INTEGRITY_MAX", 20),
    ("LOCKDOWN_KCORE", 21),
    ("LOCKDOWN_PERF", 25),
    ("LOCKDOWN_XFRM_SECRET", 28),
    ("LOCKDOWN_CONFIDENTIALITY_MAX", 29),
)


def lockdown_blob(reasons=LOCKDOWN_REASONS, drop=None, tgid_byte=2404):
    """
    The BTF kerf-init needs for a deny list, hook functions as ids 1 and 2.

    drop leaves out one thing by name: a function, the enum, an enumerator
    or the tgid member.
    """
    types = []
    for hook in ("bpf_lsm_locked_down", "bpf_lsm_ptrace_access_check"):
        types.append(func(hook) if drop != hook else zeroed(PTR))
    if drop != "lockdown_reason":
        types.append(enum("lockdown_reason", [r for r in reasons if r[0] != drop]))
    members = [("pid", (tgid_byte - 4) * 8), ("tgid", tgid_byte * 8)]
    types.append(struct_("task_struct", [m for m in members if m[0] != drop]))
    return build(types)
