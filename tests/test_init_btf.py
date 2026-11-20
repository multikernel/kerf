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
Drive kerf-init's BTF walker (src/init/lockdown-test) over synthetic blobs.
"""

import struct
import subprocess
from pathlib import Path

import pytest

from tests.btf_blob import (
    ARRAY,
    FUNC_PROTO,
    INT,
    build,
    enum,
    enum64,
    full_layout,
    func,
    raw,
    struct_,
    zeroed,
)

LOCKDOWN_TEST = Path(__file__).resolve().parents[1] / "src" / "init" / "lockdown-test"

pytestmark = pytest.mark.skipif(
    not LOCKDOWN_TEST.exists(), reason="lockdown-test not built, run make"
)

TGID = 2404
REASONS = [("LOCKDOWN_NONE", 0), ("LOCKDOWN_KCORE", 21)]
MEMBERS = [("pid", (TGID - 4) * 8), ("tgid", TGID * 8)]


def base(reasons=None, task=None):
    """Seven types: the hook functions are ids 2 and 7."""
    return [
        zeroed(INT),
        func("bpf_lsm_locked_down"),
        reasons or enum("lockdown_reason", REASONS),
        zeroed(ARRAY),
        task or struct_("task_struct", MEMBERS),
        zeroed(FUNC_PROTO, 2),
        func("bpf_lsm_ptrace_access_check"),
    ]


def output(locked_down=2, ptrace=7, tgid=TGID, kcore="21"):
    return (f"bpf_lsm_locked_down={locked_down}\n"
            f"bpf_lsm_ptrace_access_check={ptrace}\n"
            f"task_struct.tgid={tgid}\nLOCKDOWN_KCORE={kcore}\n")


@pytest.fixture(name="run")
def fixture_run(tmp_path):
    def run(blob):
        path = tmp_path / "btf"
        path.write_bytes(blob)
        proc = subprocess.run([str(LOCKDOWN_TEST), "btf", str(path)],
                              capture_output=True, text=True, timeout=10, check=False)
        return proc.returncode, proc.stdout, proc.stderr

    return run


def test_lookups(run):
    assert run(build(base())) == (0, output(), "")


def test_every_known_kind_keeps_ids_aligned(run):
    ahead = [zeroed(kind, 3) for kind in range(1, 20)]

    assert run(build(ahead + base())) == (0, output(locked_down=21, ptrace=26), "")


def test_header_longer_than_this_walker_knows(run):
    assert run(build(base(), hdr_pad=16)) == (0, output(), "")


def test_layout_section_agreeing_with_the_built_in_sizes(run):
    assert run(build(base(), layout=full_layout())) == (0, output(), "")


def test_layout_section_sizes_a_kind_the_walker_does_not_know(run):
    newer = raw(20, 2, b"\xff" * 12)
    blob = build([newer] + base(), layout=full_layout([(4, 4), (0, 4), (0, 12)]))

    assert run(blob) == (0, output(locked_down=3, ptrace=8), "")


def test_unknown_kind_without_a_layout_section_is_refused(run):
    code, out, err = run(build([raw(20, 2, b"\0" * 12)] + base()))

    assert (code, out) == (1, "") and "error:" in err


def test_unknown_kind_beyond_the_layout_section_is_refused(run):
    code, out, err = run(build([raw(23, 0)] + base(), layout=full_layout([(4, 4)])))

    assert (code, out) == (1, "") and "error:" in err


def test_layout_section_that_disagrees_on_a_kind_the_walker_reads(run):
    layout = full_layout()
    layout[6] = (0, 16)
    code, _, err = run(build(base(), layout=layout))

    assert code == 1 and "error:" in err


def test_absent_names(run):
    assert run(build([zeroed(INT)])) == (0, output(0, 0, -1, "absent"), "")


def test_names_are_matched_whole(run):
    types = [func("bpf_lsm_locked_down_too"), func("bpf_lsm_locked"),
             enum("lockdown_reasons", REASONS), struct_("task_struct_x", MEMBERS)]

    assert run(build(types)) == (0, output(0, 0, -1, "absent"), "")


def test_forward_declarations_are_skipped(run):
    types = [enum("lockdown_reason", []), struct_("task_struct", [])] + base()

    assert run(build(types)) == (0, output(locked_down=4, ptrace=9), "")


def test_enum64_values(run):
    assert run(build(base(reasons=enum64("lockdown_reason", REASONS)))) == (0, output(), "")


def test_kflag_struct_uses_the_low_24_bits(run):
    task = struct_("task_struct", MEMBERS, kflag=1)

    assert run(build(base(task=task))) == (0, output(), "")


def test_bitfield_member_has_no_byte_offset(run):
    task = struct_("task_struct", [("tgid", (3 << 24) | (TGID * 8))], kflag=1)

    assert run(build(base(task=task))) == (0, output(tgid=-1), "")


def test_member_that_is_not_byte_aligned(run):
    task = struct_("task_struct", [("tgid", TGID * 8 + 3)])

    assert run(build(base(task=task))) == (0, output(tgid=-1), "")


def damaged(blob, damage):
    if damage == "empty":
        return b""
    if damage == "truncated_header":
        return blob[:20]
    if damage == "truncated_types":
        # type_len cut in the middle of the last record
        type_len = struct.unpack_from("<I", blob, 12)[0]
        return blob[:12] + struct.pack("<I", type_len - 5) + blob[16:]
    if damage == "truncated_file":
        return blob[:-3]
    if damage == "big_endian":
        return struct.pack(">H", 0xEB9F) + blob[2:]
    if damage == "bad_magic":
        return b"\x7fE" + blob[2:]
    if damage == "bad_hdr_len":
        return blob[:4] + struct.pack("<I", 8) + blob[8:]
    if damage == "name_past_strings":
        return blob[:24] + struct.pack("<I", 1 << 20) + blob[28:]
    if damage == "strings_not_terminated":
        return blob[:-1] + b"x"
    raise ValueError(damage)


@pytest.mark.parametrize("damage", [
    "empty", "truncated_header", "truncated_types", "truncated_file", "big_endian",
    "bad_magic", "bad_hdr_len", "name_past_strings", "strings_not_terminated",
])
def test_malformed_blobs_are_refused(run, damage):
    code, out, err = run(damaged(build(base()), damage))

    assert (code, out) == (1, "") and "error:" in err


def test_missing_file_is_refused(tmp_path):
    proc = subprocess.run([str(LOCKDOWN_TEST), "btf", str(tmp_path / "nope")],
                          capture_output=True, text=True, timeout=10, check=False)

    assert proc.returncode == 1 and "error:" in proc.stderr
