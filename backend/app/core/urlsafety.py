"""URL safety for anything fetched from a provider-supplied link (docs/threat-model.md T-2)."""

import ipaddress
from urllib.parse import urlparse

_BLOCKED_NAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}


def is_safe_public_https_url(url: str) -> bool:
    """True only for https URLs whose host is a DNS name or a public IP (no private ranges).

    DNS rebinding is not solved here (a hostname can still resolve to a private address); callers
    that fetch must also avoid following redirects to other hosts.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_NAMES or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # a DNS name: needs at least one dot (rejects "intranet")
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)
