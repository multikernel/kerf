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
Tests for the instance descriptor: the layout the kernel expects, and the
commands that drive it.
"""

import ctypes
import errno
import os

import pytest
from click.testing import CliRunner

from kerf import instance
from kerf.cli import main


def test_ioctl_numbers_match_the_kernel_header():
    # _IOW(0xB9, 1, 24 bytes), _IOW(0xB9, 2, 8), _IOWR(0xB9, 3, 32), _IOWR(0xB9, 5, 16)
    assert instance.KEXEC_MK_IOC_START == 0x4018B901
    assert instance.KEXEC_MK_IOC_HALT == 0x4008B902
    assert instance.KEXEC_MK_IOC_INFO == 0xC020B903
    assert instance.KEXEC_MK_IOC_CMDLINE == 0xC010B905


def test_instance_id_goes_into_the_upper_flag_bits():
    assert instance.KEXEC_FILE_MK_ID(1) == 0x00010000
    assert instance.KEXEC_FILE_MK_ID(511) == 0x01FF0000


class FakeKernel:
    """Stands in for kexec_file_load() and the ioctls of the descriptor."""

    def __init__(self, state=instance.KEXEC_MK_STATE_LOADED, cmdline="console=mktty0"):
        self.state = state
        self.loaded_cmdline = cmdline
        self.calls = []
        self.fail = {}
        self.fds = []

    def kexec_file_load(self, kernel_fd, initrd_fd, cmdline, flags):
        self.calls.append(("kexec_file_load", kernel_fd, initrd_fd, cmdline, flags))
        if flags & instance.KEXEC_FILE_UNLOAD:
            return 0
        # A real descriptor, since the commands close what they are given
        self.fds.append(os.open(os.devnull, os.O_RDONLY))
        return self.fds[-1]

    def ioctl(self, fd, request, arg):
        assert fd in self.fds
        if request in self.fail:
            raise OSError(self.fail[request], "refused")
        if request == instance.KEXEC_MK_IOC_START:
            _, timeout_ms, ptr, length, _ = instance.KEXEC_MK_START.unpack(arg)
            cmdline = ctypes.string_at(ptr, length) if ptr else None
            self.calls.append(("start", timeout_ms, cmdline))
        elif request == instance.KEXEC_MK_IOC_HALT:
            self.calls.append(("halt",) + instance.KEXEC_MK_HALT.unpack(arg))
        elif request == instance.KEXEC_MK_IOC_INFO:
            assert instance.KEXEC_MK_INFO.unpack(arg)[0] == instance.KEXEC_MK_INFO.size
            arg[:] = instance.KEXEC_MK_INFO.pack(instance.KEXEC_MK_INFO.size, self.state, 0, 0, 0, 0, 0)
        elif request == instance.KEXEC_MK_IOC_CMDLINE:
            ptr, length, _ = instance.KEXEC_MK_CMDLINE.unpack(arg)
            data = self.loaded_cmdline.encode() + b"\0"
            assert length >= len(data)
            ctypes.memmove(ptr, data, len(data))
        else:
            raise AssertionError(f"unexpected ioctl {request:#x}")
        return 0


@pytest.fixture(name="fd")
def fixture_fd(kernel):
    fd = instance.open_instance(3)
    kernel.calls.clear()
    yield fd
    os.close(fd)


@pytest.fixture(name="kernel")
def fixture_kernel(monkeypatch):
    fake = FakeKernel()
    # arm64 adds a parameter of its own to every command line
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr(instance, "kexec_file_load", fake.kexec_file_load)
    monkeypatch.setattr(instance.fcntl, "ioctl", fake.ioctl)
    for module in ("kerf.utils", "kerf.start.main", "kerf.kill.main"):
        monkeypatch.setattr(
            f"{module}.resolve_instance", lambda name, instance_id: ("web", 3), raising=False
        )
    return fake


def test_open_asks_for_the_instance_and_loads_nothing(kernel):
    fd = instance.open_instance(3)
    os.close(fd)

    assert fd == kernel.fds[0]
    flags = instance.KEXEC_FILE_MULTIKERNEL | instance.KEXEC_FILE_MK_OPEN | 0x00030000
    assert kernel.calls == [("kexec_file_load", -1, -1, None, flags)]


def test_load_without_an_initrd_says_so(kernel):
    os.close(instance.load_image(3, 7, -1, "quiet"))

    flags = kernel.calls[0][4]
    assert flags & instance.KEXEC_FILE_NO_INITRAMFS
    assert flags & instance.KEXEC_FILE_MULTIKERNEL
    assert not flags & instance.KEXEC_FILE_MK_OPEN


def test_start_without_a_command_line_passes_none(kernel, fd):
    instance.start(fd)

    assert kernel.calls == [("start", 0, None)]


def test_start_passes_the_command_line_with_its_terminator(kernel, fd):
    instance.start(fd, "console=mktty0 quiet")

    assert kernel.calls == [("start", 0, b"console=mktty0 quiet\0")]


def test_info_reports_the_state(kernel, fd):
    kernel.state = instance.KEXEC_MK_STATE_ACTIVE

    state = instance.info(fd)

    assert state.running and state.loaded


def test_a_ready_instance_is_neither_loaded_nor_running(kernel, fd):
    kernel.state = instance.KEXEC_MK_STATE_READY

    state = instance.info(fd)

    assert not state.running and not state.loaded


def test_cmdline_reads_what_the_image_was_loaded_with(fd):
    assert instance.loaded_cmdline(fd) == "console=mktty0"


def test_kerf_start_boots_the_instance(kernel):
    result = CliRunner().invoke(main, ["start", "web"])

    assert result.exit_code == 0
    assert ("start", 0, b"console=mktty0\0") in kernel.calls


def test_kerf_start_explains_a_missing_image(kernel):
    kernel.fail[instance.KEXEC_MK_IOC_START] = errno.ENOEXEC

    result = CliRunner().invoke(main, ["start", "web"])

    assert result.exit_code == 1
    assert "kerf load web" in result.output


def test_kerf_kill_asks_and_waits(kernel):
    kernel.state = instance.KEXEC_MK_STATE_ACTIVE

    result = CliRunner().invoke(main, ["kill", "web"])

    assert result.exit_code == 0
    assert ("halt", 0, 30000) in kernel.calls


def test_kerf_kill_refuses_an_instance_that_is_not_running(kernel):
    result = CliRunner().invoke(main, ["kill", "web"])

    assert result.exit_code == 1
    assert "is not running" in result.output
    assert not [call for call in kernel.calls if call[0] == "halt"]


def test_kerf_kill_force_reruns_on_a_stopped_instance(kernel):
    result = CliRunner().invoke(main, ["kill", "--force", "web"])

    assert result.exit_code == 0
    assert ("halt", instance.KEXEC_MK_HALT_FORCE, 30000) in kernel.calls


def test_kerf_kill_points_at_force_after_a_timeout(kernel):
    kernel.state = instance.KEXEC_MK_STATE_ACTIVE
    kernel.fail[instance.KEXEC_MK_IOC_HALT] = errno.ETIMEDOUT

    result = CliRunner().invoke(main, ["kill", "web"])

    assert result.exit_code == 1
    assert "--force" in result.output
