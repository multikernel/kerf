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
Sign a spawn kernel so kexec_file_load() can verify it.

Run this where the signing key is kept, such as a build machine, not on
the hosts that load kernels.
"""

import sys
from pathlib import Path

import click

from ..signature import HASH_ALGORITHMS, SignatureError, is_signed, load_signing_key, sign_kernel
from ..vmlinuz import VmlinuzError, extract_vmlinux, is_bzimage


@click.command()
@click.argument("kernel", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path),
    help="Signed ELF vmlinux to write",
)
@click.option(
    "--key", "key_path", required=True, type=click.Path(exists=True, dir_okay=False),
    help="Private key, PEM or DER",
)
@click.option(
    "--cert", "cert_path", type=click.Path(exists=True, dir_okay=False),
    help="X.509 certificate, PEM or DER (default: read from --key)",
)
@click.option(
    "--hash", "hash_name", type=click.Choice(HASH_ALGORITHMS), default="sha256",
    show_default=True, help="Digest algorithm",
)
@click.option(
    "--password", envvar="KERF_SIGN_PASSWORD",
    help="Private key password (or $KERF_SIGN_PASSWORD)",
)
def sign(kernel, output, key_path, cert_path, hash_name, password):
    """Sign KERNEL for kexec_file_load() signature verification.

    KERNEL may be a bzImage, from which the embedded ELF vmlinux is
    extracted, or an ELF vmlinux. An existing signature is replaced. The
    signing certificate must be trusted by the host kernel, for example
    enrolled as a MOK.
    """
    try:
        key, cert = load_signing_key(key_path, cert_path, password)
        data = kernel.read_bytes()
        if is_bzimage(data):
            data = extract_vmlinux(data)
        elif is_signed(data):
            click.echo(f"Replacing the existing signature on {kernel}")
        signed = sign_kernel(data, key, cert, hash_name)
        output.write_bytes(signed)
    except (SignatureError, VmlinuzError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"Signed {output} with {cert.subject.rfc4514_string()} ({hash_name})")
