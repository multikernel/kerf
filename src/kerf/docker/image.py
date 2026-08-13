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
OCI image extraction and configuration parsing via the Docker daemon API.
"""

import hashlib
import http.client
import json
import shutil
import socket
import tarfile
import tempfile
import urllib.parse
from pathlib import Path
from typing import List, Tuple

KERF_ROOTFS_DIR = "/var/lib/kerf/rootfs"
DOCKER_SOCKET = "/var/run/docker.sock"


class DockerError(Exception):
    """Exception raised for image extraction errors."""


class _UnixSocketConnection(http.client.HTTPConnection):
    """HTTP connection over the Docker daemon's Unix socket."""

    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self.sock = sock


def _docker_request(method: str, path: str) -> http.client.HTTPResponse:
    """Send a request to the Docker daemon and return the response.

    The caller owns the response and must read it fully; closing the
    response also closes the underlying connection.
    """
    conn = _UnixSocketConnection(DOCKER_SOCKET)
    try:
        conn.request(method, path)
        return conn.getresponse()
    except (FileNotFoundError, ConnectionRefusedError, PermissionError) as e:
        conn.close()
        raise DockerError(
            f"Cannot connect to Docker daemon at {DOCKER_SOCKET}. "
            "Is the Docker daemon running?"
        ) from e


def _split_ref(image_ref: str) -> Tuple[str, str]:
    """Split an image reference into (name, tag-or-digest)."""
    if "@" in image_ref:
        name, _, digest = image_ref.partition("@")
        return name, digest
    name, sep, tag = image_ref.rpartition(":")
    # A ':' inside the registry host part (e.g. localhost:5000/img) is not a tag
    if sep and "/" not in tag:
        return name, tag
    return image_ref, "latest"


def _error_message(body: str) -> str:
    """Extract the human-readable message from a daemon error body."""
    try:
        return json.loads(body)["message"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return body.strip()


def _pull_image(image_ref: str) -> None:
    """Pull an image through the Docker daemon."""
    name, tag = _split_ref(image_ref)
    query = urllib.parse.urlencode({"fromImage": name, "tag": tag})
    resp = _docker_request("POST", f"/images/create?{query}")
    with resp:
        if resp.status != 200:
            body = resp.read().decode(errors="replace")
            raise DockerError(
                f"Failed to pull image '{image_ref}': {_error_message(body)}"
            )
        # The daemon streams progress as JSON lines; a mid-stream failure
        # still comes back with status 200 and an "error" message.
        for line in resp:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in message:
                raise DockerError(
                    f"Failed to pull image '{image_ref}': {message['error']}"
                )


def _image_request_with_pull(method: str, path_template: str, image_ref: str
                             ) -> http.client.HTTPResponse:
    """Send an image request, pulling the image first if not present locally."""
    path = path_template.format(name=urllib.parse.quote(image_ref, safe="/:"))
    resp = _docker_request(method, path)
    if resp.status == 404:
        resp.read()
        resp.close()
        _pull_image(image_ref)
        resp = _docker_request(method, path)
    if resp.status != 200:
        status = resp.status
        with resp:
            body = resp.read().decode(errors="replace")
        raise DockerError(
            f"Docker daemon returned status {status} for image "
            f"'{image_ref}': {_error_message(body)}"
        )
    return resp


def extract_image(image_ref: str, instance_name: str) -> Tuple[str, List[str], str]:
    """
    Extract an image filesystem to a directory via the Docker daemon.

    Args:
        image_ref: Docker image reference (e.g., "nginx:latest")
        instance_name: Instance name for directory naming

    Returns:
        Tuple of (rootfs_path, entrypoint_cmd, image_id), where image_id
        is the sha256 digest of the image config blob, or None if the
        image tar has no config

    Raises:
        DockerError: If extraction fails
    """
    rootfs_path = Path(KERF_ROOTFS_DIR) / instance_name
    if rootfs_path.exists():
        shutil.rmtree(rootfs_path)
    rootfs_path.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = Path(tmpdir) / "image.tar"

        resp = _image_request_with_pull("GET", "/images/{name}/get", image_ref)
        with resp, open(tar_path, "wb") as f:
            shutil.copyfileobj(resp, f)

        entrypoint = []
        cmd = []
        image_id = None

        with tarfile.open(tar_path, 'r') as tar:
            manifest_data = None
            for member in tar.getmembers():
                if member.name == "manifest.json":
                    f = tar.extractfile(member)
                    if f:
                        manifest_data = json.load(f)
                    break

            if manifest_data and len(manifest_data) > 0:
                config_file = manifest_data[0].get("Config", "")
                if config_file:
                    for member in tar.getmembers():
                        if member.name == config_file:
                            f = tar.extractfile(member)
                            if f:
                                raw_config = f.read()
                                # The image ID is the digest of the config blob
                                image_id = "sha256:" + hashlib.sha256(raw_config).hexdigest()
                                config = json.loads(raw_config)
                                oci_config = config.get("config", {})
                                entrypoint = oci_config.get("Entrypoint") or []
                                cmd = oci_config.get("Cmd") or []
                            break

                layers = manifest_data[0].get("Layers", [])
                for layer_name in layers:
                    for member in tar.getmembers():
                        if member.name == layer_name:
                            layer_file = tar.extractfile(member)
                            if layer_file:
                                with tarfile.open(fileobj=layer_file, mode='r:*') as layer_tar:
                                    # A rootfs needs full metadata (setuid bits,
                                    # device nodes, absolute symlinks), which the
                                    # Python 3.14 default 'data' filter rejects
                                    layer_tar.extractall(
                                        path=rootfs_path, filter="fully_trusted"
                                    )
                            break

    return str(rootfs_path), entrypoint + cmd, image_id


def get_image_entrypoint(image_ref: str) -> List[str]:
    """
    Get ENTRYPOINT + CMD from image without extracting.

    Args:
        image_ref: Docker image reference (e.g., "nginx:latest")

    Returns:
        List of command components (ENTRYPOINT + CMD)

    Raises:
        DockerError: If image info cannot be retrieved
    """
    resp = _image_request_with_pull("GET", "/images/{name}/json", image_ref)
    with resp:
        try:
            config = json.load(resp)
        except json.JSONDecodeError as e:
            raise DockerError(f"Failed to parse image config: {e}") from e
    oci_config = config.get("Config") or {}
    entrypoint = oci_config.get("Entrypoint") or []
    cmd = oci_config.get("Cmd") or []
    return entrypoint + cmd
