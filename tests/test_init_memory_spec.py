# Copyright 2025 Multikernel Technologies, Inc.
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

"""The --memory specification accepted by 'kerf init'."""

import pytest

from kerf.init.main import parse_memory_request, validate_memory_request

GB = 1 << 30


def test_plain_size_is_any_node():
    assert parse_memory_request("2GB") == {-1: 2 * GB}


def test_per_node_sizes():
    assert parse_memory_request("8GB@0, 512MB@1") == {0: 8 * GB, 1: 512 << 20}


@pytest.mark.parametrize(
    "spec",
    ["8GB@x", "8GB@-1", "8GB@0,8GB", "8GB@0,4GB@0", "@0", "8GB@", ""],
)
def test_invalid_specs(spec):
    with pytest.raises(ValueError):
        parse_memory_request(spec)


@pytest.mark.parametrize("spec", ["0", "0@0", "4097", "5000@1"])
def test_sizes_must_be_positive_and_page_aligned(spec):
    with pytest.raises(ValueError):
        parse_memory_request(spec)


@pytest.mark.parametrize("requested", [{-1: 0}, {0: -4096}, {0: 4097}, {1: 5000}])
def test_input_file_sizes_get_the_same_check(requested):
    # A DTS/DTB request reaches the kernel without going through --memory.
    with pytest.raises(ValueError):
        validate_memory_request(requested)


def test_valid_input_file_sizes_pass():
    validate_memory_request({0: GB, 1: 512 << 20})
