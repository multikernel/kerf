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
Tests for per-instance kernel image metadata recording and inspection.
"""

import gzip
import hashlib

import pytest

from kerf.metadata import (
    delete_instance_metadata,
    inspect_kernel_image,
    load_instance_metadata,
    save_instance_metadata,
)
from tests.test_vmlinuz import make_bzimage, make_elf64


@pytest.fixture(autouse=True)
def instances_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("kerf.metadata.KERF_INSTANCES_DIR", str(tmp_path))
    return tmp_path


class TestInstanceMetadataStore:
    def test_save_load_roundtrip(self):
        metadata = {"kernel": {"path": "/boot/vmlinuz"}, "initrd": None}
        save_instance_metadata("web-server", metadata)
        assert load_instance_metadata("web-server") == metadata

    def test_load_missing_returns_none(self):
        assert load_instance_metadata("no-such-instance") is None

    def test_save_overwrites_previous(self):
        save_instance_metadata("web-server", {"loaded_at": "old"})
        save_instance_metadata("web-server", {"loaded_at": "new"})
        assert load_instance_metadata("web-server") == {"loaded_at": "new"}

    def test_delete_removes_metadata(self):
        save_instance_metadata("web-server", {"kernel": {}})
        delete_instance_metadata("web-server")
        assert load_instance_metadata("web-server") is None

    def test_delete_missing_is_noop(self):
        delete_instance_metadata("no-such-instance")

    def test_load_corrupt_file_returns_none(self, instances_dir):
        (instances_dir / "web-server.json").write_text("not json{")
        assert load_instance_metadata("web-server") is None


class TestInspectKernelImage:
    def test_bzimage_with_version_string(self, tmp_path):
        data = make_bzimage(
            gzip.compress(make_elf64()),
            version_string="7.0.11-test (build@host) #1 SMP",
        )
        path = tmp_path / "vmlinuz"
        path.write_bytes(data)

        info = inspect_kernel_image(path)
        assert info["format"] == "bzImage"
        assert info["compression"] == "gzip"
        assert info["version"] == "7.0.11-test (build@host) #1 SMP"
        assert info["size"] == len(data)
        assert info["sha256"] == hashlib.sha256(data).hexdigest()

    def test_bzimage_compression_detected_from_magic(self, tmp_path):
        # Only the magic matters for naming, no decompression involved
        data = make_bzimage(b"\x28\xb5\x2f\xfd" + b"\x00" * 32)
        path = tmp_path / "vmlinuz"
        path.write_bytes(data)

        assert inspect_kernel_image(path)["compression"] == "zstd"

    def test_bzimage_without_version_string(self, tmp_path):
        path = tmp_path / "vmlinuz"
        path.write_bytes(make_bzimage(gzip.compress(make_elf64())))

        assert inspect_kernel_image(path)["version"] is None

    def test_vmlinux_with_banner(self, tmp_path):
        banner = b"Linux version 6.17.9-test (gcc 14) #1 SMP\n"
        path = tmp_path / "vmlinux"
        path.write_bytes(make_elf64(body=b"pad" + banner + b"more"))

        info = inspect_kernel_image(path)
        assert info["format"] == "vmlinux"
        assert info["compression"] is None
        assert info["version"] == "6.17.9-test (gcc 14) #1 SMP"

    def test_vmlinux_without_banner(self, tmp_path):
        path = tmp_path / "vmlinux"
        path.write_bytes(make_elf64())

        info = inspect_kernel_image(path)
        assert info["format"] == "vmlinux"
        assert info["version"] is None

    def test_unknown_format(self, tmp_path):
        path = tmp_path / "kernel"
        path.write_bytes(b"neither elf nor bzimage")

        assert inspect_kernel_image(path)["format"] == "unknown"


class TestDisplayMetadata:
    def _metadata(self):
        return {
            "kernel": {
                "path": "/boot/vmlinuz-7.0.11",
                "format": "bzImage",
                "compression": "zstd",
                "version": "7.0.11-test #1 SMP",
                "size": 12345,
                "sha256": "ab" * 32,
            },
            "initrd": "/boot/initrd.img",
            "loaded_at": "2026-08-13T10:00:00+00:00",
        }

    def test_metadata_shown_in_kernel_image_section(self, capsys):
        from kerf.show.main import display_instance_info

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data={"mk_id": "1", "type": "multikernel"},
            metadata=self._metadata(),
        )

        out = capsys.readouterr().out
        assert "/boot/vmlinuz-7.0.11" in out
        assert "bzImage (zstd compressed, vmlinux extracted at load)" in out
        assert "7.0.11-test #1 SMP" in out
        assert "/boot/initrd.img" in out
        assert "2026-08-13T10:00:00+00:00" in out

    def test_metadata_shown_without_kimage_data(self, capsys):
        from kerf.show.main import display_instance_info

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "created"},
            kimage_data=None,
            metadata=self._metadata(),
        )

        out = capsys.readouterr().out
        assert "Kernel Image:" in out
        assert "/boot/vmlinuz-7.0.11" in out

    def test_sha256_only_in_verbose(self, capsys):
        from kerf.show.main import display_instance_info

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=self._metadata(),
        )
        assert "ab" * 32 not in capsys.readouterr().out

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=self._metadata(),
            verbose=True,
        )
        assert "ab" * 32 in capsys.readouterr().out

    def test_plain_vmlinux_format_line(self, capsys):
        from kerf.show.main import display_instance_info

        metadata = self._metadata()
        metadata["kernel"]["format"] = "vmlinux"
        metadata["kernel"]["compression"] = None

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=metadata,
        )
        assert "vmlinux (ELF)" in capsys.readouterr().out
