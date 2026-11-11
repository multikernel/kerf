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

"""Tests for the daxfs image builder."""

import mmap
import os
import stat
import struct

from kerf.daxfs.mkdaxfs import (
    DAXFS_BLOCK_SIZE,
    DAXFS_INODE_SIZE,
    INODE_FORMAT,
    DaxfsBuilder,
)


def build(rootfs, overrides):
    builder = DaxfsBuilder(str(rootfs), overrides=overrides)
    builder.scan()
    builder.build_tree()
    builder.calculate_offsets()
    size = builder.calculate_total_size()
    mem = mmap.mmap(-1, size)
    builder.write_image(mem, size)
    return builder, mem


def read_inode(mem, ino):
    off = DAXFS_BLOCK_SIZE + (ino - 1) * DAXFS_INODE_SIZE
    fields = struct.unpack_from(INODE_FORMAT, mem, off)
    return {"mode": fields[1], "size": fields[4], "data_offset": fields[5]}


def test_override_keeps_the_image_init(tmp_path):
    rootfs = tmp_path / "rootfs"
    (rootfs / "system/bin").mkdir(parents=True)
    real_init = rootfs / "system/bin/init"
    real_init.write_bytes(b"android init")
    (rootfs / "init").symlink_to("/system/bin/init")
    kerf_init = tmp_path / "kerf-init"
    kerf_init.write_bytes(b"kerf init binary")

    builder, mem = build(rootfs, {".kerf-init": kerf_init})

    inode = read_inode(mem, builder.files_by_path[".kerf-init"].ino)
    assert inode["mode"] == stat.S_IFREG | 0o755
    data = DAXFS_BLOCK_SIZE + inode["data_offset"]
    assert mem[data:data + inode["size"]] == b"kerf init binary"

    assert stat.S_ISLNK(builder.files_by_path["init"].stat.st_mode)
    assert builder.files_by_path[""].child_count == 3
    assert os.readlink(rootfs / "init") == "/system/bin/init"
    assert real_init.read_bytes() == b"android init"
    assert not (rootfs / ".kerf-init").exists()


def test_override_replaces_an_entry_at_its_path(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / ".kerf-init").write_bytes(b"stale")
    kerf_init = tmp_path / "kerf-init"
    kerf_init.write_bytes(b"kerf init binary")

    builder, _ = build(rootfs, {".kerf-init": kerf_init})

    entries = [e for e in builder.files if e.path == ".kerf-init"]
    assert len(entries) == 1
    assert entries[0].source == kerf_init
    assert (rootfs / ".kerf-init").read_bytes() == b"stale"
