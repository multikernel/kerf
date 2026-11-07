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

import pytest
from click.testing import CliRunner

from kerf.load.main import build_ip_param, load, required_kernel_params

PSEUDO_NMI = "irqchip.gicv3_pseudo_nmi=1"


def test_arm64_spawn_always_gets_pseudo_nmis():
    assert required_kernel_params([], "aarch64") == [PSEUDO_NMI]
    assert required_kernel_params(["root=/dev/vda console=mktty0"], "arm64") == [PSEUDO_NMI]


def test_x86_needs_nothing():
    assert not required_kernel_params(["root=/dev/sda1"], "x86_64")


def test_not_repeated_when_already_in_effect():
    assert not required_kernel_params([f"quiet {PSEUDO_NMI}", "console=mktty0"], "aarch64")


def test_wins_over_a_command_line_that_turns_it_off():
    parts = ["irqchip.gicv3_pseudo_nmi=0 quiet"]

    assert required_kernel_params(parts, "aarch64") == [PSEUDO_NMI]


def test_appended_again_when_turned_off_after_being_on():
    parts = [PSEUDO_NMI, "irqchip.gicv3_pseudo_nmi=0"]

    assert required_kernel_params(parts, "aarch64") == [PSEUDO_NMI]


def test_dhcp_on_a_named_interface_puts_it_in_the_device_field():
    param = build_ip_param("dhcp", None, "255.255.255.0", None, "enP9s0")
    assert param == "ip=:::::enP9s0:dhcp"
    assert param[3:].split(":")[5] == "enP9s0"


def test_dhcp_on_any_interface():
    assert build_ip_param("dhcp", None, "255.255.255.0", None, None) == "ip=dhcp"
    assert build_ip_param("DHCP", None, "255.255.255.0", "web", None) == "ip=::::web::dhcp"


def test_static_address():
    assert (build_ip_param("192.168.122.157", "192.168.122.1", "255.255.255.0", None, "enP9s0")
            == "ip=192.168.122.157::192.168.122.1:255.255.255.0::enP9s0:off")


def test_no_address_no_parameter():
    assert build_ip_param(None, None, "255.255.255.0", None, "eth0") is None


def test_static_address_defaults_its_netmask():
    assert (build_ip_param("10.0.0.2", None, None, None, "eth0")
            == "ip=10.0.0.2:::255.255.255.0::eth0:off")


@pytest.mark.parametrize("options, named", [
    (["--nic=eth0"], "--nic"),
    (["--gateway=10.0.0.1", "--hostname=web"], "--gateway, --hostname"),
    (["--netmask=255.255.0.0"], "--netmask"),
])
def test_network_options_without_an_address_are_rejected(options, named):
    result = CliRunner().invoke(load, ["web", "--kernel=/boot/vmlinuz", *options])

    assert result.exit_code == 2
    assert f"Error: {named} requires --ip (an address or dhcp)" in result.output
