"""Failures raised by the parsers and the adapter that are not about reachability or login.

`PortalUnavailableError` and `SessionExpiredError` stay in `app/client.py`, where the v1 code and
tests import them from; this module only holds what the parsers need to raise without importing
the adapter (which would be a cycle).
"""

from __future__ import annotations


class PortalParseError(Exception):
    """The portal answered, but not in a shape we recognise (layout changed, maintenance page)."""


class PortalNotFoundError(Exception):
    """The portal says the thing we asked for does not exist (for example an unknown meter id)."""
