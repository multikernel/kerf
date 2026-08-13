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
    inspect_initrd_image,
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

    def test_load_corrupt_file_returns_none(self, tmp_path):
        (tmp_path / "web-server.json").write_text("not json{")
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


class TestInspectInitrdImage:
    def test_gzip_initrd(self, tmp_path):
        data = gzip.compress(b"fake cpio archive contents")
        path = tmp_path / "initrd.img"
        path.write_bytes(data)

        info = inspect_initrd_image(path)
        assert info["path"] == str(path)
        assert info["compression"] == "gzip"
        assert info["size"] == len(data)
        assert info["sha256"] == hashlib.sha256(data).hexdigest()

    def test_zstd_initrd_detected_from_magic(self, tmp_path):
        path = tmp_path / "initrd.img"
        path.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 32)

        assert inspect_initrd_image(path)["compression"] == "zstd"

    def test_uncompressed_cpio_initrd(self, tmp_path):
        # Distro initrds often start with an uncompressed early
        # microcode cpio in newc format
        path = tmp_path / "initrd.img"
        path.write_bytes(b"070701" + b"0" * 104 + b"TRAILER!!!")

        assert inspect_initrd_image(path)["compression"] == "cpio"

    def test_unknown_initrd_format(self, tmp_path):
        path = tmp_path / "initrd.img"
        path.write_bytes(b"mystery bytes")

        assert inspect_initrd_image(path)["compression"] is None


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
            "initrd": {
                "path": "/boot/initrd.img",
                "compression": "zstd",
                "size": 6789,
                "sha256": "cd" * 32,
            },
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
        assert "/boot/initrd.img (zstd compressed)" in out
        assert "2026-08-13T10:00:00+00:00" in out

    def test_initrd_row_absent_when_not_used(self, capsys):
        from kerf.show.main import display_instance_info

        metadata = self._metadata()
        metadata["initrd"] = None

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=metadata,
        )
        assert "Initrd" not in capsys.readouterr().out

    def test_uncompressed_cpio_initrd_row(self, capsys):
        from kerf.show.main import display_instance_info

        metadata = self._metadata()
        metadata["initrd"]["compression"] = "cpio"

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=metadata,
        )
        assert "/boot/initrd.img (uncompressed cpio)" in capsys.readouterr().out

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
        out = capsys.readouterr().out
        assert "ab" * 32 not in out
        assert "cd" * 32 not in out

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=self._metadata(),
            verbose=True,
        )
        out = capsys.readouterr().out
        assert "ab" * 32 in out
        assert "cd" * 32 in out

    def test_docker_rootfs_section(self, capsys):
        from kerf.show.main import display_instance_info

        metadata = self._metadata()
        metadata["rootfs"] = {
            "source": "docker",
            "image": "nginx:latest",
            "path": "/var/lib/kerf/rootfs/web-server",
            "entrypoint": "/docker-entrypoint.sh",
            "daxfs": {"phys_addr": 0xF80000000, "size": 536870912},
        }

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=metadata,
        )
        out = capsys.readouterr().out
        assert "Rootfs:" in out
        assert "nginx:latest" in out
        assert "/docker-entrypoint.sh" in out
        assert "phys=0xf80000000" in out
        assert "size=536870912" in out
        assert "/var/lib/kerf/rootfs/web-server" not in out

    def test_docker_rootfs_extraction_path_in_verbose(self, capsys):
        from kerf.show.main import display_instance_info

        metadata = self._metadata()
        metadata["rootfs"] = {
            "source": "docker",
            "image": "nginx:latest",
            "path": "/var/lib/kerf/rootfs/web-server",
            "entrypoint": "/docker-entrypoint.sh",
        }

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=metadata,
            verbose=True,
        )
        assert "/var/lib/kerf/rootfs/web-server" in capsys.readouterr().out

    def test_directory_rootfs_section(self, capsys):
        from kerf.show.main import display_instance_info

        metadata = self._metadata()
        metadata["rootfs"] = {
            "source": "directory",
            "path": "/mnt/rootfs",
            "entrypoint": "/sbin/init",
        }

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=metadata,
        )
        out = capsys.readouterr().out
        assert "/mnt/rootfs (directory)" in out
        assert "/sbin/init" in out

    def test_rootfs_section_absent_when_not_used(self, capsys):
        from kerf.show.main import display_instance_info

        display_instance_info(
            {"name": "web-server", "id": "1", "status": "loaded"},
            kimage_data=None,
            metadata=self._metadata(),
        )
        assert "Rootfs:" not in capsys.readouterr().out

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
