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
Kernel image signatures in the kernel's module signature format.

A signed image is the image followed by a detached PKCS#7 signature, a
struct module_signature, and the "~Module signature appended~" marker,
the layout scripts/sign-file writes and kexec_file_load() verifies for an
ELF vmlinux.
"""

import gzip
import struct
from pathlib import Path

from .exceptions import KerfError
from .vmlinuz import ELF_MAGIC

MODULE_SIG_STRING = b"~Module signature appended~\n"
PKEY_ID_PKCS7 = 2

# struct module_signature: algo, hash, id_type, signer_len, key_id_len,
# __pad[3], __be32 sig_len
_SIG_INFO = struct.Struct(">BBBBB3xI")

HASH_ALGORITHMS = ("sha256", "sha384", "sha512")

LOCKDOWN_PATH = "/sys/kernel/security/lockdown"
KCONFIG_PATH = "/proc/config.gz"


class SignatureError(KerfError):
    """Raised when a kernel image cannot be signed or its signature parsed."""


def split_signature(data: bytes) -> "tuple[bytes, bytes | None]":
    """Split an image into its content and appended PKCS#7 signature, if any."""
    if not data.endswith(MODULE_SIG_STRING):
        return data, None

    end = len(data) - len(MODULE_SIG_STRING)
    if end < _SIG_INFO.size:
        raise SignatureError("signature marker present but signature info is truncated")
    algo, hash_id, id_type, signer_len, key_id_len, sig_len = _SIG_INFO.unpack_from(
        data, end - _SIG_INFO.size
    )
    end -= _SIG_INFO.size
    if id_type != PKEY_ID_PKCS7:
        raise SignatureError(f"unsupported signature type {id_type}, expected PKCS#7")
    if algo or hash_id or signer_len or key_id_len:
        raise SignatureError("PKCS#7 signature info has unexpected non-zero fields")
    if sig_len >= end:
        raise SignatureError("signature length exceeds image size")
    return data[: end - sig_len], data[end - sig_len : end]


def is_signed(data: bytes) -> bool:
    return data.endswith(MODULE_SIG_STRING)


def load_signing_key(key_path, cert_path=None, password=None):
    """
    Load a private key and its X.509 certificate from PEM or DER files.

    Without cert_path the certificate is read from the key file, which may
    hold both, as scripts/sign-file accepts.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    key_data = Path(key_path).read_bytes()
    pw = password.encode() if isinstance(password, str) else password
    try:
        if b"-----BEGIN" in key_data:
            key = serialization.load_pem_private_key(key_data, password=pw)
        else:
            key = serialization.load_der_private_key(key_data, password=pw)
    except (ValueError, TypeError) as e:
        raise SignatureError(f"{key_path}: cannot load private key: {e}") from e

    cert_data = Path(cert_path).read_bytes() if cert_path else key_data
    try:
        if b"-----BEGIN CERTIFICATE" in cert_data:
            cert = x509.load_pem_x509_certificate(cert_data)
        else:
            cert = x509.load_der_x509_certificate(cert_data)
    except ValueError as e:
        raise SignatureError(f"{cert_path or key_path}: cannot load certificate: {e}") from e

    def _der(public_key):
        return public_key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    if _der(cert.public_key()) != _der(key.public_key()):
        raise SignatureError("certificate does not match the private key")
    return key, cert


def _der(tag: int, content: bytes) -> bytes:
    if len(content) < 0x80:
        length = bytes([len(content)])
    else:
        raw = len(content).to_bytes((len(content).bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + content


def _seq(*items: bytes) -> bytes:
    return _der(0x30, b"".join(items))


def _set(*items: bytes) -> bytes:
    return _der(0x31, b"".join(items))


def _int(value: int) -> bytes:
    return _der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))


def _oid(dotted: str) -> bytes:
    arcs = [int(a) for a in dotted.split(".")]
    body = bytearray([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body += bytes(reversed(chunk))
    return _der(0x06, bytes(body))


_NULL = b"\x05\x00"
_OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
_OID_DATA = "1.2.840.113549.1.7.1"
_OID_RSA = "1.2.840.113549.1.1.1"
_OID_DIGEST = {
    "sha256": "2.16.840.1.101.3.4.2.1",
    "sha384": "2.16.840.1.101.3.4.2.2",
    "sha512": "2.16.840.1.101.3.4.2.3",
}
_OID_ECDSA = {
    "sha256": "1.2.840.10045.4.3.2",
    "sha384": "1.2.840.10045.4.3.3",
    "sha512": "1.2.840.10045.4.3.4",
}


def sign_kernel(data: bytes, key, cert, hash_name: str = "sha256") -> bytes:
    """
    Sign an ELF vmlinux, replacing any existing signature.

    The PKCS#7 message is laid out as scripts/sign-file writes it: detached,
    with no certificates and no authenticated attributes, naming the signer
    by issuer and serial number so the kernel finds the key in its keyrings.
    It is assembled here rather than by cryptography's PKCS#7 builder,
    which labels RSA signatures sha256WithRSAEncryption; the kernel only
    accepts rsaEncryption there.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    if hash_name not in HASH_ALGORITHMS:
        raise SignatureError(f"unsupported hash {hash_name}, choose one of {HASH_ALGORITHMS}")
    content, _ = split_signature(data)
    if not content.startswith(ELF_MAGIC):
        raise SignatureError("not an ELF vmlinux")

    algorithm = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}[
        hash_name
    ]()
    if isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(content, padding.PKCS1v15(), algorithm)
        sig_algo = _seq(_oid(_OID_RSA), _NULL)
    elif isinstance(key, ec.EllipticCurvePrivateKey):
        sig = key.sign(content, ec.ECDSA(algorithm))
        sig_algo = _seq(_oid(_OID_ECDSA[hash_name]))
    else:
        raise SignatureError("only RSA and ECDSA keys are supported")

    digest_algo = _seq(_oid(_OID_DIGEST[hash_name]))
    signer_info = _seq(
        _int(1),
        _seq(cert.issuer.public_bytes(), _int(cert.serial_number)),
        digest_algo,
        sig_algo,
        _der(0x04, sig),
    )
    signed_data = _seq(_int(1), _set(digest_algo), _seq(_oid(_OID_DATA)), _set(signer_info))
    pkcs7 = _seq(_oid(_OID_SIGNED_DATA), _der(0xA0, signed_data))

    return content + pkcs7 + _SIG_INFO.pack(0, 0, PKEY_ID_PKCS7, 0, 0, len(pkcs7)) + MODULE_SIG_STRING


def kexec_signatures_enforced() -> bool:
    """Whether this kernel refuses unsigned kexec_file_load() images."""
    try:
        lockdown = Path(LOCKDOWN_PATH).read_text(encoding="utf-8")
        if "[integrity]" in lockdown or "[confidentiality]" in lockdown:
            return True
    except OSError:
        pass

    try:
        with gzip.open(KCONFIG_PATH, "rt", encoding="utf-8") as f:
            return any(line.strip() == "CONFIG_KEXEC_SIG_FORCE=y" for line in f)
    except OSError:
        return False
