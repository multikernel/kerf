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
@click.option("--output", "-o", type=click.Path(dir_okay=False), help="Write the DTB here instead of stdout.")
def dump(name, output):
    """Dump the baseline device tree, or that of instance NAME, as a DTB.

    The output is the blob the kernel holds, byte for byte, so a baseline
    dump can be handed back to 'kerf init --input'. Use 'dtc -I dtb -O dts'
    to read it.
    """
    path = _device_tree_path(name)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise click.ClickException(f"Failed to read {path}: {e}") from e

    if output:
        Path(output).write_bytes(data)
        return
    if _stdout_is_tty():
        raise click.ClickException(
            "Refusing to write a binary DTB to the terminal; use -o FILE or redirect stdout."
        )
    sys.stdout.buffer.write(data)
    sys.stdout.flush()
