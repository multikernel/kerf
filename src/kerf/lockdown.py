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
Kernel lockdown for spawn kernels.

The kernel offers two levels, lockdown=integrity and
lockdown=confidentiality. On top of integrity, kerf-init can deny
individual confidentiality reasons with a BPF LSM program; kerf.deny=
carries their names to it.
"""

import mmap
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .exceptions import KerfError

LOCKDOWN_LEVELS: Tuple[str, ...] = ("integrity", "confidentiality")

# enum lockdown_reason without the LOCKDOWN_ prefix, in kernel order. The
# spawn kernel's own BTF is the authority; these only catch mistakes early.
INTEGRITY_REASONS: Tuple[str, ...] = (
    "module-signature", "dev-mem", "efi-test", "kexec", "hibernation",
    "pci-access", "ioport", "msr", "acpi-tables", "device-tree",
    "pcmcia-cis", "tiocsserial", "module-parameters", "mmiotrace", "debugfs",
    "xmon-wr", "bpf-write-user", "dbg-write-kernel", "rtas-error-injection",
    "xen-user-actions",
)
CONFIDENTIALITY_REASONS: Tuple[str, ...] = (
    "kcore", "kprobes", "bpf-read-kernel", "dbg-read-kernel",
    "perf", "tracefs", "xmon-rw", "xfrm-secret",
)

# Printed by lock_kernel_down(), which is built only with
# CONFIG_SECURITY_LOCKDOWN_LSM; a kernel without it ignores lockdown=.
_LOCKDOWN_MARKER = b"Kernel is locked down from"

_REASON_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


class LockdownError(KerfError):
    """Raised when a spawn lockdown request cannot be honored."""


@dataclass(frozen=True)
class LockdownRequest:
    """A validated lockdown request and what it adds to the command line."""

    level: str
    deny: Tuple[str, ...]
    params: Tuple[str, ...]
    warnings: Tuple[str, ...]


def _check_options(level: Optional[str], kerf_init: bool, initrd: bool) -> None:
    if level is None:
        raise LockdownError("--deny requires --lockdown=integrity")
    if level == "confidentiality":
        raise LockdownError(
            "--deny is redundant with --lockdown=confidentiality, which already "
            "denies every confidentiality reason"
        )
    if not kerf_init:
        raise LockdownError(
            "--deny needs kerf-init as the spawn's init; use --image or --rootfs-dir"
        )
    if initrd:
        raise LockdownError(
            "--deny cannot be combined with --initrd: the initrd would run before "
            "the deny list is in force"
        )


def _check_cmdline(words: List[str], level: str, denying: bool) -> bool:
    """Check the user's command line; return whether it already sets the level."""
    if any(word.startswith("kerf.deny=") for word in words):
        raise LockdownError("kerf.deny= is reserved; use --deny")

    wanted = f"lockdown={level}"
    settings = [word for word in words if word.startswith("lockdown=")]
    for setting in settings:
        if setting != wanted:
            raise LockdownError(f"--lockdown={level} conflicts with {setting} in --cmdline")

    # lsm= replaces the kernel's whole LSM list, and the last one wins
    lsm = [word for word in words if word.startswith("lsm=")]
    if lsm:
        active = lsm[-1][len("lsm="):].split(",")
        if "lockdown" not in active:
            raise LockdownError(f"{lsm[-1]} in --cmdline leaves out lockdown, disabling it")
        if denying and "bpf" not in active:
            raise LockdownError(
                f"{lsm[-1]} in --cmdline leaves out bpf, which --deny needs"
            )
    return bool(settings)


def _parse_deny(deny: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    names: List[str] = []
    warnings: List[str] = []
    for name in deny.split(","):
        if not _REASON_RE.fullmatch(name):
            raise LockdownError(
                f"malformed --deny name '{name}'; give comma-separated names such as "
                f"{','.join(CONFIDENTIALITY_REASONS[:2])}"
            )
        if name in INTEGRITY_REASONS:
            raise LockdownError(
                f"'{name}' is an integrity reason, already covered by --lockdown=integrity; "
                f"--deny selects among: {', '.join(CONFIDENTIALITY_REASONS)}"
            )
        if name in names:
            continue
        if name not in CONFIDENTIALITY_REASONS:
            warnings.append(
                f"'{name}' is not a lockdown reason kerf knows; the spawn will refuse "
                "to start if its kernel does not have it either"
            )
        names.append(name)
    return tuple(names), tuple(warnings)


def resolve_lockdown(
    level: Optional[str],
    deny: Optional[str],
    cmdline: Optional[str],
    kerf_init: bool,
    initrd: bool,
) -> Optional[LockdownRequest]:
    """
    Validate --lockdown and --deny against the rest of a kerf load request.

    Args:
        level: --lockdown, if given
        deny: --deny, if given
        cmdline: --cmdline, if given
        kerf_init: Whether kerf-init will be the spawn's init
        initrd: Whether an initrd was given

    Returns:
        The request, or None when neither option was given

    Raises:
        LockdownError: The request cannot be honored
    """
    if level is None and deny is None:
        return None
    if deny is not None:
        _check_options(level, kerf_init, initrd)

    level_set = _check_cmdline((cmdline or "").split(), level, deny is not None)
    names, warnings = _parse_deny(deny) if deny is not None else ((), ())

    params = [] if level_set else [f"lockdown={level}"]
    if names:
        params.append(f"kerf.deny={','.join(names)}")
    return LockdownRequest(level, names, tuple(params), warnings)


def image_supports_lockdown(kernel_fd: int) -> bool:
    """Whether an uncompressed kernel image was built with the lockdown LSM."""
    try:
        # Mapped, since a vmlinux with debug info runs to hundreds of
        # megabytes, and so that the offset stays put for kexec_file_load
        with mmap.mmap(kernel_fd, 0, prot=mmap.PROT_READ) as image:
            return image.find(_LOCKDOWN_MARKER) != -1
    except (ValueError, OSError):
        return False
