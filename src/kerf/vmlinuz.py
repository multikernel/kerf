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
bzImage (vmlinuz) detection and embedded vmlinux extraction.

On x86 the bzImage payload is the original ELF vmlinux, stripped but
otherwise intact (arch/x86/boot/compressed/Makefile uses plain objcopy,
not -O binary), so decompressing it yields an image the kexec ELF
loader accepts directly. This is the same trick the kernel's own
scripts/extract-vmlinux relies on.
"""

import bz2
import lzma
import os
import struct
import subprocess
import zlib

from .exceptions import KerfError


class VmlinuzError(KerfError):
    """Raised when a bzImage cannot be parsed or its payload extracted."""


ELF_MAGIC = b"\x7fELF"

# x86 boot protocol setup header fields (Documentation/arch/x86/boot.rst)
BZIMAGE_SETUP_SECTS = 0x1F1
BZIMAGE_MAGIC = 0x202
BZIMAGE_VERSION = 0x206
BZIMAGE_PAYLOAD_OFFSET = 0x248
BZIMAGE_PAYLOAD_LENGTH = 0x24C
BZIMAGE_HEADER_SIZE = 0x250


def is_bzimage(data: bytes) -> bool:
    """Check for the x86 boot protocol magic in a kernel image."""
    return data[BZIMAGE_MAGIC:BZIMAGE_MAGIC + 4] == b"HdrS"


def _gunzip(payload: bytes) -> bytes:
    return zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(payload)


def _unxz(payload: bytes) -> bytes:
    return lzma.LZMADecompressor(format=lzma.FORMAT_XZ).decompress(payload)


def _unlzma(payload: bytes) -> bytes:
    return lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(payload)


def _bunzip2(payload: bytes) -> bytes:
    return bz2.BZ2Decompressor().decompress(payload)


def _decompress_cli(argv: list, payload: bytes) -> bytes:
    try:
        proc = subprocess.run(
            argv, input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False,
        )
    except FileNotFoundError:
        raise VmlinuzError(
            f"'{argv[0]}' binary not found, required to decompress this kernel"
        ) from None
    # The CLI tools complain about data trailing the compressed stream
    # (relocations, appended size), but still emit the decoded output
    if not proc.stdout:
        raise VmlinuzError(f"{argv[0]} failed to decompress kernel payload")
    return proc.stdout


def _unzstd(payload: bytes) -> bytes:
    try:
        import zstandard
    except ImportError:
        return _decompress_cli(["zstd", "-d", "-c"], payload)
    try:
        return zstandard.ZstdDecompressor().decompressobj().decompress(payload)
    except zstandard.ZstdError as e:
        raise VmlinuzError(f"kernel payload decompression failed: {e}") from e


def _unlz4(payload: bytes) -> bytes:
    return _decompress_cli(["lz4", "-d", "-c"], payload)


_DECOMPRESSORS = [
    (b"\x1f\x8b", _gunzip),
    (b"\xfd7zXZ\x00", _unxz),
    (b"BZh", _bunzip2),
    (b"\x28\xb5\x2f\xfd", _unzstd),
    (b"\x02\x21\x4c\x18", _unlz4),
    (b"\x04\x22\x4d\x18", _unlz4),
    (b"\x5d\x00", _unlzma),
]


def _decompress(payload: bytes) -> bytes:
    for magic, decompress in _DECOMPRESSORS:
        if payload.startswith(magic):
            try:
                return decompress(payload)
            except (OSError, EOFError, zlib.error, lzma.LZMAError) as e:
                raise VmlinuzError(f"kernel payload decompression failed: {e}") from e
    if payload.startswith(b"\x89LZO"):
        raise VmlinuzError("LZO kernel compression is not supported")
    raise VmlinuzError(
        f"unknown kernel compression format (payload magic {payload[:4].hex()})"
    )


def _elf_size(data: bytes) -> int:
    """Size of the ELF image, excluding anything appended after it."""
    if data[4:6] != b"\x02\x01":
        raise VmlinuzError("embedded vmlinux is not a little-endian ELF64 image")
    e_phoff, e_shoff = struct.unpack_from("<QQ", data, 32)
    e_phentsize, e_phnum, e_shentsize, e_shnum = struct.unpack_from("<HHHH", data, 54)
    end = max(64, e_phoff + e_phentsize * e_phnum, e_shoff + e_shentsize * e_shnum)
    for i in range(e_phnum):
        p_offset, _, _, p_filesz = struct.unpack_from(
            "<QQQQ", data, e_phoff + i * e_phentsize + 8
        )
        end = max(end, p_offset + p_filesz)
    return min(end, len(data))


def extract_vmlinux(data: bytes) -> bytes:
    """Extract the embedded ELF vmlinux from a bzImage."""
    if not is_bzimage(data):
        raise VmlinuzError("not a bzImage (missing HdrS boot protocol magic)")

    version = struct.unpack_from("<H", data, BZIMAGE_VERSION)[0]
    if version < 0x0208:
        raise VmlinuzError(
            f"boot protocol {version >> 8}.{version & 0xFF:02d} is too old "
            "(payload location fields require >= 2.08)"
        )

    setup_sects = data[BZIMAGE_SETUP_SECTS] or 4
    payload_offset, payload_length = struct.unpack_from(
        "<II", data, BZIMAGE_PAYLOAD_OFFSET
    )
    start = (setup_sects + 1) * 512 + payload_offset
    payload = data[start:start + payload_length]
    if len(payload) < payload_length:
        raise VmlinuzError("bzImage is truncated (payload extends past end of file)")

    vmlinux = _decompress(payload)
    if not vmlinux.startswith(ELF_MAGIC):
        raise VmlinuzError("decompressed payload is not an ELF image")

    # Relocatable kernels have the relocation table appended after the
    # ELF image; drop it in case the kernel-side loader is strict
    return vmlinux[:_elf_size(vmlinux)]


def open_kernel_fd(path) -> int:
    """
    Open a kernel image for kexec_file_load.

    An ELF vmlinux is opened directly. A bzImage is decompressed and the
    embedded vmlinux is returned as an anonymous in-memory file.
    """
    with open(path, "rb") as f:
        head = f.read(BZIMAGE_HEADER_SIZE)
        if not is_bzimage(head):
            return os.open(str(path), os.O_RDONLY)
        data = head + f.read()

    vmlinux = extract_vmlinux(data)
    memfd = os.memfd_create("kerf-vmlinux")
    try:
        os.write(memfd, vmlinux)
        os.lseek(memfd, 0, os.SEEK_SET)
    except OSError:
        os.close(memfd)
        raise
    return memfd
