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
Exception classes for kerf validation and processing errors.
"""


class KerfError(Exception):
    """Base exception for all kerf errors."""


class ValidationError(KerfError):
    """Raised when validation fails."""


class ParseError(KerfError):
    """Raised when parsing DTS/DTB fails."""


class ResourceConflictError(ValidationError):
    """Raised when resource conflicts are detected."""


class ResourceExhaustionError(ValidationError):
    """Raised when resources are over-allocated."""


class InvalidReferenceError(ValidationError):
    """Raised when invalid device references are found."""


class KernelInterfaceError(KerfError):
    """Raised when kernel interface operations fail."""


class ResourceError(KerfError):
    """Raised when resource allocation operations fail."""
