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
Tests for the kerf start command.
"""

import os

import click
import pytest
from click.testing import CliRunner

from kerf.cli import main
from kerf.lockdown import LockdownError
from kerf.start import main as start_main
from kerf.start.main import compose_cmdline, split_cmdline

DAXFS = 'rootfstype=daxfs init=/.kerf-init kerf.entrypoint="/bin/sleep 20" console=mktty0'
INITRAMFS = "root=/dev/vda console=mktty0"


def test_start_is_the_boot_command():
    result = CliRunner().invoke(main, ["start", "--help"])
    assert result.exit_code == 0
    assert "Boot a multikernel instance" in result.output
    assert "kerf start web-server" in result.output


def test_quoted_parameters_stay_whole():
    assert split_cmdline(DAXFS) == [
        "rootfstype=daxfs",
        "init=/.kerf-init",
        'kerf.entrypoint="/bin/sleep 20"',
        "console=mktty0",
    ]


def _compose(base, has_initrd=False, lockdown=None, deny=None, entrypoint=None, append=None):
    return compose_cmdline(base, has_initrd, lockdown, deny, entrypoint, append)


def test_entrypoint_replaces_the_loaded_one():
    cmdline, request = _compose(DAXFS, entrypoint="/bin/sh -c 'exit 4'")

    assert request is None
    assert cmdline == (
        "rootfstype=daxfs init=/.kerf-init console=mktty0 "
        "kerf.entrypoint=\"/bin/sh -c 'exit 4'\""
    )


def test_entrypoint_needs_kerf_init_to_run_it():
    with pytest.raises(click.UsageError, match="--entrypoint needs kerf-init"):
        _compose(INITRAMFS, has_initrd=True, entrypoint="/bin/sh")


def test_append_goes_after_the_loaded_command_line():
    cmdline, _ = _compose(INITRAMFS, append="loglevel=7 quiet")

    assert cmdline == f"{INITRAMFS} loglevel=7 quiet"


def test_lockdown_adds_the_level():
    cmdline, request = _compose(INITRAMFS, lockdown="integrity")

    assert cmdline == f"{INITRAMFS} lockdown=integrity"
    assert request.level == "integrity" and not request.chained


def test_deny_with_kerf_init_as_pid_one_only_names_the_reasons():
    cmdline, request = _compose(DAXFS, lockdown="integrity", deny="kcore")

    assert cmdline == f"{DAXFS} lockdown=integrity kerf.deny=kcore"
    assert not request.chained


def test_deny_on_an_initramfs_spawn_runs_kerf_init_first_even_after_append():
    cmdline, request = _compose(
        INITRAMFS, has_initrd=True, lockdown="integrity", deny="kcore", append="rdinit=/sbin/init"
    )

    assert request.chained
    assert cmdline.endswith(
        "rdinit=/sbin/init lockdown=integrity kerf.deny=kcore "
        "rdinit=/.kerf-init kerf.init=/sbin/init"
    )


def test_deny_without_kerf_init_anywhere_is_refused():
    with pytest.raises(LockdownError, match="--image, --rootfs-dir or --initrd"):
        _compose(INITRAMFS, lockdown="integrity", deny="kcore")


def test_lockdown_conflicting_with_the_loaded_command_line_is_refused():
    with pytest.raises(LockdownError, match="conflicts with lockdown=integrity"):
        _compose(f"{INITRAMFS} lockdown=integrity", lockdown="confidentiality")


class FakeInstance:
    """Stands in for kerf.instance in the start command."""

    def __init__(self, cmdline):
        self.base = cmdline
        self.started_with = []

    def open_instance(self, instance_id):
        assert instance_id == 3
        return os.open(os.devnull, os.O_RDONLY)

    def loaded_cmdline(self, fd):  # pylint: disable=unused-argument
        return self.base

    def start(self, fd, cmdline=None):  # pylint: disable=unused-argument
        self.started_with.append(cmdline)


@pytest.fixture(name="spawn")
def fixture_spawn(monkeypatch):
    fake = FakeInstance(INITRAMFS)
    saved = []
    metadata = {"kernel": {"path": "/boot/vmlinux"}, "initrd": None, "lockdown_lsm": False}
    monkeypatch.setattr(start_main, "instance", fake)
    monkeypatch.setattr(start_main, "resolve_instance", lambda name, instance_id: ("web", 3))
    monkeypatch.setattr(start_main, "load_instance_metadata", lambda instance_id: metadata)
    monkeypatch.setattr(
        start_main, "save_instance_metadata", lambda instance_id, record: saved.append(record)
    )
    fake.saved = saved
    return fake


def test_start_without_options_passes_no_command_line(spawn):
    result = CliRunner().invoke(main, ["start", "web"])

    assert result.exit_code == 0
    assert spawn.started_with == [None]
    assert spawn.saved[-1]["last_run"] == {"cmdline": None, "lockdown": None}


def test_start_with_lockdown_composes_warns_and_records(spawn):
    result = CliRunner().invoke(main, ["start", "web", "--lockdown=integrity"])

    assert result.exit_code == 0
    assert spawn.started_with == [f"{INITRAMFS} lockdown=integrity"]
    assert "does not appear to be built with the lockdown LSM" in result.output
    assert spawn.saved[-1]["last_run"]["lockdown"] == {
        "level": "integrity", "deny": [], "pinned": False,
    }


@pytest.mark.parametrize("args, message", [
    (["--deny=kcore"], "Error: --deny requires --lockdown=integrity"),
    (["--lockdown=integrity", "--deny=kcore"], "--image, --rootfs-dir or --initrd"),
    (["--lockdown=integrity", "--deny=kexec"], "--image, --rootfs-dir or --initrd"),
    (["--entrypoint=/bin/sh"], "--entrypoint needs kerf-init"),
])
def test_bad_requests_are_rejected_before_the_instance_is_started(spawn, args, message):
    result = CliRunner().invoke(main, ["start", "web", *args])

    assert result.exit_code == 2
    assert message in result.output
    assert not spawn.started_with


def test_lockdown_takes_only_the_two_kernel_levels():
    result = CliRunner().invoke(main, ["start", "web", "--lockdown=none"])

    assert result.exit_code == 2
    assert "integrity" in result.output and "confidentiality" in result.output


def test_lockdown_help_names_levels_and_reasons():
    output = CliRunner().invoke(main, ["start", "--help"]).output

    assert "--lockdown" in output and "--deny" in output
    assert "/dev/mem" in output
    for reason in ("kcore", "kprobes", "bpf-read-kernel", "dbg-read-kernel",
                   "perf", "tracefs", "xmon-rw", "xfrm-secret"):
        assert reason in output
