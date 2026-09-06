"""In-tree PEP 517 backend: build the XDP objects, then hand over to poetry-core.

The objects are stable-ABI BPF, independent of the Python version and the
kernel, so the wheel stays pure. They are compiled once, here, on a machine
with clang; the device-kernel never needs a compiler.
"""
import subprocess
from pathlib import Path

from poetry.core.masonry import api as _poetry

get_requires_for_build_wheel = _poetry.get_requires_for_build_wheel
get_requires_for_build_sdist = _poetry.get_requires_for_build_sdist
get_requires_for_build_editable = _poetry.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _poetry.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _poetry.prepare_metadata_for_build_editable
build_sdist = _poetry.build_sdist


def _build_objects():
    subprocess.run(["make", "-s", "-C", str(Path(__file__).parent / "src/kerf/xdp/bpf")],
                   check=True)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _build_objects()
    return _poetry.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    _build_objects()
    return _poetry.build_editable(wheel_directory, config_settings, metadata_directory)
