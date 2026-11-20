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
Drive kerf-init's deny list resolution and program emission
(src/init/lockdown-test) over synthetic BTF.
"""

import subprocess
from pathlib import Path

import pytest

from tests.btf_blob import lockdown_blob

LOCKDOWN_TEST = Path(__file__).resolve().parents[1] / "src" / "init" / "lockdown-test"

pytestmark = pytest.mark.skipif(
    not LOCKDOWN_TEST.exists(), reason="lockdown-test not built, run make"
)

GUARD = [
    "guard 79 2 1 8 0", "guard b7 0 0 0 0", "guard 57 2 0 0 2", "guard 15 2 0 4 0",
    "guard 79 1 1 0 0", "guard 61 1 1 2404 0", "guard 55 1 0 1 1",
    "guard b7 0 0 0 -1", "guard 95 0 0 0 0",
]


def deny_program(low, high):
    return [
        "deny 79 2 1 0 0", "deny b7 0 0 0 0", "deny 25 2 0 6 63",
        f"deny 18 3 0 0 {low}", f"deny 00 0 0 0 {high}", "deny 7f 3 2 0 0",
        "deny 57 3 0 0 1", "deny 15 3 0 1 0", "deny b7 0 0 0 -1", "deny 95 0 0 0 0",
    ]


@pytest.fixture(name="run")
def fixture_run(tmp_path):
    def run(blob, names):
        path = tmp_path / "btf"
        path.write_bytes(blob)
        proc = subprocess.run([str(LOCKDOWN_TEST), "plan", str(path), names],
                              capture_output=True, text=True, timeout=10, check=False)
        return proc.returncode, proc.stdout, proc.stderr

    return run


def test_plan_and_programs(run):
    code, out, err = run(lockdown_blob(), "kcore,xfrm-secret")
    mask = (1 << 21) | (1 << 28)

    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"mask={mask:x}", "locked_down_id=1", "ptrace_id=2", "tgid_off=2404",
        *deny_program(mask, 0), *GUARD,
    ]


def test_mask_above_32_bits_splits_across_the_two_immediates(run):
    reasons = (("LOCKDOWN_INTEGRITY_MAX", 20), ("LOCKDOWN_PERF", 40),
               ("LOCKDOWN_CONFIDENTIALITY_MAX", 41))
    code, out, _ = run(lockdown_blob(reasons), "perf")

    assert code == 0
    assert out.splitlines()[0] == f"mask={1 << 40:x}"
    assert out.splitlines()[4:14] == deny_program(0, 256)


def test_bit_31_of_the_mask_stays_in_the_low_immediate(run):
    reasons = (("LOCKDOWN_INTEGRITY_MAX", 20), ("LOCKDOWN_PERF", 31),
               ("LOCKDOWN_CONFIDENTIALITY_MAX", 41))
    code, out, _ = run(lockdown_blob(reasons), "perf")

    assert code == 0
    assert out.splitlines()[4:14] == deny_program(-(1 << 31), 0)


def test_duplicate_names_set_one_bit(run):
    assert run(lockdown_blob(), "perf,perf")[1].splitlines()[0] == f"mask={1 << 25:x}"


def test_tgid_offset_goes_into_the_guard(run):
    out = run(lockdown_blob(tgid_byte=1516), "kcore")[1].splitlines()

    assert out[3] == "tgid_off=1516"
    assert out[19] == "guard 61 1 1 1516 0"


@pytest.mark.parametrize("names, message", [
    ("", "kerf.deny= is empty"),
    ("kcore,,perf", "malformed name"),
    ("kcore,", "malformed name"),
    (",kcore", "malformed name"),
    ("KCORE", "malformed name"),
    ("kcore perf", "malformed name"),
    ("x" * 49, "malformed name"),
    ("nosuch", "kernel has no lockdown reason 'nosuch'"),
    ("x" * 48, "kernel has no lockdown reason"),
    ("kexec", "'kexec' is not a confidentiality reason on this kernel"),
    ("none", "'none' is not a confidentiality reason on this kernel"),
    ("integrity-max", "is not a confidentiality reason"),
    ("confidentiality-max", "is not a confidentiality reason"),
])
def test_refused_deny_lists(run, names, message):
    code, out, err = run(lockdown_blob(), names)

    assert (code, out) == (1, "")
    assert err.startswith("error: ") and message in err


@pytest.mark.parametrize("drop, message", [
    ("bpf_lsm_locked_down", "kernel has no BPF LSM (CONFIG_BPF_LSM)"),
    ("bpf_lsm_ptrace_access_check", "kernel has no BPF LSM (CONFIG_BPF_LSM)"),
    ("lockdown_reason", "kernel BTF lacks enum lockdown_reason"),
    ("LOCKDOWN_INTEGRITY_MAX", "kernel BTF lacks enum lockdown_reason"),
    ("LOCKDOWN_CONFIDENTIALITY_MAX", "kernel BTF lacks enum lockdown_reason"),
    ("tgid", "kernel BTF lacks task_struct.tgid"),
])
def test_kernels_that_cannot_do_it(run, drop, message):
    code, out, err = run(lockdown_blob(drop=drop), "kcore")

    assert (code, out) == (1, "") and message in err


def test_reason_at_or_above_64_cannot_be_encoded(run):
    reasons = (("LOCKDOWN_INTEGRITY_MAX", 60), ("LOCKDOWN_KCORE", 64),
               ("LOCKDOWN_CONFIDENTIALITY_MAX", 70))
    code, _, err = run(lockdown_blob(reasons), "kcore")

    assert code == 1 and "lockdown reason 'kcore' cannot be encoded" in err


def test_reason_63_is_the_last_that_fits(run):
    reasons = (("LOCKDOWN_INTEGRITY_MAX", 60), ("LOCKDOWN_KCORE", 63),
               ("LOCKDOWN_CONFIDENTIALITY_MAX", 70))
    code, out, _ = run(lockdown_blob(reasons), "kcore")

    assert code == 0 and out.splitlines()[0] == f"mask={1 << 63:x}"


def test_tgid_too_far_for_an_instruction_offset(run):
    code, _, err = run(lockdown_blob(tgid_byte=40000), "kcore")

    assert code == 1 and "task_struct.tgid" in err
