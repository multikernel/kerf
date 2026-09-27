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
kerf exec: run a command inside a running instance.
"""

import errno
import os
import signal
import socket
import sys
import termios
import tty as ttymod
from pathlib import Path
from typing import Optional, Tuple

import click

from ..daxfs.mkdaxfs import KERF_DAXFS_MNT_DIR
from ..models import InstanceState
from ..utils import get_instance_id_from_name, get_instance_name_from_id, get_instance_status
from . import protocol
from .client import LostConnection, RemoteError, Session, build_env
from .user import UserError, resolve_user

SO_VM_SOCKETS_TRANSPORT = 9
VSOCK_TRANSPORT_MULTIKERNEL = 1


class ExecError(Exception):
    """kerf-side failure before the command runs."""


class _Terminated(Exception):
    def __init__(self, signo: int):
        super().__init__(signo)
        self.signo = signo


def _terminate(signo, _frame):
    raise _Terminated(signo)


def _resolve_instance(name: Optional[str], instance_id: Optional[int]) -> Tuple[str, int]:
    if instance_id is None:
        instance_id = get_instance_id_from_name(name)
        if instance_id is None:
            raise ExecError(f"Instance '{name}' not found")
    else:
        name = get_instance_name_from_id(instance_id)
        if name is None:
            raise ExecError(f"Instance with ID {instance_id} not found")
    status = get_instance_status(name)
    if status is None or status.lower() != InstanceState.ACTIVE.value:
        raise ExecError(
            f"Instance '{name}' is not active (status: '{status}'). "
            f"Start it with: kerf start {name}"
        )
    return name, instance_id


def connect_agent(instance_id: int, name: str) -> socket.socket:
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.AF_VSOCK, SO_VM_SOCKETS_TRANSPORT, VSOCK_TRANSPORT_MULTIKERNEL)
        # kerf-init only trusts reserved ports, which only root can bind.
        for port in range(protocol.AGENT_PORT, 511, -1):
            try:
                sock.bind((socket.VMADDR_CID_ANY, port))
                break
            except OSError as e:
                if e.errno == errno.EACCES:
                    raise ExecError("kerf exec requires root") from e
                if e.errno != errno.EADDRINUSE:
                    raise
        else:
            raise ExecError("no free reserved vsock port")
        try:
            sock.connect((instance_id, protocol.AGENT_PORT))
        except (ConnectionResetError, ConnectionRefusedError) as e:
            raise ExecError(
                f"instance '{name}' is not running kerf-init, or it has not finished booting"
            ) from e
        except OSError as e:
            raise ExecError(f"cannot connect to instance '{name}': {e.strerror}") from e
        return sock
    except BaseException:
        sock.close()
        raise


def _winsize(fd: int) -> Tuple[int, int]:
    try:
        size = os.get_terminal_size(fd)
        return size.lines, size.columns
    except OSError:
        return 24, 80


def _install_handlers(session: Session, use_tty: bool) -> None:
    if use_tty:
        signal.signal(
            signal.SIGWINCH,
            lambda *_: session.send(protocol.pack_resize(*_winsize(0))),
        )
        return
    for signo in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signo, lambda num, _frame: session.send(protocol.pack_signal(num)))


@click.command(name="exec", context_settings={"allow_interspersed_args": False})
@click.option("-i", "--interactive", is_flag=True, help="Forward stdin to the command")
@click.option("-t", "--tty", "use_tty", is_flag=True, help="Allocate a pseudo-terminal")
@click.option("-e", "--env", "env_vars", multiple=True, help="Set an environment variable KEY=VALUE")
@click.option("-w", "--workdir", default="/", help="Working directory inside the instance")
@click.option("-u", "--user", default=None, help="USER[:GROUP], by name or number")
@click.option("--id", "instance_id", type=int, help="Instance ID (instead of a name)")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def exec_cmd(interactive, use_tty, env_vars, workdir, user, instance_id, args):
    """
    Run a command inside a running instance.

    Examples:

        kerf exec web-server ls /
        kerf exec -it web-server sh
        kerf exec -u nobody --id=1 -- id
    """
    args = list(args)
    name = None if instance_id is not None else (args.pop(0) if args else None)
    # Option parsing stops at NAME, so a separating "--" after it arrives here.
    if args and args[0] == "--":
        args.pop(0)
    if (instance_id is None and name is None) or not args:
        raise click.UsageError("usage: kerf exec [OPTIONS] NAME|--id N [--] COMMAND [ARG]...")

    try:
        name, instance_id = _resolve_instance(name, instance_id)
        creds, home = None, "/root"
        if user is not None:
            resolved = resolve_user(Path(KERF_DAXFS_MNT_DIR) / name, user)
            creds, home = (resolved.uid, resolved.gid, resolved.groups), resolved.home
        env = build_env(env_vars, home, os.environ.get("TERM") if use_tty else None)
        if use_tty and interactive and not os.isatty(0):
            raise ExecError("the input device is not a TTY")
        rows, cols = _winsize(0) if use_tty else (0, 0)
        open_frame = protocol.pack_open(
            args, env, cwd=workdir, tty=use_tty, stdin=interactive,
            rows=rows, cols=cols, user=creds,
        )
        sock = connect_agent(instance_id, name)
    except (ExecError, UserError, ValueError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    saved = termios.tcgetattr(0) if use_tty and interactive else None
    session = Session(sock)
    handlers = {signo: signal.getsignal(signo) for signo in (
        signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGWINCH)}
    try:
        if saved is not None:
            # A raw terminal must be restored however kerf exec is stopped.
            signal.signal(signal.SIGTERM, _terminate)
            signal.signal(signal.SIGHUP, _terminate)
            ttymod.setraw(0)
        code = session.run(
            open_frame,
            sys.stdout.buffer,
            sys.stderr.buffer,
            stdin_fd=0 if interactive else None,
            on_started=lambda: _install_handlers(session, use_tty),
        )
    except RemoteError as e:
        if e.err == errno.ENOSYS and creds is not None:
            click.echo("Error: spawn kernel has no multiuser support (CONFIG_MULTIUSER=n)", err=True)
        else:
            click.echo(f"Error: {e.message}", err=True)
        code = protocol.error_exit_code(e.err)
    except LostConnection:
        click.echo("Error: connection to instance lost", err=True)
        code = 1
    except _Terminated as e:
        code = 128 + e.signo
    except BrokenPipeError:
        # Behave like a command killed by SIGPIPE; stdout is gone, so point it
        # at /dev/null to keep the interpreter's final flush quiet.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        code = 128 + signal.SIGPIPE
    finally:
        if saved is not None:
            termios.tcsetattr(0, termios.TCSADRAIN, saved)
        for signo, handler in handlers.items():
            signal.signal(signo, handler)
        sock.close()
    sys.exit(code)
