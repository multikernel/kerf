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
Tests for bzImage (vmlinuz) detection and vmlinux extraction.
"""

import gzip
import lzma
import os
import struct

import pytest

from kerf.vmlinuz import VmlinuzError, extract_vmlinux, is_bzimage, open_kernel_fd


def make_elf64(body: bytes = b"kernel code segment") -> bytes:
    """Build a minimal ELF64 image with one program header."""
    ehdr_size = 64
    phdr_size = 56
    p_offset = ehdr_size + phdr_size

    ehdr = struct.pack(
        "<4sBBBBB7xHHIQQQIHHHHHH",
        b"\x7fELF",  # magic
        2,  # ELFCLASS64
        1,  # ELFDATA2LSB
        1,  # EV_CURRENT
        0,  # ELFOSABI_NONE
        0,  # ABI version
        2,  # ET_EXEC
        0x3E,  # EM_X86_64
        1,  # e_version
        0xFFFFFFFF81000000,  # e_entry
        ehdr_size,  # e_phoff
        0,  # e_shoff
        0,  # e_flags
        ehdr_size,  # e_ehsize
        phdr_size,  # e_phentsize
        1,  # e_phnum
        0,  # e_shentsize
        0,  # e_shnum
        0,  # e_shstrndx
    )
    phdr = struct.pack(
        "<IIQQQQQQ",
        1,  # PT_LOAD
        5,  # PF_R | PF_X
        p_offset,  # p_offset
        0xFFFFFFFF81000000,  # p_vaddr
        0x1000000,  # p_paddr
        len(body),  # p_filesz
        len(body),  # p_memsz
        0x200000,  # p_align
    )
    return ehdr + phdr + body


def make_bzimage(payload: bytes, setup_sects: int = 4, version: int = 0x020F) -> bytes:
    """Build a minimal bzImage wrapping the given compressed payload."""
    header = bytearray(512 * ((setup_sects or 4) + 1))
    header[0x1F1] = setup_sects
    header[0x202:0x206] = b"HdrS"
    struct.pack_into("<H", header, 0x206, version)
    struct.pack_into("<I", header, 0x248, 0)  # payload_offset
    struct.pack_into("<I", header, 0x24C, len(payload))  # payload_length
    return bytes(header) + payload


class TestIsBzimage:
    def test_detects_bzimage_magic(self):
        data = make_bzimage(b"\x00" * 16)
        assert is_bzimage(data) is True

    def test_rejects_elf(self):
        assert is_bzimage(make_elf64()) is False

    def test_rejects_short_file(self):
        assert is_bzimage(b"\x7fELF") is False


class TestExtractVmlinux:
    def test_extracts_gzip_payload(self):
        elf = make_elf64()
        data = make_bzimage(gzip.compress(elf))
        assert extract_vmlinux(data) == elf

    def test_extracts_xz_payload(self):
        elf = make_elf64()
        # Kernel build appends the uncompressed size after the xz stream
        payload = lzma.compress(elf, format=lzma.FORMAT_XZ) + struct.pack("<I", len(elf))
        data = make_bzimage(payload)
        assert extract_vmlinux(data) == elf

    def test_extracts_lzma_payload(self):
        elf = make_elf64()
        payload = lzma.compress(elf, format=lzma.FORMAT_ALONE) + struct.pack("<I", len(elf))
        data = make_bzimage(payload)
        assert extract_vmlinux(data) == elf

    def test_extracts_bzip2_payload(self):
        import bz2

        elf = make_elf64()
        payload = bz2.compress(elf) + struct.pack("<I", len(elf))
        data = make_bzimage(payload)
        assert extract_vmlinux(data) == elf

    def test_extracts_zstd_payload(self):
        zstandard = pytest.importorskip("zstandard")
        elf = make_elf64()
        payload = zstandard.ZstdCompressor().compress(elf) + struct.pack("<I", len(elf))
        data = make_bzimage(payload)
        assert extract_vmlinux(data) == elf

    def test_truncates_trailing_relocations(self):
        elf = make_elf64()
        data = make_bzimage(gzip.compress(elf + b"RELOCATION-TABLE" * 8))
        assert extract_vmlinux(data) == elf

    def test_setup_sects_zero_defaults_to_four(self):
        elf = make_elf64()
        data = make_bzimage(gzip.compress(elf), setup_sects=0)
        # setup_sects=0 means 4 per the boot protocol, so the header
        # area is identical to the setup_sects=4 layout
        assert extract_vmlinux(data) == elf

    def test_rejects_unknown_compression(self):
        data = make_bzimage(b"\x00\x01\x02\x03 not compressed data")
        with pytest.raises(VmlinuzError, match="compression"):
            extract_vmlinux(data)

    def test_rejects_old_boot_protocol(self):
        data = make_bzimage(gzip.compress(make_elf64()), version=0x0207)
        with pytest.raises(VmlinuzError, match="boot protocol"):
            extract_vmlinux(data)

    def test_rejects_non_elf_payload(self):
        data = make_bzimage(gzip.compress(b"this is not an ELF image"))
        with pytest.raises(VmlinuzError, match="ELF"):
            extract_vmlinux(data)

    def test_rejects_non_bzimage(self):
        with pytest.raises(VmlinuzError, match="bzImage"):
            extract_vmlinux(make_elf64())


class TestOpenKernelFd:
    def test_elf_file_passthrough(self, tmp_path):
        elf = make_elf64()
        path = tmp_path / "vmlinux"
        path.write_bytes(elf)

        fd = open_kernel_fd(path)
        try:
            assert os.read(fd, len(elf) + 1) == elf
        finally:
            os.close(fd)

    def test_bzimage_is_extracted_to_memfd(self, tmp_path):
        elf = make_elf64()
        path = tmp_path / "vmlinuz"
        path.write_bytes(make_bzimage(gzip.compress(elf)))

        fd = open_kernel_fd(path)
        try:
            assert os.read(fd, len(elf) + 1) == elf
        finally:
            os.close(fd)
