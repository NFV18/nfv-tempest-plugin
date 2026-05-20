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

import re
import time

from kubernetes.client.rest import ApiException
from tempest import config

from nfv_tempest_plugin.tests.common import k8s
from nfv_tempest_plugin.tests.common import shell_utilities as shell_utils
from nfv_tempest_plugin.tests.scenario import base_test
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

METRIC_RETRY_ATTEMPTS = 6
METRIC_RETRY_INTERVAL = 30
OVS_BUILD_INFO_METRIC = 'ovs_build_info'
OVS_DPDK_INITIALIZED_METRIC = 'ovs_dpdk_initialized'
OVS_BRIDGE_PORT_COUNT_METRIC = 'ovs_bridge_port_count'
OVS_BRIDGE_FLOW_COUNT_METRIC = 'ovs_bridge_flow_count'
OVN_NORTHD_STATUS_METRIC = 'ovn_northd_status'
# OVN/K8s service metrics (northd, controller, etc.), not compute :9105
OVN_K8S_METRICS_PORT = ':1981'
# openstack-network-exporter: 0=standby, 1=active, 2=paused
OVN_NORTHD_STATUS_VALUES = (0, 1, 2)
OVN_NORTHD_STATUS_ACTIVE = 1
OPENSTACK_NAMESPACE = 'openstack'
OPENSTACK_CLIENT_POD = 'openstackclient'
OPENSTACK_CLIENT_CONTAINER = 'openstackclient'
NETWORK_EXPORTER_INSTANCE_PORT = ':9105'
FLOW_COUNT_RE = re.compile(r'flow_count=(\d+)', re.IGNORECASE)
METRIC_ROW_VALUE_RE = re.compile(r'(\d+)\s*\|?\s*$')
COMPUTE_METRICS_HOST_RE = re.compile(
    r'(\d+\.\d+\.\d+\.\d+)' + re.escape(NETWORK_EXPORTER_INSTANCE_PORT))
SSH_CONNECT_TIMEOUT = 30


class TestBasicInfrastructureMetrics(base_test.BaseTest):
    """Verify basic infrastructure metrics from the network exporter."""

    def __init__(self, *args, **kwargs):
        super(TestBasicInfrastructureMetrics, self).__init__(*args, **kwargs)
        self.k8s_client = k8s.openshift_client()
        self._hypervisor_id_cache = {}

    def _metric_show(self, metric_name):
        """Run openstack metric show in the openstackclient pod.

        :param metric_name: Name of the metric to look up
        :return: Tuple of (stdout, stderr, returncode)
        """
        cmd = 'openstack metric show %s --disable-rbac' % metric_name
        LOG.info("Executing in pod %s/%s: %s",
                 OPENSTACK_NAMESPACE, OPENSTACK_CLIENT_POD, cmd)
        try:
            stdout = self.k8s_client.execute_command_in_pod(
                OPENSTACK_CLIENT_POD, OPENSTACK_NAMESPACE,
                OPENSTACK_CLIENT_CONTAINER, cmd)
            return stdout or '', '', 0
        except ApiException as exc:
            msg = 'kubernetes API %s: %s' % (exc.status, exc.body or exc.reason)
            LOG.warning("Pod exec API error: %s", msg)
            return '', msg, 1
        except Exception as exc:
            return '', str(exc), 1

    def _assert_metric_reported(self, metric_name, output_markers=None):
        """Wait until openstack metric show succeeds for metric_name.

        :param metric_name: Gnocchi/Prometheus metric name
        :param output_markers: Optional strings that must appear in stdout
        :raises: AssertionError if the metric is not reported in time
        :return: Command stdout on success
        """
        if output_markers is None:
            output_markers = [metric_name]
        stdout = stderr = ''
        returncode = 1
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            stdout, stderr, returncode = self._metric_show(metric_name)
            stdout = stdout or ''
            if returncode == 0 and metric_name in stdout:
                missing = [m for m in output_markers if m not in stdout]
                if not missing:
                    self.assertTrue(stdout.strip(),
                                    'openstack metric show returned empty '
                                    'output for %s' % metric_name)
                    LOG.warning("Metric '%s' is reported (%s bytes)",
                                metric_name, len(stdout))
                    return stdout
            LOG.warning("Attempt %s/%s for metric '%s' failed: exit %s, "
                        "stderr: %s", attempt + 1, METRIC_RETRY_ATTEMPTS,
                        metric_name, returncode, stderr)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        stdout = stdout or ''
        msg = ("Metric '%s' not found or openstack command failed "
               "(exit %s). stderr: %s stdout: %s" %
               (metric_name, returncode, stderr, stdout))
        self.assertEqual(0, returncode, msg)
        self.assertIn(metric_name, stdout,
                      "Metric '%s' not present in command output. stdout: %s"
                      % (metric_name, stdout))
        missing = [m for m in output_markers if m not in stdout]
        self.assertFalse(
            missing,
            "Metric '%s' output missing required markers %s. stdout: %s" %
            (metric_name, missing, stdout))
        return stdout

    def _split_metric_table_row(self, line):
        """Split an openstack metric show table row into columns.

        Rows look like ``| br-int | 192.168.122.101:9105 | ... | 9 |`` and
        often end with a trailing pipe, which would make ``parts[-1]`` empty.
        """
        if '|' not in line:
            return None
        parts = [part.strip() for part in line.split('|')]
        if parts and not parts[0]:
            parts = parts[1:]
        if parts and not parts[-1]:
            parts = parts[:-1]
        if len(parts) < 3:
            return None
        return parts

    def _parse_metric_row_value(self, parts):
        """Return the numeric value column from a trimmed metric table row."""
        for cell in reversed(parts):
            try:
                return int(float(cell))
            except ValueError:
                continue
        return None

    def _hypervisor_identifiers(self, hypervisor_ip):
        """Strings that identify a hypervisor in openstack metric show rows."""
        if hypervisor_ip in self._hypervisor_id_cache:
            return self._hypervisor_id_cache[hypervisor_ip]
        identifiers = {hypervisor_ip}
        for hyp in self.os_admin.hypervisor_client.list_hypervisors(
                detail=True)['hypervisors']:
            if hyp.get('host_ip', '').strip() != hypervisor_ip.strip():
                continue
            hostname = hyp['hypervisor_hostname']
            identifiers.add(hostname)
            identifiers.add(hostname.split('.')[0])
        self._hypervisor_id_cache[hypervisor_ip] = identifiers
        return identifiers

    def _exporter_instance_cell(self, parts):
        """Return the instance column (contains ':9105') from a table row."""
        for part in parts:
            if NETWORK_EXPORTER_INSTANCE_PORT in part:
                return part
        return None

    def _row_is_compute_network_exporter(self, parts):
        """True if row is from openstack-network-exporter on a compute node."""
        instance = self._exporter_instance_cell(parts)
        return (instance is not None and
                'ovn-controller-metrics' not in instance)

    def _row_matches_hypervisor(self, parts, hypervisor_ip):
        """Match metric rows to a hypervisor by IP, hostname, or FQDN."""
        if not self._row_is_compute_network_exporter(parts):
            return False
        row_text = ' '.join(parts)
        return any(identifier in row_text
                   for identifier in self._hypervisor_identifiers(hypervisor_ip))

    def _is_ovn_k8s_metrics_row(self, line):
        """True for OVN metrics scraped via openstack.svc:1981 (not compute)."""
        return (OVN_K8S_METRICS_PORT in line and 'openstack.svc' in line and
                NETWORK_EXPORTER_INSTANCE_PORT not in line)

    def _parse_ovn_k8s_metric_values(self, metric_stdout):
        """Parse numeric values from OVN K8s metrics table rows."""
        values = []
        for line in metric_stdout.splitlines():
            if not self._is_ovn_k8s_metrics_row(line):
                continue
            parts = self._split_metric_table_row(line)
            if parts and parts[0] in ('instance', 'bridge'):
                continue
            if parts:
                value = self._parse_metric_row_value(parts)
                if value is not None:
                    values.append(value)
                    continue
            match = METRIC_ROW_VALUE_RE.search(line.strip())
            if match:
                values.append(int(match.group(1)))
        return values

    def _exporter_instance_samples(self, metric_stdout):
        """Distinct :9105 instance labels (for assertion messages)."""
        samples = set()
        for line in metric_stdout.splitlines():
            parts = self._split_metric_table_row(line)
            if not parts:
                continue
            instance = self._exporter_instance_cell(parts)
            if instance and 'ovn-controller-metrics' not in instance:
                samples.add(instance)
        return sorted(samples)

    def _parse_metric_values_for_bridge(self, metric_stdout, bridge,
                                        hypervisor_ip=None):
        """Parse values from openstack metric show tabular output.

        Only uses rows from openstack-network-exporter on compute nodes
        (instance <hypervisor_ip>:9105), not other scrapes such as
        ovn-controller-metrics.

        :param metric_stdout: stdout from openstack metric show
        :param bridge: OVS bridge name to match in the row
        :param hypervisor_ip: If set, only rows mentioning this IP are used
        :return: List of numeric metric values found
        """
        if not metric_stdout:
            return []
        values = []
        for line in metric_stdout.splitlines():
            parts = self._split_metric_table_row(line)
            if not parts or parts[0] != bridge:
                continue
            if hypervisor_ip and not self._row_matches_hypervisor(
                    parts, hypervisor_ip):
                continue
            value = self._parse_metric_row_value(parts)
            if value is not None:
                values.append(value)
        if not values and hypervisor_ip:
            values = self._parse_metric_values_for_bridge_fallback(
                metric_stdout, bridge, hypervisor_ip)
        return values

    def _parse_metric_values_for_bridge_fallback(self, metric_stdout, bridge,
                                                 hypervisor_ip):
        """Fallback parser when pipe-split rows do not match."""
        values = []
        for line in metric_stdout.splitlines():
            if (bridge not in line or NETWORK_EXPORTER_INSTANCE_PORT not in line
                    or 'ovn-controller-metrics' in line):
                continue
            if not self._line_matches_hypervisor(line, hypervisor_ip):
                continue
            match = METRIC_ROW_VALUE_RE.search(line.strip())
            if match:
                values.append(int(match.group(1)))
        return values

    def _line_matches_hypervisor(self, line, hypervisor_ip):
        return any(identifier in line
                   for identifier in self._hypervisor_identifiers(hypervisor_ip))

    def _bridges_reported_for_hypervisor(self, metric_stdout, hypervisor_ip):
        """Bridge names with :9105 metric rows for hypervisor_ip."""
        bridges = set()
        for line in metric_stdout.splitlines():
            parts = self._split_metric_table_row(line)
            if not parts:
                continue
            if self._row_matches_hypervisor(parts, hypervisor_ip):
                bridges.add(parts[0])
        if not bridges:
            for line in metric_stdout.splitlines():
                if (NETWORK_EXPORTER_INSTANCE_PORT not in line
                        or 'ovn-controller-metrics' in line):
                    continue
                if not self._line_matches_hypervisor(line, hypervisor_ip):
                    continue
                parts = self._split_metric_table_row(line)
                if parts:
                    bridges.add(parts[0])
        return sorted(bridges)

    def _hypervisor_ips_from_metric_stdout(self, metric_stdout):
        """Compute hypervisor IPs that expose openstack-network-exporter :9105."""
        return sorted({
            match.group(1) for line in metric_stdout.splitlines()
            for match in [COMPUTE_METRICS_HOST_RE.search(line)] if match})

    def _get_ssh_hypervisors(self, metric_stdout):
        """Hypervisors to SSH: prefer :9105 targets from metric show."""
        hypervisors = self._hypervisor_ips_from_metric_stdout(metric_stdout)
        if hypervisors:
            return hypervisors
        LOG.warning(
            'No %s instances in metric output; falling back to Nova '
            'hypervisor list', NETWORK_EXPORTER_INSTANCE_PORT)
        return self._get_hypervisor_ip_from_undercloud()

    def _ssh_run_on_hypervisor(self, hypervisor_ip, command):
        """Run command on hypervisor over SSH with connect timeout."""
        try:
            return shell_utils.run_command_over_ssh(
                hypervisor_ip, command,
                paramiko_connect_opts={
                    'allow_agent': False,
                    'timeout': SSH_CONNECT_TIMEOUT,
                    'banner_timeout': SSH_CONNECT_TIMEOUT,
                })
        except EOFError as exc:
            self.fail(
                'SSH to hypervisor %s failed (connection closed). Ensure '
                'tempest can reach %s@%s using overcloud_node_pkey_file in '
                'tempest.conf. Error: %s' % (
                    hypervisor_ip,
                    CONF.nfv_plugin_options.overcloud_node_user,
                    hypervisor_ip, exc))
        except Exception as exc:
            self.fail(
                'SSH to hypervisor %s failed running %r: %s' % (
                    hypervisor_ip, command, exc))

    def _list_ovs_bridges_on_hypervisor(self, hypervisor_ip):
        """Return OVS bridge names on a compute hypervisor (ovs-vsctl list-br)."""
        cmd = 'sudo ovs-vsctl list-br 2>/dev/null'
        out = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
        return sorted({line.strip() for line in out.splitlines() if line.strip()})

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
        """Return the number of ports on a bridge from OVSDB.

        Uses Bridge.ports (same OVSDB view as openstack-network-exporter), not
        list-ports, which can omit a port and disagree with the metric.
        """
        cmd = ("sudo ovs-vsctl get Bridge %s ports 2>/dev/null | "
               "tr -d '[]' | tr ',' '\\n' | grep -c ." % bridge)
        return int(self._ssh_run_on_hypervisor(hypervisor_ip, cmd).strip())

    def _parse_flow_count_from_exporter_metrics(self, metrics_output, bridge):
        """Parse ovs_bridge_flow_count from Prometheus text on the hypervisor."""
        for line in metrics_output.splitlines():
            if 'bridge="%s"' % bridge not in line:
                continue
            parts = line.rsplit(None, 1)
            if len(parts) == 2:
                try:
                    return int(float(parts[1]))
                except ValueError:
                    continue
        return None

    def _ovs_bridge_flow_count(self, bridge, hypervisor_ip):
        """Return OpenFlow flow count from aggregate stats on the bridge.

        openstack-network-exporter reads FlowCount via Nicira aggregate stats
        on the bridge mgmt socket (see openflow.BridgeStats.GetAggregateStats),
        not by counting dump-flows lines. Prefer ovs-ofctl dump-aggregate; if
        that yields no parseable output, read the exporter's own :9105 scrape.
        """
        ofctl_output = ''
        for cmd in (
                'sudo ovs-ofctl dump-aggregate %s 2>&1' % bridge,
                'sudo ovs-ofctl -O OpenFlow10 dump-aggregate %s 2>&1' % bridge):
            ofctl_output = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
            match = FLOW_COUNT_RE.search(ofctl_output)
            if match:
                return int(match.group(1))

        metrics_cmds = (
            ("curl -sk https://127.0.0.1:9105/metrics 2>/dev/null | "
             "grep '^ovs_bridge_flow_count{' | grep 'bridge=\"%s\"'")
            % bridge,
            ("curl -s http://127.0.0.1:9105/metrics 2>/dev/null | "
             "grep '^ovs_bridge_flow_count{' | grep 'bridge=\"%s\"'")
            % bridge,
        )
        for cmd in metrics_cmds:
            metrics_output = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
            count = self._parse_flow_count_from_exporter_metrics(
                metrics_output, bridge)
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
            LOG.warning(
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
                LOG.warning(
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
        LOG.warning("Metric '%s' reported values: %s",
                    OVN_NORTHD_STATUS_METRIC, values)
