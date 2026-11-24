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

"""Validation of a spawn lockdown request, and the kernel image check."""

import os

import pytest

from kerf.lockdown import (
    CONFIDENTIALITY_REASONS,
    INTEGRITY_REASONS,
    LockdownError,
    image_supports_lockdown,
    lockdown_help,
    resolve_lockdown,
)

MARKER = b"Kernel is locked down from %s; see man kernel_lockdown.7\n"


def req(level="integrity", deny=None, cmdline=None, kerf_init=True, initrd=False):
    return resolve_lockdown(level, deny, cmdline, kerf_init, initrd)


def test_nothing_requested():
    assert resolve_lockdown(None, None, "lockdown=integrity", False, True) is None


def test_reason_lists():
    assert CONFIDENTIALITY_REASONS == (
        "kcore", "kprobes", "bpf-read-kernel", "dbg-read-kernel",
        "perf", "tracefs", "xmon-rw", "xfrm-secret")
    assert len(INTEGRITY_REASONS) == 20
    assert not set(INTEGRITY_REASONS) & set(CONFIDENTIALITY_REASONS)


@pytest.mark.parametrize("cmdline", [None, "", "root=/dev/sda1 quiet"])
def test_level_is_appended(cmdline):
    r = req(cmdline=cmdline, kerf_init=False)
    assert (r.level, r.deny, r.params, r.warnings) == (
        "integrity", (), ("lockdown=integrity",), ())


def test_level_not_repeated_when_already_set():
    assert not req(cmdline="quiet lockdown=integrity").params


@pytest.mark.parametrize("cmdline", [
    "lockdown=confidentiality", "lockdown=integrity lockdown=bogus", "lockdown="])
def test_a_different_level_on_the_command_line_is_a_conflict(cmdline):
    with pytest.raises(LockdownError, match=r"--lockdown=integrity conflicts with lockdown="):
        req(cmdline=cmdline)


@pytest.mark.parametrize("cmdline", ["foo.lockdown=confidentiality", "nolockdown=1"])
def test_only_the_lockdown_parameter_itself_counts(cmdline):
    assert req(cmdline=cmdline).params == ("lockdown=integrity",)


def test_deny_is_passed_by_name():
    r = req(deny="kcore,xfrm-secret")
    assert r.deny == ("kcore", "xfrm-secret")
    assert r.params == ("lockdown=integrity", "kerf.deny=kcore,xfrm-secret")


def test_deny_is_deduplicated_in_order():
    assert req(deny="perf,kcore,perf").deny == ("perf", "kcore")


@pytest.mark.parametrize("deny", ["kcore,,perf", "kcore,", ",kcore", "", "kcore, perf",
                                  "KCORE", "xfrm_secret"])
def test_malformed_deny_names(deny):
    with pytest.raises(LockdownError, match="malformed"):
        req(deny=deny)


def test_integrity_reasons_are_not_selectable():
    with pytest.raises(LockdownError, match=r"kexec.*already covered by --lockdown=integrity"):
        req(deny="kcore,kexec")


def test_unknown_reason_is_passed_through_with_a_warning():
    r = req(deny="kcore,future-thing")
    assert r.deny == ("kcore", "future-thing")
    assert len(r.warnings) == 1 and "future-thing" in r.warnings[0]


@pytest.mark.parametrize("kwargs, message", [
    ({"level": None, "deny": "kcore"}, "--deny requires --lockdown=integrity"),
    ({"level": "confidentiality", "deny": "kcore"}, "already denies"),
    ({"deny": "kcore", "kerf_init": False}, "--image, --rootfs-dir or --initrd"),
    ({"deny": "kcore", "cmdline": "kerf.init=/sbin/init"}, "kerf.init= is reserved"),
    ({"deny": "kcore", "kerf_init": False, "initrd": True, "cmdline": "rdinit=bin/sh"},
     "cannot run rdinit=bin/sh"),
    ({"deny": "kcore", "cmdline": "kerf.deny=perf"}, "kerf.deny= is reserved"),
    ({"cmdline": "kerf.deny=perf"}, "kerf.deny= is reserved"),
    ({"cmdline": "lsm=landlock,bpf"}, "lsm=landlock,bpf .*leaves out lockdown"),
    ({"deny": "kcore", "cmdline": "lsm=landlock,lockdown"}, "leaves out bpf"),
    ({"deny": "kcore", "cmdline": "lsm=lockdown,bpf lsm=lockdown"}, "lsm=lockdown in .*leaves out bpf"),
])
def test_rejected_requests(kwargs, message):
    with pytest.raises(LockdownError, match=message):
        req(**kwargs)


def test_lsm_list_that_keeps_both_is_fine():
    r = req(deny="kcore", cmdline="lsm=landlock lsm=landlock,lockdown,bpf")
    assert r.params == ("lockdown=integrity", "kerf.deny=kcore")


def test_deny_with_only_an_initrd_runs_kerf_init_ahead_of_its_init():
    r = req(deny="kcore", kerf_init=False, initrd=True)

    assert r.chained
    assert r.params == ("lockdown=integrity", "kerf.deny=kcore",
                        "rdinit=/.kerf-init", "kerf.init=/init")
    assert len(r.warnings) == 1 and "root in the spawn" in r.warnings[0]


def test_chained_init_is_the_last_rdinit_of_the_command_line():
    r = req(deny="kcore", kerf_init=False, initrd=True,
            cmdline="rdinit=/bin/sh quiet rdinit=/sbin/init")

    assert r.params[-2:] == ("rdinit=/.kerf-init", "kerf.init=/sbin/init")


def test_an_initrd_ahead_of_kerf_init_is_trusted_and_said_so():
    r = req(deny="kcore", initrd=True)

    assert not r.chained
    assert r.params == ("lockdown=integrity", "kerf.deny=kcore")
    assert len(r.warnings) == 1 and "initrd runs before" in r.warnings[0]


def test_level_without_deny_never_chains():
    assert not req(kerf_init=False, initrd=True).chained


def test_level_without_deny_works_with_any_init_and_an_initrd():
    assert req(level="confidentiality", kerf_init=False, initrd=True).params == (
        "lockdown=confidentiality",)


def _image(tmp_path, data):
    path = tmp_path / "vmlinux"
    path.write_bytes(data)
    return os.open(path, os.O_RDONLY)


def test_image_with_the_lockdown_message(tmp_path):
    fd = _image(tmp_path, b"\x7fELF" + b"\0" * 8192 + MARKER)
    try:
        assert image_supports_lockdown(fd)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
    finally:
        os.close(fd)


def test_image_without_the_lockdown_message(tmp_path):
    fd = _image(tmp_path, b"\x7fELF" + b"\0" * 8192)
    try:
        assert not image_supports_lockdown(fd)
    finally:
        os.close(fd)


def test_empty_image_is_not_an_error(tmp_path):
    fd = _image(tmp_path, b"")
    try:
        assert not image_supports_lockdown(fd)
    finally:
        os.close(fd)


def test_works_on_a_memfd():
    fd = os.memfd_create("kerf-test")
    try:
        os.write(fd, b"\x7fELF" + MARKER)
        os.lseek(fd, 0, os.SEEK_SET)
        assert image_supports_lockdown(fd)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
    finally:
        os.close(fd)


def test_help_lists_every_lockdown_feature():
    text = lockdown_help()
    for name in INTEGRITY_REASONS + CONFIDENTIALITY_REASONS:
        assert f"  {name} " in text
    assert text.index("module-signature") < text.index("kcore")
