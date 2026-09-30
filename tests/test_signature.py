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
Tests for kernel image signing in the module signature format.
"""

import datetime
import gzip
import lzma
import shutil
import struct
import subprocess

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from kerf import signature
from kerf.sign.main import sign
from kerf.signature import (
    MODULE_SIG_STRING,
    PKEY_ID_PKCS7,
    SignatureError,
    is_signed,
    load_signing_key,
    sign_kernel,
    split_signature,
)
from tests.test_vmlinuz import make_bzimage, make_elf64


def make_cert(key, name="kerf test"):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )


@pytest.fixture(name="signer", params=["rsa", "ec"])
def fixture_signer(request):
    if request.param == "rsa":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
    return key, make_cert(key)


def write_key_files(tmp_path, key, cert):
    key_path = tmp_path / "signing_key.pem"
    cert_path = tmp_path / "signing_cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


class TestSignKernel:
    def test_appends_module_signature_trailer(self, signer):
        key, cert = signer
        elf = make_elf64()
        signed = sign_kernel(elf, key, cert)

        assert signed.startswith(elf)
        assert signed.endswith(MODULE_SIG_STRING)
        info = signed[-len(MODULE_SIG_STRING) - 12 : -len(MODULE_SIG_STRING)]
        algo, hash_id, id_type, signer_len, key_id_len, sig_len = struct.unpack(
            ">BBBBB3xI", info
        )
        assert (algo, hash_id, signer_len, key_id_len) == (0, 0, 0, 0)
        assert id_type == PKEY_ID_PKCS7
        assert len(signed) == len(elf) + sig_len + 12 + len(MODULE_SIG_STRING)

    def test_split_recovers_content(self, signer):
        key, cert = signer
        elf = make_elf64()
        content, sig = split_signature(sign_kernel(elf, key, cert))
        assert content == elf
        assert sig and sig[0] == 0x30  # DER SEQUENCE

    def test_unsigned_image_has_no_signature(self):
        elf = make_elf64()
        assert split_signature(elf) == (elf, None)
        assert not is_signed(elf)

    def test_resigning_replaces_signature(self, signer):
        key, cert = signer
        elf = make_elf64()
        once = sign_kernel(elf, key, cert)
        twice = sign_kernel(once, key, cert, "sha512")
        assert split_signature(twice)[0] == elf
        assert twice.count(MODULE_SIG_STRING) == 1

    def test_signer_algorithm_is_what_the_kernel_accepts(self, signer):
        # The kernel's PKCS#7 parser accepts rsaEncryption, not
        # sha256WithRSAEncryption, for RSA signers
        key, cert = signer
        _, sig = split_signature(sign_kernel(make_elf64(), key, cert))
        rsa_encryption = bytes.fromhex("06092a864886f70d010101")
        sha256_with_rsa = bytes.fromhex("06092a864886f70d01010b")
        ecdsa_with_sha256 = bytes.fromhex("06082a8648ce3d040302")
        assert sha256_with_rsa not in sig
        if isinstance(key, rsa.RSAPrivateKey):
            assert rsa_encryption in sig
        else:
            assert ecdsa_with_sha256 in sig

    def test_rejects_non_elf(self, signer):
        key, cert = signer
        with pytest.raises(SignatureError, match="not an ELF"):
            sign_kernel(b"MZ not an elf", key, cert)

    def test_rejects_unknown_hash(self, signer):
        key, cert = signer
        with pytest.raises(SignatureError, match="unsupported hash"):
            sign_kernel(make_elf64(), key, cert, "md5")

    def test_rejects_truncated_trailer(self):
        with pytest.raises(SignatureError):
            split_signature(b"\x7fELF" + MODULE_SIG_STRING)

    @pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
    def test_signature_verifies_with_openssl(self, tmp_path, signer):
        key, cert = signer
        content, sig = split_signature(sign_kernel(make_elf64(), key, cert))
        (tmp_path / "content").write_bytes(content)
        (tmp_path / "sig.p7").write_bytes(sig)
        (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        def verify():
            return subprocess.run(
                ["openssl", "cms", "-verify", "-binary", "-inform", "DER",
                 "-in", str(tmp_path / "sig.p7"), "-content", str(tmp_path / "content"),
                 "-certfile", str(tmp_path / "cert.pem"), "-noverify", "-out", "/dev/null"],
                capture_output=True, check=False,
            )

        assert verify().returncode == 0
        (tmp_path / "content").write_bytes(content + b"x")
        assert verify().returncode != 0


class TestLoadSigningKey:
    def test_separate_key_and_cert(self, tmp_path, signer):
        key_path, cert_path = write_key_files(tmp_path, *signer)
        _, cert = load_signing_key(key_path, cert_path)
        assert cert == signer[1]

    def test_combined_pem(self, tmp_path, signer):
        key_path, cert_path = write_key_files(tmp_path, *signer)
        combined = tmp_path / "combined.pem"
        combined.write_bytes(key_path.read_bytes() + cert_path.read_bytes())
        _, cert = load_signing_key(combined)
        assert cert == signer[1]

    def test_der_cert(self, tmp_path, signer):
        key_path, _ = write_key_files(tmp_path, *signer)
        der = tmp_path / "cert.der"
        der.write_bytes(signer[1].public_bytes(serialization.Encoding.DER))
        _, cert = load_signing_key(key_path, der)
        assert cert == signer[1]

    def test_mismatched_cert(self, tmp_path, signer):
        key_path, _ = write_key_files(tmp_path, *signer)
        other = ec.generate_private_key(ec.SECP256R1())
        other_cert = tmp_path / "other.pem"
        other_cert.write_bytes(make_cert(other).public_bytes(serialization.Encoding.PEM))
        with pytest.raises(SignatureError, match="does not match"):
            load_signing_key(key_path, other_cert)


class TestSignCommand:
    def test_signs_bzimage_as_extracted_vmlinux(self, tmp_path, signer):
        key_path, cert_path = write_key_files(tmp_path, *signer)
        elf = make_elf64()
        kernel = tmp_path / "bzImage"
        kernel.write_bytes(make_bzimage(lzma.compress(elf, format=lzma.FORMAT_XZ)))
        output = tmp_path / "vmlinux.signed"

        result = CliRunner().invoke(
            sign, [str(kernel), "-o", str(output), "--key", str(key_path), "--cert", str(cert_path)]
        )
        assert result.exit_code == 0, result.output
        assert split_signature(output.read_bytes())[0] == elf

    def test_reports_bad_input(self, tmp_path, signer):
        key_path, cert_path = write_key_files(tmp_path, *signer)
        kernel = tmp_path / "notakernel"
        kernel.write_bytes(b"garbage")

        result = CliRunner().invoke(
            sign, [str(kernel), "-o", str(tmp_path / "out"), "--key", str(key_path),
                   "--cert", str(cert_path)]
        )
        assert result.exit_code == 1
        assert "not an ELF" in result.output


class TestSignaturesEnforced:
    def test_lockdown_integrity(self, tmp_path, monkeypatch):
        lockdown = tmp_path / "lockdown"
        lockdown.write_text("none [integrity] confidentiality\n")
        monkeypatch.setattr(signature, "LOCKDOWN_PATH", str(lockdown))
        assert signature.kexec_signatures_enforced()

    def test_kexec_sig_force(self, tmp_path, monkeypatch):
        lockdown = tmp_path / "lockdown"
        lockdown.write_text("[none] integrity confidentiality\n")
        config = tmp_path / "config.gz"
        with gzip.open(config, "wt") as f:
            f.write("CONFIG_KEXEC_SIG=y\nCONFIG_KEXEC_SIG_FORCE=y\n")
        monkeypatch.setattr(signature, "LOCKDOWN_PATH", str(lockdown))
        monkeypatch.setattr(signature, "KCONFIG_PATH", str(config))
        assert signature.kexec_signatures_enforced()

    def test_not_enforced(self, tmp_path, monkeypatch):
        lockdown = tmp_path / "lockdown"
        lockdown.write_text("[none] integrity confidentiality\n")
        monkeypatch.setattr(signature, "LOCKDOWN_PATH", str(lockdown))
        monkeypatch.setattr(signature, "KCONFIG_PATH", str(tmp_path / "missing.gz"))
        assert not signature.kexec_signatures_enforced()
