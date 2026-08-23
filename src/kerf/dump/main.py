# Copyright 2026 Multikernel Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Dump the device tree the kernel holds for the baseline or an instance."""

import sys
from pathlib import Path

import click

from ..dtc.parser import DeviceTreeParser
from ..exceptions import ParseError

KERNFS_ROOT = Path("/sys/fs/multikernel")


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def _device_tree_path(name):
    if not KERNFS_ROOT.exists():
        raise click.ClickException(
            f"{KERNFS_ROOT} is not mounted; is the multikernel kernel running?"
        )
    if name is None:
        return KERNFS_ROOT / "device_tree"
    path = KERNFS_ROOT / "instances" / name / "device_tree"
    if not path.exists():
        instances_dir = KERNFS_ROOT / "instances"
        known = sorted(p.name for p in instances_dir.iterdir()) if instances_dir.exists() else []
        hint = f" Known instances: {', '.join(known)}" if known else " No instances exist."
        raise click.ClickException(f"Instance '{name}' not found.{hint}")
    return path


@click.command()
@click.argument("name", required=False)
@click.option("--output", "-o", type=click.Path(dir_okay=False), help="Write here instead of stdout.")
@click.option("--dts", is_flag=True, help="Render as DTS text for reading. Not accepted by --input.")
def dump(name, output, dts):
    """Dump the baseline device tree, or that of instance NAME.

    The default output is the blob the kernel holds, byte for byte, which
    'kerf init --input' and 'kerf create --input' take back. With --dts the
    same tree is rendered as text for reading or diffing; only the DTB form
    can be replayed.
    """
    path = _device_tree_path(name)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise click.ClickException(f"Failed to read {path}: {e}") from e

    if dts:
        try:
            text = DeviceTreeParser().dts_from_dtb(data)
        except ParseError as e:
            raise click.ClickException(str(e)) from e
        if output:
            Path(output).write_text(text, encoding="utf-8")
        else:
            click.echo(text, nl=False)
        return

    if output:
        Path(output).write_bytes(data)
        return
    if _stdout_is_tty():
        raise click.ClickException(
            "Refusing to write a binary DTB to the terminal; use -o FILE or redirect stdout."
        )
    sys.stdout.buffer.write(data)
    sys.stdout.flush()
