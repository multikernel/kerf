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
Tests for resolving kerf exec -u against a rootfs.
"""

import pytest

from kerf.exec.user import ResolvedUser, UserError, resolve_user

PASSWD = """\
root:x:0:0:root:/root:/bin/sh
# comment
alice:x:1000:1000::/home/alice:/bin/sh
bob:x:1001:100::/home/bob:/bin/sh
broken line
"""

GROUP = """\
root:x:0:
users:x:100:alice
wheel:x:10:alice,bob
alice:x:1000:
"""


@pytest.fixture(name="rootfs")
def fixture_rootfs(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "passwd").write_text(PASSWD)
    (tmp_path / "etc" / "group").write_text(GROUP)
    return tmp_path


def test_name(rootfs):
    assert resolve_user(rootfs, "alice") == ResolvedUser(1000, 1000, [100, 10], "/home/alice")


def test_number_with_entry(rootfs):
    assert resolve_user(rootfs, "1001") == ResolvedUser(1001, 100, [10], "/home/bob")


def test_number_without_entry(rootfs):
    assert resolve_user(rootfs, "4242") == ResolvedUser(4242, 0, [], "/")


def test_group_by_name(rootfs):
    assert resolve_user(rootfs, "alice:wheel").gid == 10


def test_group_by_number(rootfs):
    assert resolve_user(rootfs, "4242:7").gid == 7


def test_unknown_user(rootfs):
    with pytest.raises(UserError, match="unable to find user carol: no matching entries in passwd file"):
        resolve_user(rootfs, "carol")


def test_unknown_group(rootfs):
    with pytest.raises(UserError, match="unable to find group staff: no matching entries in group file"):
        resolve_user(rootfs, "alice:staff")


def test_names_need_a_mounted_rootfs(tmp_path):
    missing = tmp_path / "gone"
    with pytest.raises(UserError, match="rootfs is not mounted on the host"):
        resolve_user(missing, "alice")


def test_numbers_work_without_rootfs(tmp_path):
    assert resolve_user(tmp_path / "gone", "1000:1000") == ResolvedUser(1000, 1000, [], "/")


def test_empty_user(rootfs):
    with pytest.raises(UserError):
        resolve_user(rootfs, ":10")
