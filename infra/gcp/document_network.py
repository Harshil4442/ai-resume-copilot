"""Fixed private document-call routing contract; no cloud calls or activation.

Agreement with metadata is necessary but never proves native HTTP connectivity,
worker isolation, identity, firewall policy, or successful document processing.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any

PROJECT = "ai-resume-parser-482412"
REGION = "us-central1"
NETWORK = "hirewiz-private"
SUBNET = "hirewiz-callers-us-central1"
IP_RANGE = "10.42.0.0/24"
DNS_ZONE = "hirewiz-private-run-app"
DNS_NAME = "run.app."
PRIVATE_GOOGLE_IPS = ("199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11")
NETWORK_PATH = f"projects/{PROJECT}/global/networks/{NETWORK}"
SUBNET_PATH = f"projects/{PROJECT}/regions/{REGION}/subnetworks/{SUBNET}"
NETWORK_URL = f"https://www.googleapis.com/compute/v1/{NETWORK_PATH}"
SUBNET_URL = f"https://www.googleapis.com/compute/v1/{SUBNET_PATH}"
REGION_URL = f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/regions/{REGION}"
GATEWAY_URL = f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/global/gateways/default-internet-gateway"
_DENIED = "document_private_network_configuration_unverified"


class NetworkConfigurationDenied(ValueError):
    """A fixed reason which does not echo untrusted configuration values."""


def caller_deploy_args() -> list[str]:
    return [f"--network={NETWORK}", f"--subnet={SUBNET}", "--vpc-egress=private-ranges-only"]


def caller_vpc_access() -> dict[str, Any]:
    return {"egress": "PRIVATE_RANGES_ONLY",
            "networkInterfaces": [{"network": NETWORK, "subnetwork": SUBNET}]}


def caller_annotations() -> dict[str, str]:
    return {
        "run.googleapis.com/network-interfaces": json.dumps(
            [{"network": NETWORK, "subnetwork": SUBNET}], separators=(",", ":")),
        "run.googleapis.com/vpc-access-egress": "private-ranges-only",
    }


def _reference(value: Any, name: str, path: str, url: str) -> bool:
    return type(value) is str and (value in (name, path) or _resource_url(value, url))


def _resource_url(value: Any, expected: str) -> bool:
    """Only the two Google spellings of this fixed full resource are accepted."""
    alternate = expected.replace("https://www.googleapis.com/compute/v1/",
                                 "https://compute.googleapis.com/compute/v1/", 1)
    return type(value) is str and value in (expected, alternate)


def _private_visibility(value: Any) -> bool:
    if type(value) is not dict or set(value) - {"kind", "networks"}:
        return False
    if value.get("kind", "dns#managedZonePrivateVisibilityConfig") != "dns#managedZonePrivateVisibilityConfig":
        return False
    networks = value.get("networks")
    if type(networks) is not list or len(networks) != 1 or type(networks[0]) is not dict:
        return False
    row = networks[0]
    return (not set(row) - {"kind", "networkUrl"}
            and row.get("kind", "dns#managedZonePrivateVisibilityConfigNetwork")
            == "dns#managedZonePrivateVisibilityConfigNetwork"
            and _resource_url(row.get("networkUrl"), NETWORK_URL))


def _interfaces(value: Any) -> bool:
    if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
        return False
    row = value[0]
    return (set(row) == {"network", "subnetwork"}
            and _reference(row["network"], NETWORK, NETWORK_PATH, NETWORK_URL)
            and _reference(row["subnetwork"], SUBNET, SUBNET_PATH, SUBNET_URL))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NetworkConfigurationDenied(_DENIED)
        result[key] = value
    return result


def validate_caller_network(template: Any) -> list[str]:
    """Check a Cloud Run v2 template or complete v1 revision; no HTTP proof."""
    try:
        if type(template) is not dict:
            return [_DENIED]
        if "vpcAccess" in template:
            access = template["vpcAccess"]
            valid = (type(access) is dict and set(access) == {"egress", "networkInterfaces"}
                     and access["egress"] == "PRIVATE_RANGES_ONLY"
                     and _interfaces(access["networkInterfaces"]))
            return [] if valid else [_DENIED]
        metadata = template.get("metadata")
        annotations = metadata.get("annotations") if type(metadata) is dict else None
        if type(annotations) is not dict or annotations.get("run.googleapis.com/vpc-access-connector"):
            return [_DENIED]
        raw = annotations.get("run.googleapis.com/network-interfaces")
        if type(raw) is not str or len(raw.encode("utf-8")) > 4096:
            return [_DENIED]
        interfaces = json.loads(raw, object_pairs_hook=_unique_pairs)
        return ([] if annotations.get("run.googleapis.com/vpc-access-egress") == "private-ranges-only"
                and _interfaces(interfaces) else [_DENIED])
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return [_DENIED]


def verify_caller(template: Any) -> None:
    if validate_caller_network(template):
        raise NetworkConfigurationDenied(_DENIED)


def _network_metadata(network: Any, subnet: Any, zone: Any) -> bool:
    if any(type(item) is not dict for item in (network, subnet, zone)):
        return False
    visibility = zone.get("privateVisibilityConfig")
    return (
        network.get("name") == NETWORK and _resource_url(network.get("selfLink"), NETWORK_URL)
        and network.get("autoCreateSubnetworks") is False
        and not network.get("peerings")
        and subnet.get("name") == SUBNET and _resource_url(subnet.get("selfLink"), SUBNET_URL)
        and _resource_url(subnet.get("network"), NETWORK_URL)
        and _resource_url(subnet.get("region"), REGION_URL)
        and subnet.get("ipCidrRange") == IP_RANGE and subnet.get("privateIpGoogleAccess") is True
        and subnet.get("stackType", "IPV4_ONLY") == "IPV4_ONLY"
        and not subnet.get("secondaryIpRanges")
        and zone.get("name") == DNS_ZONE and zone.get("dnsName") == DNS_NAME
        and zone.get("visibility") == "private" and _private_visibility(visibility)
        and not zone.get("forwardingConfig") and not zone.get("peeringConfig")
    )


def _dns_records(records: Any) -> bool:
    if type(records) is not list or not 2 <= len(records) <= 4:
        return False
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        if type(row) is not dict or type(row.get("name")) is not str or type(row.get("type")) is not str:
            return False
        key = (row["name"], row["type"])
        if key in targets:
            return False
        if key in ((DNS_NAME, "NS"), (DNS_NAME, "SOA")):
            targets[key] = row
            continue
        if key not in ((DNS_NAME, "A"), ("*." + DNS_NAME, "CNAME")):
            return False
        targets[key] = row
    apex = targets.get((DNS_NAME, "A"), {})
    wildcard = targets.get(("*." + DNS_NAME, "CNAME"), {})
    ips = apex.get("rrdatas")
    return (type(ips) is list and len(ips) == 4 and all(type(ip) is str for ip in ips)
            and set(ips) == set(PRIVATE_GOOGLE_IPS)
            and wildcard.get("rrdatas") == [DNS_NAME]
            and type(apex.get("ttl")) is int and apex["ttl"] == 300
            and type(wildcard.get("ttl")) is int and wildcard["ttl"] == 300
            and not apex.get("routingPolicy") and not wildcard.get("routingPolicy"))


def _google_route(routes: Any) -> bool:
    if type(routes) is not list or not 1 <= len(routes) <= 64:
        return False
    found = False
    for row in routes:
        if type(row) is not dict or not _resource_url(row.get("network"), NETWORK_URL):
            return False
        destination = row.get("destRange")
        if type(destination) is not str or len(destination) > 18:
            return False
        address_range = ipaddress.IPv4Network(destination, strict=True)
        # A narrower overriding VIP route is not accepted even if a default
        # route exists. Full effective routing/firewall acceptance remains native.
        if destination in ("0.0.0.0/0", "199.36.153.8/30"):
            if not _resource_url(row.get("nextHopGateway"), GATEWAY_URL):
                return False
            found = True
        elif address_range.overlaps(ipaddress.IPv4Network("199.36.153.8/30")):
            return False
    return found


def validate_network_metadata(network: Any, subnet: Any, zone: Any,
                              records: Any, routes: Any) -> list[str]:
    """Compare native Compute/Cloud DNS GET bodies; connectivity remains unproven."""
    try:
        return ([] if _network_metadata(network, subnet, zone)
                and _dns_records(records) and _google_route(routes) else [_DENIED])
    except (TypeError, ValueError, RecursionError):
        return [_DENIED]


def verify_network(network: Any, subnet: Any, zone: Any, records: Any, routes: Any) -> None:
    if validate_network_metadata(network, subnet, zone, records, routes):
        raise NetworkConfigurationDenied(_DENIED)


def plan() -> dict[str, Any]:
    """Preparation only. A plan never establishes resource existence or API state."""
    return {
        "project": PROJECT, "region": REGION, "network": NETWORK, "subnet": SUBNET,
        "ip_range": IP_RANGE, "private_google_access": True, "dns_zone": DNS_ZONE,
        "dns_name": DNS_NAME, "private_google_ips": list(PRIVATE_GOOGLE_IPS),
        "caller_deploy_args": caller_deploy_args(), "caller_vpc_access": caller_vpc_access(),
        "worker_vpc_attachment": False, "worker_ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "connector_required": False, "cloud_nat_required": False,
        "api_enablement_proven": False, "resources_exist_proven": False,
        "native_http_acceptance_proven": False, "native_calls": 0,
    }
