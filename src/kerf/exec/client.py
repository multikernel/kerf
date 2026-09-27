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
Host side of a kerf exec session: sends OPEN and stdin, prints what comes back.
"""

import os
import queue
import threading
from typing import BinaryIO, Callable, List, Optional, Sequence

from . import protocol

DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class LostConnection(Exception):
    """The instance closed the connection before EXIT."""


class RemoteError(Exception):
    """The spawn could not start the command."""

    def __init__(self, err: int, message: str):
        super().__init__(message)
        self.err = err
        self.message = message


def build_env(extra: Sequence[str], home: str, term: Optional[str]) -> List[str]:
    env = {"PATH": DEFAULT_PATH, "HOME": home}
    if term:
        env["TERM"] = term
    for item in extra:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"invalid environment entry '{item}', expected KEY=VALUE")
        env[key] = value
    return [f"{key}={value}" for key, value in env.items()]


class Session:
    """One exec connection. All sends go through a single writer thread so
    frames never interleave. Stdin is limited by the spawn's STDIN_ACK credit,
    so the queue stays small and control frames are never stuck behind it."""

    def __init__(self, sock):
        self.sock = sock
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._credit = protocol.STDIN_WINDOW
        self._credit_cond = threading.Condition()

    def send(self, frame: Optional[bytes]) -> None:
        self._queue.put(frame)

    def _writer(self) -> None:
        while True:
            frame = self._queue.get()
            if frame is None:
                return
            try:
                self.sock.sendall(frame)
            except OSError:
                return

    def _stdin_reader(self, fd: int) -> None:
        while True:
            data = os.read(fd, protocol.MAX_PAYLOAD)
            if not data:
                self.send(protocol.pack_frame(protocol.STDIN_EOF))
                return
            while data:
                with self._credit_cond:
                    self._credit_cond.wait_for(lambda: self._credit > 0)
                    n = min(len(data), self._credit)
                    self._credit -= n
                self.send(protocol.pack_frame(protocol.STDIN, data[:n]))
                data = data[n:]

    def run(
        self,
        open_frame: bytes,
        stdout: BinaryIO,
        stderr: BinaryIO,
        stdin_fd: Optional[int] = None,
        on_started: Optional[Callable[[], None]] = None,
    ) -> int:
        threading.Thread(target=self._writer, daemon=True).start()
        self.send(open_frame)
        try:
            while True:
                try:
                    frame = protocol.read_frame(self.sock)
                except (OSError, protocol.ProtocolError):
                    frame = None
                if frame is None:
                    raise LostConnection()
                ftype, payload = frame
                if ftype == protocol.STARTED:
                    if stdin_fd is not None:
                        threading.Thread(
                            target=self._stdin_reader, args=(stdin_fd,), daemon=True
                        ).start()
                    if on_started:
                        on_started()
                elif ftype in (protocol.STDOUT, protocol.STDERR):
                    out = stdout if ftype == protocol.STDOUT else stderr
                    out.write(payload)
                    out.flush()
                elif ftype == protocol.STDIN_ACK:
                    with self._credit_cond:
                        self._credit += protocol.unpack_ack(payload)
                        self._credit_cond.notify()
                elif ftype == protocol.EXIT:
                    return protocol.unpack_exit(payload)
                elif ftype == protocol.ERROR:
                    raise RemoteError(*protocol.unpack_error(payload))
        finally:
            self.send(None)
