# Copyright 2026 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from tempest import config

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

OVS_BUILD_INFO_METRIC = metrics_base.OVS_BUILD_INFO_METRIC
OVS_DPDK_INITIALIZED_METRIC = metrics_base.OVS_DPDK_INITIALIZED_METRIC
OVS_BRIDGE_PORT_COUNT_METRIC = metrics_base.OVS_BRIDGE_PORT_COUNT_METRIC
OVS_BRIDGE_FLOW_COUNT_METRIC = metrics_base.OVS_BRIDGE_FLOW_COUNT_METRIC
OVN_NORTHD_STATUS_METRIC = metrics_base.OVN_NORTHD_STATUS_METRIC
OVN_K8S_METRICS_PORT = metrics_base.OVN_K8S_METRICS_PORT
OVN_NORTHD_STATUS_VALUES = metrics_base.OVN_NORTHD_STATUS_VALUES
OVN_NORTHD_STATUS_ACTIVE = metrics_base.OVN_NORTHD_STATUS_ACTIVE
NETWORK_EXPORTER_INSTANCE_PORT = metrics_base.NETWORK_EXPORTER_INSTANCE_PORT
FLOW_COUNT_RE = metrics_base.FLOW_COUNT_RE


class TestBasicInfrastructureMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify basic infrastructure metrics from the network exporter."""

    def _bridges_to_verify_on_hypervisor(self, hypervisor_ip, ovs_bridges):
        """Bridges to check on one hypervisor: OVS discovery ∩ config filter."""
        configured = CONF.nfv_plugin_options.network_exporter_bridges
        if not configured:
            return ovs_bridges
        return sorted(set(ovs_bridges).intersection(configured))

    def _assert_configured_bridges_present_in_ovs(self, hypervisors):
        """Fail if a configured bridge name is absent from OVS on all computes."""
        configured = CONF.nfv_plugin_options.network_exporter_bridges
        if not configured:
            return
        seen_on_any = set()
        for hypervisor_ip in hypervisors:
            seen_on_any.update(
                self._list_ovs_bridges_on_hypervisor(hypervisor_ip))
        missing = sorted(set(configured) - seen_on_any)
        self.assertFalse(
            missing,
            "network_exporter_bridges lists %s but those bridges were not "
            "found via 'ovs-vsctl list-br' on any hypervisor in %s. "
            "Bridges seen in OVS: %s" % (
                missing, hypervisors, sorted(seen_on_any)))

    def _ovs_bridge_port_count(self, bridge, hypervisor_ip):
        """Return the number of ports on a bridge from OVSDB."""
        cmd = ("sudo ovs-vsctl get Bridge %s ports 2>/dev/null | "
               "tr -d '[]' | tr ',' '\\n' | grep -c ." % bridge)
        return int(self._ssh_run_on_hypervisor(hypervisor_ip, cmd).strip())

    def _ovs_bridge_flow_count(self, bridge, hypervisor_ip):
        """Return OpenFlow flow count from aggregate stats on the bridge."""
        ofctl_output = ''
        for cmd in (
                'sudo ovs-ofctl dump-aggregate %s 2>&1' % bridge,
                'sudo ovs-ofctl -O OpenFlow10 dump-aggregate %s 2>&1' % bridge):
            ofctl_output = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
            match = FLOW_COUNT_RE.search(ofctl_output)
            if match:
                return int(match.group(1))

        labels = {'bridge': bridge}
        count = self._prom_compute_metric_value(
            hypervisor_ip, OVS_BRIDGE_FLOW_COUNT_METRIC, labels)
        if count is not None:
            return count

        metrics_output = ''
        for cmd in (
                ("curl -sk https://127.0.0.1:9105/metrics 2>/dev/null | "
                 "grep '^ovs_bridge_flow_count{' | grep 'bridge=\"%s\"'")
                % bridge,
                ("curl -s http://127.0.0.1:9105/metrics 2>/dev/null | "
                 "grep '^ovs_bridge_flow_count{' | grep 'bridge=\"%s\"'")
                % bridge):
            metrics_output = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
            count = self._parse_prom_metric_text(
                metrics_output, OVS_BRIDGE_FLOW_COUNT_METRIC, labels)
            if count is not None:
                return count

        self.fail(
            "Could not determine flow count for bridge '%s' on %s. "
            "ovs-ofctl dump-aggregate output: %r; exporter metrics: %r" % (
                bridge, hypervisor_ip,
                (ofctl_output or '').strip()[:500],
                (metrics_output or '').strip()[:500]))

    def _assert_bridge_metrics_on_hypervisors(self, metric_name, metric_stdout):
        """Verify exporter metrics match OVS for discovered bridges on computes."""
        hypervisors = self._get_ssh_hypervisors(metric_stdout)
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors with %s metrics found (metric output or '
            'Nova hypervisor list)' % NETWORK_EXPORTER_INSTANCE_PORT)
        self._assert_configured_bridges_present_in_ovs(hypervisors)
        self._hypervisor_id_cache = {}
        checked = False
        for hypervisor_ip in hypervisors:
            ovs_bridges = self._list_ovs_bridges_on_hypervisor(hypervisor_ip)
            bridges = self._bridges_to_verify_on_hypervisor(
                hypervisor_ip, ovs_bridges)
            LOG.info(
                "Hypervisor %s OVS bridges %s; verifying %s for metric %s",
                hypervisor_ip, ovs_bridges, bridges, metric_name)
            for bridge in bridges:
                checked = True
                if metric_name == OVS_BRIDGE_PORT_COUNT_METRIC:
                    expected = self._ovs_bridge_port_count(
                        bridge, hypervisor_ip)
                else:
                    expected = self._ovs_bridge_flow_count(
                        bridge, hypervisor_ip)
                reported = self._parse_metric_values_for_bridge(
                    metric_stdout, bridge, hypervisor_ip)
                self.assertNotEmpty(
                    reported,
                    "Bridge '%s' exists on hypervisor %s (ovs-vsctl list-br) "
                    "but metric '%s' has no openstack-network-exporter row "
                    "matching identifiers %s. :9105 instance labels in metric "
                    "output: %s. Bridges matched on this host: %s" % (
                        bridge, hypervisor_ip, metric_name,
                        sorted(self._hypervisor_identifiers(hypervisor_ip)),
                        self._exporter_instance_samples(metric_stdout),
                        self._bridges_reported_for_hypervisor(
                            metric_stdout, hypervisor_ip)))
                self.assertIn(
                    expected, reported,
                    "Metric '%s' on bridge '%s' hypervisor %s: OVS reports %s "
                    "but openstack metric show had %s (stdout excerpt: %s)" % (
                        metric_name, bridge, hypervisor_ip, expected, reported,
                        [line for line in metric_stdout.splitlines()
                         if '|' in line and bridge in line
                         and hypervisor_ip in line][:3]))
                LOG.info(
                    "Metric '%s' bridge '%s' on %s matches OVS count %s",
                    metric_name, bridge, hypervisor_ip, expected)
        self.assertTrue(
            checked,
            'No bridges to verify on any hypervisor (configure '
            'network_exporter_bridges or ensure OVS bridges exist on computes)')

    def test_ovs_build_info_metric(self):
        """Verify ovs_build_info is reported by the network exporter."""
        self._assert_metric_reported(
            OVS_BUILD_INFO_METRIC,
            output_markers=[OVS_BUILD_INFO_METRIC, 'ovs_version'])

    def test_ovs_dpdk_initialized_metric(self):
        """Verify ovs_dpdk_initialized is reported by the network exporter."""
        self._assert_metric_reported(OVS_DPDK_INITIALIZED_METRIC)

    def test_ovs_bridge_port_count_matches_configuration(self):
        """Verify ovs_bridge_port_count matches ovs-vsctl on each bridge."""
        metric_stdout = self._assert_metric_reported(
            OVS_BRIDGE_PORT_COUNT_METRIC)
        self._assert_bridge_metrics_on_hypervisors(
            OVS_BRIDGE_PORT_COUNT_METRIC, metric_stdout)

    def test_ovs_bridge_flow_count_matches_configuration(self):
        """Verify ovs_bridge_flow_count matches ovs-ofctl dump-aggregate."""
        metric_stdout = self._assert_metric_reported(
            OVS_BRIDGE_FLOW_COUNT_METRIC)
        self._assert_bridge_metrics_on_hypervisors(
            OVS_BRIDGE_FLOW_COUNT_METRIC, metric_stdout)

    def test_ovn_northd_status_metric(self):
        """Verify ovn_northd_status is reported and northd is active."""
        metric_stdout = self._assert_metric_reported(OVN_NORTHD_STATUS_METRIC)
        values = self._parse_ovn_k8s_metric_values(metric_stdout)
        self.assertNotEmpty(
            values,
            "Metric '%s' has no OVN metrics rows (%s) in: %s" % (
                OVN_NORTHD_STATUS_METRIC, OVN_K8S_METRICS_PORT, metric_stdout))
        for value in values:
            self.assertIn(
                value, OVN_NORTHD_STATUS_VALUES,
                "Metric '%s' value %s is not a valid northd status "
                "(expected 0=standby, 1=active, 2=paused). All values: %s" % (
                    OVN_NORTHD_STATUS_METRIC, value, values))
        self.assertIn(
            OVN_NORTHD_STATUS_ACTIVE, values,
            "Metric '%s' has no active northd (value 1); reported %s" % (
                OVN_NORTHD_STATUS_METRIC, values))
        LOG.info("Metric '%s' reported values: %s",
                    OVN_NORTHD_STATUS_METRIC, values)
