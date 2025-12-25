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
OCI image extraction and configuration parsing using skopeo.
"""

import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import List, Tuple

KERF_ROOTFS_DIR = "/var/lib/kerf/rootfs"


class DockerError(Exception):
    """Exception raised for image extraction errors."""


def _normalize_image_ref(image_ref: str) -> str:
    """Normalize image reference for skopeo."""
    if "://" in image_ref:
        return image_ref
    if "/" not in image_ref:
        return f"docker://docker.io/library/{image_ref}"
    if not image_ref.startswith("docker.io/") and image_ref.count("/") == 1:
        return f"docker://docker.io/{image_ref}"
    return f"docker://{image_ref}"


def _check_tool_available(tool: str) -> bool:
    """Check if a command-line tool is available."""
    return shutil.which(tool) is not None


def extract_image(image_ref: str, instance_name: str) -> Tuple[str, List[str]]:
    """
    Extract OCI image filesystem to a directory using skopeo.

    Args:
        image_ref: Docker image reference (e.g., "nginx:latest")
        instance_name: Instance name for directory naming

    Returns:
        Tuple of (rootfs_path, entrypoint_cmd)

    Raises:
        DockerError: If extraction fails
    """
    if not _check_tool_available("skopeo"):
        raise DockerError(
            "skopeo not installed. Install with: yum install skopeo"
        )

    rootfs_path = Path(KERF_ROOTFS_DIR) / instance_name
    if rootfs_path.exists():
        shutil.rmtree(rootfs_path)
    rootfs_path.mkdir(parents=True, exist_ok=True)

    normalized_ref = _normalize_image_ref(image_ref)

    with tempfile.TemporaryDirectory() as tmpdir:
        tar_path = Path(tmpdir) / "image.tar"

        try:
            subprocess.run(
                ["skopeo", "copy", normalized_ref, f"docker-archive:{tar_path}"],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            raise DockerError(f"skopeo copy failed: {e.stderr}") from e

        entrypoint = []
        cmd = []

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
                                config = json.load(f)
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
                                    layer_tar.extractall(path=rootfs_path)
                            break

    return str(rootfs_path), entrypoint + cmd


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
    if not _check_tool_available("skopeo"):
        raise DockerError(
            "skopeo not installed. Install with: yum install skopeo"
        )

    normalized_ref = _normalize_image_ref(image_ref)
    try:
        result = subprocess.run(
            ["skopeo", "inspect", normalized_ref],
            check=True,
            capture_output=True,
            text=True
        )
        config = json.loads(result.stdout)
        entrypoint = config.get("config", {}).get("Entrypoint") or []
        cmd = config.get("config", {}).get("Cmd") or []
        return entrypoint + cmd
    except subprocess.CalledProcessError as e:
        raise DockerError(f"skopeo inspect failed: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise DockerError(f"Failed to parse image config: {e}") from e
