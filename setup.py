#!/usr/bin/env python3
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
Setup script for kerf development environment.
"""

import subprocess
import sys
from pathlib import Path


def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"Running: {description}")
    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print(f"✓ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ {description} failed:")
        print(f"  Error: {e}")
        if e.stdout:
            print(f"  stdout: {e.stdout}")
        if e.stderr:
            print(f"  stderr: {e.stderr}")
        return False


def main():
    """Set up the development environment."""
    print("Setting up kerf development environment...")
    print("=" * 50)

    # Check if we're in the right directory
    if not Path("pyproject.toml").exists():
        print("Error: pyproject.toml not found. Please run this script from the project root.")
        return 1

    # Install dependencies
    print("\n1. Installing dependencies...")
    if not run_command("pip install -e .", "Installing kerf in development mode"):
        return 1

    # Install development dependencies
    print("\n2. Installing development dependencies...")
    cmd = "pip install pytest pytest-cov black flake8 mypy"
    if not run_command(cmd, "Installing dev dependencies"):
        return 1

    # Run tests
    print("\n3. Running tests...")
    if not run_command("python -m pytest tests/", "Running test suite"):
        return 1

    # Check code formatting
    print("\n4. Checking code formatting...")
    if not run_command("black --check src/kerf/", "Checking code formatting"):
        print("  Note: Run 'black src/kerf/' to fix formatting issues")

    # Run linting
    print("\n5. Running linting...")
    if not run_command("flake8 src/kerf/", "Running flake8 linting"):
        print("  Note: Fix linting issues before committing")

    print("\n" + "=" * 50)
    print("✓ Development environment setup complete!")
    print("\nNext steps:")
    print("1. Run 'python -m pytest tests/' to test the implementation")
    print("2. Run 'kerf --help' to see available commands")
    print("3. Try: kerf dtc --input=examples/system.dts --output-dir=build/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
