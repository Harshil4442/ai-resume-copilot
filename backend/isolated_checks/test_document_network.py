"""Pure routing contract controls; direct execution imports no app or conftest."""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "infra/gcp/document_network.py"
SPEC = importlib.util.spec_from_file_location("isolated_document_network", SOURCE)
routing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(routing)


def fixture():
    network = {"name": routing.NETWORK, "selfLink": routing.NETWORK_URL,
               "autoCreateSubnetworks": False}
    subnet = {"name": routing.SUBNET, "selfLink": routing.SUBNET_URL,
              "network": routing.NETWORK_URL, "region": routing.REGION_URL,
              "ipCidrRange": routing.IP_RANGE, "privateIpGoogleAccess": True}
    zone = {"name": routing.DNS_ZONE, "dnsName": routing.DNS_NAME, "visibility": "private",
            "privateVisibilityConfig": {"networks": [{"networkUrl": routing.NETWORK_URL}]}}
    records = [{"name": routing.DNS_NAME, "type": "A", "ttl": 300,
                "rrdatas": list(routing.PRIVATE_GOOGLE_IPS)},
               {"name": "*." + routing.DNS_NAME, "type": "CNAME", "ttl": 300,
                "rrdatas": [routing.DNS_NAME]}]
    routes = [{"network": routing.NETWORK_URL, "destRange": "0.0.0.0/0",
               "nextHopGateway": routing.GATEWAY_URL}]
    return network, subnet, zone, records, routes


class DocumentNetworkTests(unittest.TestCase):
    def test_v1_and_v2_exact_caller_configurations_agree(self):
        routing.verify_caller({"vpcAccess": routing.caller_vpc_access()})
        routing.verify_caller({"metadata": {"annotations": routing.caller_annotations()}})
        access = routing.caller_vpc_access()
        access["networkInterfaces"][0] = {"network": routing.NETWORK_PATH,
                                          "subnetwork": routing.SUBNET_URL}
        routing.verify_caller({"vpcAccess": access})

    def test_callers_refuse_public_only_all_traffic_connector_and_foreign_network(self):
        invalid = [{}, {"vpcAccess": {}}, {"vpcAccess": {"connector": "old"}}]
        for field, value in (("egress", "ALL_TRAFFIC"), ("connector", "old")):
            access = routing.caller_vpc_access()
            access[field] = value
            invalid.append({"vpcAccess": access})
        for field, value in (("network", "foreign"), ("subnetwork", "foreign"), ("tags", ["tag"])):
            access = routing.caller_vpc_access()
            access["networkInterfaces"][0][field] = value
            invalid.append({"vpcAccess": access})
        for actual in invalid:
            with self.subTest(actual=actual):
                self.assertTrue(routing.validate_caller_network(actual))

    def test_v1_refuses_duplicate_json_missing_egress_and_multiple_interfaces(self):
        for raw in ('[{"network":"hirewiz-private","network":"foreign",'
                    '"subnetwork":"hirewiz-callers-us-central1"}]', "[]", "not-json",
                    json.dumps(routing.caller_vpc_access()["networkInterfaces"] * 2)):
            annotations = routing.caller_annotations()
            annotations["run.googleapis.com/network-interfaces"] = raw
            self.assertTrue(routing.validate_caller_network({"metadata": {"annotations": annotations}}))
        for key, value in (("run.googleapis.com/vpc-access-egress", "all-traffic"),
                           ("run.googleapis.com/vpc-access-connector", "old")):
            annotations = routing.caller_annotations()
            annotations[key] = value
            self.assertTrue(routing.validate_caller_network({"metadata": {"annotations": annotations}}))

    def test_network_dns_route_fixture_and_native_default_records_agree(self):
        values = fixture()
        routing.verify_network(*values)
        values[3].extend([{"name": routing.DNS_NAME, "type": "NS"},
                          {"name": routing.DNS_NAME, "type": "SOA"}])
        values[3][0]["rrdatas"].reverse()
        routing.verify_network(*values)

    def test_actual_dns_metadata_kinds_and_compute_host_spelling_agree(self):
        values = fixture()
        compute_url = routing.NETWORK_URL.replace("www.googleapis.com", "compute.googleapis.com")
        values[2]["privateVisibilityConfig"] = {
            "kind": "dns#managedZonePrivateVisibilityConfig",
            "networks": [{"kind": "dns#managedZonePrivateVisibilityConfigNetwork",
                          "networkUrl": compute_url}],
        }
        routing.verify_network(*values)
        access = routing.caller_vpc_access()
        access["networkInterfaces"][0]["network"] = compute_url
        routing.verify_caller({"vpcAccess": access})

    def test_dns_resource_alias_does_not_allow_foreign_or_extra_authority(self):
        for target in (
            routing.NETWORK_URL.replace("www.googleapis.com", "compute.googleapis.com.evil.test"),
            routing.NETWORK_URL.replace(routing.PROJECT, "foreign-project"),
            routing.NETWORK_URL.replace(routing.NETWORK, "foreign-network"),
            routing.NETWORK_URL + "?alternate=1",
        ):
            values = fixture()
            values[2]["privateVisibilityConfig"]["networks"][0]["networkUrl"] = target
            self.assertTrue(routing.validate_network_metadata(*values))
        for container, key, value in (
            ("visibility", "kind", "wrong-kind"),
            ("visibility", "gkeClusters", [{"gkeClusterName": "foreign"}]),
            ("network", "kind", "wrong-kind"),
            ("network", "otherNetworkUrl", routing.NETWORK_URL),
        ):
            values = fixture()
            visibility = values[2]["privateVisibilityConfig"]
            target = visibility if container == "visibility" else visibility["networks"][0]
            target[key] = value
            self.assertTrue(routing.validate_network_metadata(*values))
        values = fixture()
        values[2]["privateVisibilityConfig"]["networks"].append({"networkUrl": routing.NETWORK_URL})
        self.assertTrue(routing.validate_network_metadata(*values))

    def test_each_missing_or_cross_project_native_resource_is_refused(self):
        mutations = [(0, "autoCreateSubnetworks", True), (0, "selfLink", "foreign"),
                     (0, "peerings", [{"name": "foreign"}]), (1, "region", "foreign"),
                     (1, "network", "foreign"), (1, "privateIpGoogleAccess", 1),
                     (1, "privateIpGoogleAccess", False), (1, "ipCidrRange", "10.42.0.0/26"),
                     (1, "secondaryIpRanges", [{"rangeName": "extra"}]),
                     (2, "visibility", "public"), (2, "name", "foreign"),
                     (2, "privateVisibilityConfig", {"networks": [{"networkUrl": "foreign"}]}),
                     (2, "forwardingConfig", {"targetNameServers": [{"ipv4Address": "10.0.0.1"}]})]
        for position, field, value in mutations:
            values = fixture()
            values[position][field] = value
            with self.subTest(field=field):
                self.assertTrue(routing.validate_network_metadata(*values))
        for position in range(5):
            values = list(fixture())
            values[position] = None
            self.assertTrue(routing.validate_network_metadata(*values))

    def test_dns_cannot_hide_public_or_restricted_or_host_override(self):
        for ips in (["199.36.153.4", "199.36.153.5", "199.36.153.6", "199.36.153.7"],
                    ["199.36.153.8"] * 4, list(routing.PRIVATE_GOOGLE_IPS) + ["8.8.8.8"]):
            values = fixture()
            values[3][0]["rrdatas"] = ips
            self.assertTrue(routing.validate_network_metadata(*values))
        for record in ({"name": "worker.run.app.", "type": "A", "ttl": 300, "rrdatas": ["8.8.8.8"]},
                       {"name": "run.app.", "type": "AAAA", "ttl": 300, "rrdatas": ["::1"]}):
            values = fixture()
            values[3].append(record)
            self.assertTrue(routing.validate_network_metadata(*values))
        values = fixture()
        values[3].append(copy.deepcopy(values[3][0]))
        self.assertTrue(routing.validate_network_metadata(*values))

    def test_default_or_specific_vip_route_must_use_google_gateway(self):
        values = fixture()
        values[4][0]["destRange"] = "199.36.153.8/30"
        routing.verify_network(*values)
        values[4][0]["nextHopGateway"] = "foreign"
        self.assertTrue(routing.validate_network_metadata(*values))
        values = fixture()
        values[4].append({"network": routing.NETWORK_URL, "destRange": "199.36.153.8/32",
                          "nextHopGateway": routing.GATEWAY_URL})
        self.assertTrue(routing.validate_network_metadata(*values))
        for destination in ("199.36.0.0/16", "128.0.0.0/1", "not-a-range"):
            values = fixture()
            values[4].append({"network": routing.NETWORK_URL, "destRange": destination,
                              "nextHopInstance": "foreign"})
            self.assertTrue(routing.validate_network_metadata(*values))

    def test_bounded_native_lists_do_not_silently_hide_extra_rows(self):
        values = fixture()
        values[4].extend(copy.deepcopy(values[4]) * 64)
        self.assertTrue(routing.validate_network_metadata(*values))
        values = fixture()
        values[3].extend([{"name": routing.DNS_NAME, "type": "NS"},
                          {"name": routing.DNS_NAME, "type": "SOA"},
                          {"name": "other.run.app.", "type": "A"}])
        self.assertTrue(routing.validate_network_metadata(*values))

    def test_preparation_never_claims_native_acceptance_or_resources(self):
        result = routing.plan()
        self.assertEqual(result["caller_deploy_args"], ["--network=hirewiz-private",
                         "--subnet=hirewiz-callers-us-central1", "--vpc-egress=private-ranges-only"])
        for field in ("api_enablement_proven", "resources_exist_proven", "native_http_acceptance_proven",
                      "worker_vpc_attachment", "connector_required", "cloud_nat_required"):
            self.assertIs(result[field], False)
        self.assertEqual(result["native_calls"], 0)
        with self.assertRaisesRegex(routing.NetworkConfigurationDenied,
                                    "^document_private_network_configuration_unverified$"):
            routing.verify_caller({"vpcAccess": {"connector": "secret-value"}})


if __name__ == "__main__":
    unittest.main()
