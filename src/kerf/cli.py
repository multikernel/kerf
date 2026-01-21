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

"""
Command-line interface for kerf.
"""

import click
from .load.main import load
from .init.main import init
from .create.main import create
from .update.main import update
from .exec.main import exec_cmd
from .kill.main import kill_cmd
from .unload.main import unload
from .delete.main import delete
from .show.main import show
from .console.main import console


@click.group()
@click.version_option(version="0.1.0", prog_name="kerf")
@click.option("--debug", is_flag=True, help="Enable debug mode")
@click.pass_context
def main(ctx, debug):
    """kerf: Multikernel Management System - Device Tree Foundation."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug


# Add subcommands
main.add_command(init)
main.add_command(load)
main.add_command(create)
main.add_command(update)
main.add_command(exec_cmd)
main.add_command(kill_cmd)
main.add_command(unload)
main.add_command(delete)
main.add_command(show)
main.add_command(console)


if __name__ == "__main__":
    main()
