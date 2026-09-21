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

"""Kernel parameters kerf load adds to every spawn's command line."""

from kerf.load.main import required_kernel_params

PSEUDO_NMI = "irqchip.gicv3_pseudo_nmi=1"


def test_arm64_spawn_always_gets_pseudo_nmis():
    assert required_kernel_params([], "aarch64") == [PSEUDO_NMI]
    assert required_kernel_params(["root=/dev/vda console=mktty0"], "arm64") == [PSEUDO_NMI]


def test_x86_needs_nothing():
    assert required_kernel_params(["root=/dev/sda1"], "x86_64") == []


def test_not_repeated_when_already_in_effect():
    assert required_kernel_params([f"quiet {PSEUDO_NMI}", "console=mktty0"], "aarch64") == []


def test_wins_over_a_command_line_that_turns_it_off():
    parts = ["irqchip.gicv3_pseudo_nmi=0 quiet"]

    assert required_kernel_params(parts, "aarch64") == [PSEUDO_NMI]


def test_appended_again_when_turned_off_after_being_on():
    parts = [PSEUDO_NMI, "irqchip.gicv3_pseudo_nmi=0"]

    assert required_kernel_params(parts, "aarch64") == [PSEUDO_NMI]
