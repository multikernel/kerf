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
Tests for Docker image extraction.
"""

import hashlib
import io
import json
import tarfile

from kerf.docker.image import extract_image


def make_image_tar(config: dict, layer_files: dict) -> bytes:
    """Build a docker-save style tar with one layer."""
    config_bytes = json.dumps(config).encode("utf-8")
    config_name = "abc123.json"

    layer_buf = io.BytesIO()
    with tarfile.open(fileobj=layer_buf, mode="w") as layer_tar:
        for name, content in layer_files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            layer_tar.addfile(info, io.BytesIO(content))

    manifest = [{"Config": config_name, "Layers": ["layer1/layer.tar"]}]

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in [
            ("manifest.json", json.dumps(manifest).encode("utf-8")),
            (config_name, config_bytes),
            ("layer1/layer.tar", layer_buf.getvalue()),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def install_fake_docker(monkeypatch, tmp_path, tar_bytes):
    monkeypatch.setattr("kerf.docker.image.KERF_ROOTFS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "kerf.docker.image._image_request_with_pull",
        lambda method, path, image_ref: io.BytesIO(tar_bytes),
    )


class TestExtractImage:
    def _config(self):
        return {"config": {"Entrypoint": ["/entry"], "Cmd": ["arg"]}}

    def test_returns_image_id_digest_of_config(self, monkeypatch, tmp_path):
        config = self._config()
        tar_bytes = make_image_tar(config, {"etc/hostname": b"web\n"})
        install_fake_docker(monkeypatch, tmp_path, tar_bytes)

        _, _, image_id = extract_image("nginx:latest", "web-server")

        expected = hashlib.sha256(json.dumps(config).encode("utf-8")).hexdigest()
        assert image_id == f"sha256:{expected}"

    def test_extracts_rootfs_and_entrypoint(self, monkeypatch, tmp_path):
        tar_bytes = make_image_tar(self._config(), {"etc/hostname": b"web\n"})
        install_fake_docker(monkeypatch, tmp_path, tar_bytes)

        rootfs_path, entrypoint_cmd, _ = extract_image("nginx:latest", "web-server")

        assert rootfs_path == str(tmp_path / "web-server")
        assert (tmp_path / "web-server" / "etc" / "hostname").read_bytes() == b"web\n"
        assert entrypoint_cmd == ["/entry", "arg"]
