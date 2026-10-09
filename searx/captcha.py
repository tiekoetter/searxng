#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Captcha extension points."""

from typing import Any


def handle_captcha(request, secret, *_) -> Any | None:  # pylint: disable=unused-argument
    """Return a captcha response when a request must be challenged."""
    return None


def captcha(request, secret) -> Any | None:  # pylint: disable=unused-argument
    return None
