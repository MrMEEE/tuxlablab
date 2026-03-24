"""Red Hat CDN – certificate-authenticated image download.

Authentication
--------------
Downloads from the Red Hat CDN use entitlement certificate authentication.
On a system registered with subscription-manager, the certificates are at:

    CA cert : /etc/rhsm/ca/redhat-uep.pem
    Cert    : /etc/pki/entitlement/<id>.pem        (auto-discovered)
    Key     : /etc/pki/entitlement/<id>-key.pem    (auto-discovered)

You can override these paths in the tuxlablab Settings page via:

    rhn_ca_cert          — path to the CA certificate
    rhn_entitlement_cert — path to the entitlement certificate (.pem)
    rhn_entitlement_key  — path to the entitlement private key (-key.pem)

Reference: https://access.redhat.com/solutions/4004591
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# CDN / certificate defaults
# ---------------------------------------------------------------------------

_CDN_BASE = "https://cdn.redhat.com"
_DEFAULT_CA_CERT = "/etc/rhsm/ca/redhat-uep.pem"
_DEFAULT_ENTITLEMENT_DIR = "/etc/pki/entitlement"


class RHDownloadError(Exception):
    """Raised when Red Hat certificate lookup or URL construction fails."""


# ---------------------------------------------------------------------------
# Certificate discovery
# ---------------------------------------------------------------------------


def _find_entitlement_files() -> tuple[str, str]:
    """Auto-discover the entitlement cert and key from /etc/pki/entitlement/.

    Returns (cert_path, key_path).
    Raises :exc:`RHDownloadError` if no entitlement certificates are found.
    """
    ent_dir = Path(_DEFAULT_ENTITLEMENT_DIR)
    if not ent_dir.is_dir():
        raise RHDownloadError(
            f"Entitlement directory {_DEFAULT_ENTITLEMENT_DIR!r} not found. "
            "Is this system registered with subscription-manager? "
            "Alternatively, set rhn_entitlement_cert and rhn_entitlement_key in Settings."
        )
    keys = sorted(ent_dir.glob("*-key.pem"))
    if not keys:
        raise RHDownloadError(
            f"No entitlement key found in {_DEFAULT_ENTITLEMENT_DIR}. "
            "Run 'subscription-manager register' or set rhn_entitlement_key in Settings."
        )
    key_path = keys[0]
    cert_path = ent_dir / key_path.name.replace("-key.pem", ".pem")
    if not cert_path.is_file():
        raise RHDownloadError(
            f"Entitlement cert not found at {cert_path}. "
            "Set rhn_entitlement_cert in Settings."
        )
    return str(cert_path), str(key_path)


# ---------------------------------------------------------------------------
# Image URL + cert resolution
# ---------------------------------------------------------------------------


def get_rhel_kvm_download_info(
    rhel_version: str = "9.7",
    ca_cert: str = "",
    cert: str = "",
    key: str = "",
) -> dict:
    """Return the CDN download URL and certificate paths for the RHEL KVM guest image.

    Returns a dict::

        {"url": str, "ca_cert": str, "cert": str, "key": str}

    Certificate paths fall back to the standard subscription-manager locations
    when not provided.  Raises :exc:`RHDownloadError` if certs cannot be located.
    """
    resolved_cert = cert.strip()
    resolved_key = key.strip()
    if not resolved_cert or not resolved_key:
        resolved_cert, resolved_key = _find_entitlement_files()

    resolved_ca = ca_cert.strip() or _DEFAULT_CA_CERT

    # CDN path pattern: /content/dist/rhel{major}/{version}/x86_64/baseos/images/
    major = rhel_version.split(".")[0]
    filename = f"rhel-{rhel_version}-x86_64-kvm.qcow2"
    url = f"{_CDN_BASE}/content/dist/rhel{major}/{rhel_version}/x86_64/baseos/images/{filename}"

    return {"url": url, "ca_cert": resolved_ca, "cert": resolved_cert, "key": resolved_key}


def rhel_version_from_filename(image_file: str) -> str:
    """Extract the RHEL version string from an image filename.

    >>> rhel_version_from_filename("rhel-9.7-x86_64-kvm.qcow2")
    '9.7'
    """
    m = re.search(r"rhel-(\d+\.\d+)", image_file)
    return m.group(1) if m else "9.7"
