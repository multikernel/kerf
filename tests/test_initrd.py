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

"""Adding kerf-init to a spawn's own initramfs."""

import os

import pytest

from kerf import initrd
from kerf.initrd import InitrdError, kerf_init_archive, open_initrd_with_kerf_init

HEADER_LEN = 110


def entries(archive):
    """The (name, mode, data) of each entry, read the way the kernel does."""
    found = []
    offset = 0
    while offset < len(archive):
        assert archive[offset:offset + 6] == b"070701"
        fields = [int(archive[offset + 6 + 8 * i:offset + 14 + 8 * i], 16) for i in range(13)]
        mode, size, name_len = fields[1], fields[6], fields[11]
        name_end = offset + HEADER_LEN + name_len
        name = archive[offset + HEADER_LEN:name_end - 1].decode()
        data_start = name_end + -name_end % 4
        found.append((name, mode, archive[data_start:data_start + size]))
        offset = data_start + size + -size % 4
    return found


@pytest.fixture(name="binary")
def fixture_binary(tmp_path, monkeypatch):
    path = tmp_path / "kerf-init"
    path.write_bytes(b"\x7fELF-not-a-multiple-of-four")
    monkeypatch.setattr(initrd, "get_init_binary_path", lambda: path)
    return path


def test_archive_holds_kerf_init_as_an_executable(binary):
    assert entries(kerf_init_archive()) == [
        (".kerf-init", 0o100755, binary.read_bytes()),
        ("TRAILER!!!", 0, b""),
    ]


@pytest.mark.parametrize("size", [0, 1, 512, 1023])
def test_archive_follows_the_initrd_at_an_aligned_offset(tmp_path, binary, size):
    original = bytes(range(1, 256)) * 5
    path = tmp_path / "initrd.img"
    path.write_bytes(original[:size])

    fd = open_initrd_with_kerf_init(path)
    try:
        with open(fd, "rb", closefd=False) as combined:
            data = combined.read()
    finally:
        os.close(fd)

    start = size + -size % 4
    assert data[:size] == original[:size]
    assert data[size:start] == b"\0" * (start - size)
    assert entries(data[start:])[0] == (".kerf-init", 0o100755, binary.read_bytes())
    assert path.read_bytes() == original[:size]


def test_missing_kerf_init_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(initrd, "get_init_binary_path", lambda: tmp_path / "absent")

    with pytest.raises(InitrdError, match="Run 'make'"):
        kerf_init_archive()
