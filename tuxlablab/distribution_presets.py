"""Built-in distribution presets shared by web UI and CLI."""

from __future__ import annotations

from copy import deepcopy


def _rhel_preset(version: str) -> dict[str, str]:
    suffix = version.replace(".", "")
    return {
        "name": f"rhel{suffix}",
        "display_name": f"Red Hat Enterprise Linux {version}",
        "image_file": f"rhel-{version}-x86_64-kvm.qcow2",
        "playbooks": "rh-register.yml",
        "download_url": "",
    }


def _centos_stream_preset(version: str) -> dict[str, str]:
    major = version.split()[0]
    image = f"CentOS-Stream-GenericCloud-{major}-latest.x86_64.qcow2"
    return {
        "name": f"centos{major}",
        "display_name": f"CentOS Stream {major}",
        "image_file": image,
        "playbooks": "",
        "download_url": f"https://cloud.centos.org/centos/{major.lower()}-stream/x86_64/images/{image}",
    }

# family -> version -> preset fields
_PRESETS: dict[str, dict[str, dict[str, str]]] = {
    "RHEL": {
        # Versions derived from Red Hat's public release-date matrix:
        # https://access.redhat.com/articles/red-hat-enterprise-linux-release-dates
        "10.1": _rhel_preset("10.1"),
        "10.0": _rhel_preset("10.0"),
        "9.7": _rhel_preset("9.7"),
        "9.6": _rhel_preset("9.6"),
        "9.5": _rhel_preset("9.5"),
        "9.4": _rhel_preset("9.4"),
        "9.3": _rhel_preset("9.3"),
        "9.2": _rhel_preset("9.2"),
        "9.1": _rhel_preset("9.1"),
        "9.0": _rhel_preset("9.0"),
        "8.10": _rhel_preset("8.10"),
        "8.9": _rhel_preset("8.9"),
        "8.8": _rhel_preset("8.8"),
        "8.7": _rhel_preset("8.7"),
        "8.6": _rhel_preset("8.6"),
        "8.5": _rhel_preset("8.5"),
        "8.4": _rhel_preset("8.4"),
        "8.3": _rhel_preset("8.3"),
        "8.2": _rhel_preset("8.2"),
        "8.1": _rhel_preset("8.1"),
        "8.0": _rhel_preset("8.0"),
    },
    "SUSE": {
        "15": {
            "name": "sles15",
            "display_name": "SUSE Enterprise Linux 15",
            "image_file": "sles15-raw.qcow2",
            "playbooks": "",
            "download_url": "",
        },
    },
    "Rocky": {
        "9": {
            "name": "rocky9",
            "display_name": "Rocky Linux 9",
            "image_file": "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2",
            "playbooks": "",
            "download_url": "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2",
        },
    },
    "OEL": {
        "9.2": {
            "name": "oel92",
            "display_name": "Oracle Enterprise Linux 9.2",
            "image_file": "OL9U2_x86_64-kvm-b197.qcow",
            "playbooks": "",
            "download_url": "",
        },
    },
    "Fedora": {
        "41": {
            "name": "fedora41",
            "display_name": "Fedora Cloud 41",
            "image_file": "Fedora-Cloud-Base-Generic.x86_64-41-1.4.qcow2",
            "playbooks": "",
            "download_url": "",
        },
    },
    "CentOS Stream": {
        # Latest GenericCloud x86_64 image names from the public CentOS image indexes:
        # https://cloud.centos.org/centos/9-stream/x86_64/images/
        # https://cloud.centos.org/centos/10-stream/x86_64/images/
        "10 Stream": _centos_stream_preset("10 Stream"),
        "9 Stream": _centos_stream_preset("9 Stream"),
    },
}


def families() -> list[str]:
    return sorted(_PRESETS.keys())


def versions(family: str) -> list[str]:
    return list(_PRESETS.get(family, {}).keys())


def presets_for_web() -> list[dict[str, object]]:
    """Return presets grouped by family for template rendering."""
    rows: list[dict[str, object]] = []
    for family in families():
        rows.append({"family": family, "versions": versions(family)})
    return rows


def get_preset(family: str, version: str) -> dict[str, str]:
    family_map = _PRESETS.get(family)
    if family_map is None:
        raise ValueError(f"Unsupported distribution family '{family}'")
    preset = family_map.get(version)
    if preset is None:
        raise ValueError(f"Unsupported version '{version}' for family '{family}'")
    return deepcopy(preset)
