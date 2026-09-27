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
Resolve kerf exec -u to numeric credentials, the way containerd does for kata.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List


class UserError(Exception):
    """The -u value cannot be resolved."""


@dataclass(frozen=True)
class ResolvedUser:
    uid: int
    gid: int
    groups: List[int]
    home: str


def _read_db(path: Path) -> List[List[str]]:
    try:
        text = path.read_text(errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [line.split(":") for line in text.splitlines() if line and not line.startswith("#")]


def resolve_user(rootfs: Path, spec: str) -> ResolvedUser:
    user, _, group = spec.partition(":")
    if not user:
        raise UserError(f"invalid user '{spec}'")
    if (not user.isdigit() or (group and not group.isdigit())) and not rootfs.is_dir():
        raise UserError(
            f"cannot resolve user names: rootfs is not mounted on the host at {rootfs}"
        )

    passwd = [
        r for r in _read_db(rootfs / "etc" / "passwd")
        if len(r) >= 7 and r[2].isdigit() and r[3].isdigit()
    ]
    groups = [r for r in _read_db(rootfs / "etc" / "group") if len(r) >= 4 and r[2].isdigit()]

    if user.isdigit():
        uid = int(user)
        entry = next((r for r in passwd if int(r[2]) == uid), None)
    else:
        entry = next((r for r in passwd if r[0] == user), None)
        if entry is None:
            raise UserError(f"unable to find user {user}: no matching entries in passwd file")
        uid = int(entry[2])

    if not group:
        gid = int(entry[3]) if entry else 0
    elif group.isdigit():
        gid = int(group)
    else:
        match = next((r for r in groups if r[0] == group), None)
        if match is None:
            raise UserError(f"unable to find group {group}: no matching entries in group file")
        gid = int(match[2])

    supplementary: List[int] = []
    if entry:
        for r in groups:
            if entry[0] in r[3].split(",") and int(r[2]) not in supplementary:
                supplementary.append(int(r[2]))

    home = entry[5] if entry and entry[5] else "/"
    return ResolvedUser(uid, gid, supplementary, home)
