#!/usr/bin/env python3
"""Move legacy Kubernetes Service values out of the native FTL tree.

Writes a new YAML document to stdout; never changes the input file or a cluster.
Comments are not retained. Review the output and keep the original as a backup.
"""
import argparse
import copy
from pathlib import Path
import sys

import yaml


def migrate(original):
    result = copy.deepcopy(original)
    if not isinstance(result, dict):
        raise ValueError("Values must be a YAML mapping")
    services = result.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("services must be a mapping")
    paths = {
        "dns": ("pihole", "ftl", "dns"),
        "web": ("pihole", "web"),
        "dhcp": ("pihole", "ftl", "dhcp"),
        "dhcpv6": ("pihole", "ftl", "dhcp", "ipv6"),
        "ntp": ("pihole", "ftl", "ntp"),
    }
    for name, path in paths.items():
        node = result
        for part in path:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict) and "service" in node:
            value = node.pop("service")
            if name in services and services[name] != value:
                raise ValueError(f"Conflicting legacy and services.{name} values; resolve explicitly")
            services[name] = value
    if services:
        result["services"] = services
    dhcp = result.get("pihole", {}).get("ftl", {}).get("dhcp", {})
    ipv6 = dhcp.get("ipv6")
    if isinstance(ipv6, dict):
        if set(ipv6) - {"active"}:
            raise ValueError("Unknown legacy dhcp.ipv6 keys")
        enabled = ipv6.get("active", False)
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("dhcp.ipv6.active must be boolean or null")
        dhcp["ipv6"] = enabled
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("values", type=Path)
    args = parser.parse_args()
    try:
        result = migrate(yaml.safe_load(args.values.read_text()))
    except (ValueError, yaml.YAMLError) as error:
        parser.exit(1, f"Migration failed: {error}\n")
    sys.stdout.write(yaml.safe_dump(result, sort_keys=False))


if __name__ == "__main__":
    main()
