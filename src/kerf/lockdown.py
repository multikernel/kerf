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

kerf-init holds that program as the spawn's PID 1. A spawn that boots its
own initramfs has another PID 1, so there kerf-init runs first as rdinit=,
pins the program in a bpffs and execs the init named by kerf.init=.
"""

import mmap
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .data import KERF_INIT_PATH
from .exceptions import KerfError

LOCKDOWN_LEVELS: Tuple[str, ...] = ("integrity", "confidentiality")

# enum lockdown_reason without the LOCKDOWN_ prefix, in kernel order, with
# the kernel's own description of each. The spawn kernel's BTF is the
# authority; these only catch mistakes early and fill the help text.
_INTEGRITY = (
    ("module-signature", "unsigned module loading"),
    ("dev-mem", "/dev/mem, /dev/kmem and /dev/port"),
    ("efi-test", "/dev/efi_test access"),
    ("kexec", "kexec of unsigned images"),
    ("hibernation", "hibernation"),
    ("pci-access", "direct PCI access"),
    ("ioport", "raw I/O port access"),
    ("msr", "raw MSR access"),
    ("acpi-tables", "modifying ACPI tables"),
    ("device-tree", "modifying device tree contents"),
    ("pcmcia-cis", "direct PCMCIA CIS storage"),
    ("tiocsserial", "reconfiguration of serial port I/O"),
    ("module-parameters", "unsafe module parameters"),
    ("mmiotrace", "unsafe mmio"),
    ("debugfs", "debugfs access"),
    ("xmon-wr", "xmon write access"),
    ("bpf-write-user", "use of bpf to write user RAM"),
    ("dbg-write-kernel", "use of kgdb/kdb to write kernel RAM"),
    ("rtas-error-injection", "RTAS error injection"),
    ("xen-user-actions", "Xen guest user action"),
)
_CONFIDENTIALITY = (
    ("kcore", "/proc/kcore access"),
    ("kprobes", "use of kprobes"),
    ("bpf-read-kernel", "use of bpf to read kernel RAM"),
    ("dbg-read-kernel", "use of kgdb/kdb to read kernel RAM"),
    ("perf", "unsafe use of perf"),
    ("tracefs", "use of tracefs"),
    ("xmon-rw", "xmon read and write access"),
    ("xfrm-secret", "xfrm SA secret"),
)
INTEGRITY_REASONS: Tuple[str, ...] = tuple(name for name, _ in _INTEGRITY)
CONFIDENTIALITY_REASONS: Tuple[str, ...] = tuple(name for name, _ in _CONFIDENTIALITY)

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
    # kerf-init has to be added to the initrd, to run ahead of its init
    chained: bool = False


def lockdown_help() -> str:
    """The lockdown features of each level, for kerf start --help."""

    def table(reasons):
        return "\n".join(f"  {name:22}{what}" for name, what in reasons)

    # \b keeps click from rewrapping the paragraph that follows it
    return (
        "Lockdown features:\n\n"
        "--lockdown=integrity restricts what lets root modify the running kernel:\n\n"
        f"\b\n{table(_INTEGRITY)}\n\n"
        "--lockdown=confidentiality also restricts what lets root read kernel "
        "memory. With --lockdown=integrity, --deny selects among these by name:\n\n"
        f"\b\n{table(_CONFIDENTIALITY)}"
    )


def _check_options(level: Optional[str], kerf_init: bool, initrd: bool) -> None:
    if level is None:
        raise LockdownError("--deny requires --lockdown=integrity")
    if level == "confidentiality":
        raise LockdownError(
            "--deny is redundant with --lockdown=confidentiality, which already "
            "denies every confidentiality reason"
        )
    if not kerf_init and not initrd:
        raise LockdownError(
            "--deny needs kerf-init in the spawn; load it with --image, --rootfs-dir or --initrd"
        )


def _check_cmdline(words: List[str], level: str, denying: bool) -> bool:
    """Check the user's command line; return whether it already sets the level."""
    if any(word.startswith("kerf.deny=") for word in words):
        raise LockdownError("kerf.deny= is reserved; use --deny")
    if any(word.startswith("kerf.init=") for word in words):
        raise LockdownError("kerf.init= is reserved")

    wanted = f"lockdown={level}"
    settings = [word for word in words if word.startswith("lockdown=")]
    for setting in settings:
        if setting != wanted:
            raise LockdownError(f"--lockdown={level} conflicts with {setting} in the command line")

    # lsm= replaces the kernel's whole LSM list, and the last one wins
    lsm = [word for word in words if word.startswith("lsm=")]
    if lsm:
        active = lsm[-1][len("lsm="):].split(",")
        if "lockdown" not in active:
            raise LockdownError(f"{lsm[-1]} in the command line leaves out lockdown, disabling it")
        if denying and "bpf" not in active:
            raise LockdownError(
                f"{lsm[-1]} in the command line leaves out bpf, which --deny needs"
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


def _chain_params(words: List[str]) -> Tuple[str, ...]:
    """Put kerf-init ahead of the init the initramfs would have run."""
    rdinit = [word for word in words if word.startswith("rdinit=")]
    target = rdinit[-1][len("rdinit="):] if rdinit else "/init"
    if not target.startswith("/") or '"' in target:
        raise LockdownError(f"--deny cannot run rdinit={target} after kerf-init")
    # Appended after the user's own rdinit=, and the kernel takes the last
    return (f"rdinit={KERF_INIT_PATH}", f"kerf.init={target}")


def resolve_lockdown(
    level: Optional[str],
    deny: Optional[str],
    cmdline: Optional[str],
    kerf_init: bool,
    initrd: bool,
) -> Optional[LockdownRequest]:
    """
    Validate --lockdown and --deny against the rest of a kerf start request.

    Args:
        level: --lockdown, if given
        deny: --deny, if given
        cmdline: The command line of the run without them
        kerf_init: Whether kerf-init will be the spawn's init
        initrd: Whether the spawn was loaded with an initrd

    Returns:
        The request, or None when neither option was given

    Raises:
        LockdownError: The request cannot be honored
    """
    if level is None and deny is None:
        return None
    if deny is not None:
        _check_options(level, kerf_init, initrd)

    words = (cmdline or "").split()
    level_set = _check_cmdline(words, level, deny is not None)
    names, warnings = _parse_deny(deny) if deny is not None else ((), ())
    chained = bool(names) and not kerf_init

    params = [] if level_set else [f"lockdown={level}"]
    if names:
        params.append(f"kerf.deny={','.join(names)}")
    if chained:
        params.extend(_chain_params(words))
        warnings += (
            "with an initrd alone the deny list is only pinned in a bpffs; it holds "
            "until root in the spawn removes the pin",
        )
    elif names and initrd:
        warnings += ("the initrd runs before the deny list is in force",)
    return LockdownRequest(level, names, tuple(params), warnings, chained)


def image_supports_lockdown(kernel_fd: int) -> bool:
    """Whether an uncompressed kernel image was built with the lockdown LSM."""
    try:
        # Mapped, since a vmlinux with debug info runs to hundreds of
        # megabytes, and so that the offset stays put for kexec_file_load
        with mmap.mmap(kernel_fd, 0, prot=mmap.PROT_READ) as image:
            return image.find(_LOCKDOWN_MARKER) != -1
    except (ValueError, OSError):
        return False
