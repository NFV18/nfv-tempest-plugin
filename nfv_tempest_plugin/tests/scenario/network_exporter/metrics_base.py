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

import ipaddress
import os
import re
import time
import unittest

import paramiko
import requests
import urllib3
from tempest import config

from nfv_tempest_plugin.tests.scenario import base_test
from oslo_log import log as logging

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROMETHEUS_HOST = os.environ.get(
    'PROMETHEUS_HOST', 'metric-storage-prometheus.openstack.svc')
PROMETHEUS_PORT = os.environ.get('PROMETHEUS_PORT', '9090')
PROMETHEUS_CA_CERT = os.environ.get('PROMETHEUS_CA_CERT', '')
PROMETHEUS_SCHEME = os.environ.get('PROMETHEUS_SCHEME', '')

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

METRIC_RETRY_ATTEMPTS = 6
METRIC_RETRY_INTERVAL = 30
OVS_BUILD_INFO_METRIC = 'ovs_build_info'
OVS_DPDK_INITIALIZED_METRIC = 'ovs_dpdk_initialized'
OVS_BRIDGE_PORT_COUNT_METRIC = 'ovs_bridge_port_count'
OVS_BRIDGE_FLOW_COUNT_METRIC = 'ovs_bridge_flow_count'
OVN_NORTHD_STATUS_METRIC = 'ovn_northd_status'
OVS_INTERFACE_ADMIN_STATE_METRIC = 'ovs_interface_admin_state'
OVS_INTERFACE_LINK_STATE_METRIC = 'ovs_interface_link_state'
OVS_INTERFACE_MTU_BYTES_METRIC = 'ovs_interface_mtu_bytes'
OVS_INTERFACE_LINK_SPEED_BPS_METRIC = 'ovs_interface_link_speed_bps'
OVS_INTERFACE_LINK_RESETS_METRIC = 'ovs_interface_link_resets'
OVS_INTERFACE_RX_PACKETS_METRIC = 'ovs_interface_rx_packets'
OVS_INTERFACE_TX_PACKETS_METRIC = 'ovs_interface_tx_packets'
OVS_INTERFACE_RX_BYTES_METRIC = 'ovs_interface_rx_bytes'
OVS_INTERFACE_TX_BYTES_METRIC = 'ovs_interface_tx_bytes'
OVS_INTERFACE_STAT_TO_METRIC = {
    'rx_packets': OVS_INTERFACE_RX_PACKETS_METRIC,
    'tx_packets': OVS_INTERFACE_TX_PACKETS_METRIC,
    'rx_bytes': OVS_INTERFACE_RX_BYTES_METRIC,
    'tx_bytes': OVS_INTERFACE_TX_BYTES_METRIC,
}
OVS_INTERFACE_RX_ERRORS_METRIC = 'ovs_interface_rx_errors'
OVS_INTERFACE_RX_DROPPED_METRIC = 'ovs_interface_rx_dropped'
OVS_INTERFACE_TX_ERRORS_METRIC = 'ovs_interface_tx_errors'
OVS_INTERFACE_TX_RETRIES_METRIC = 'ovs_interface_tx_retries'
OVS_INTERFACE_ERROR_STAT_TO_METRIC = {
    'rx_errors': OVS_INTERFACE_RX_ERRORS_METRIC,
    'rx_dropped': OVS_INTERFACE_RX_DROPPED_METRIC,
    'tx_errors': OVS_INTERFACE_TX_ERRORS_METRIC,
    'tx_retries': OVS_INTERFACE_TX_RETRIES_METRIC,
}
OVNC_ROUTER_PORT_TRAFFIC_PKTS_METRIC = 'ovnc_router_port_traffic_pkts'
OVNC_ROUTER_PORT_TRAFFIC_BYTES_METRIC = 'ovnc_router_port_traffic_bytes'
OVNC_ENCAP_IP_METRIC = 'ovnc_encap_ip'
OVNC_ENCAP_TYPE_METRIC = 'ovnc_encap_type'
OVNC_SB_CONNECTION_METHOD_METRIC = 'ovnc_sb_connection_method'
OVNC_MONITOR_ALL_METRIC = 'ovnc_monitor_all'
OVNC_BRIDGE_MAPPINGS_METRIC = 'ovnc_bridge_mappings'
OVNC_CONFIG_METRICS = (
    OVNC_ENCAP_IP_METRIC,
    OVNC_ENCAP_TYPE_METRIC,
    OVNC_SB_CONNECTION_METHOD_METRIC,
    OVNC_MONITOR_ALL_METRIC,
    OVNC_BRIDGE_MAPPINGS_METRIC,
)
OVNC_TXN_SUCCESS_METRIC = 'ovnc_txn_success'
OVNC_TXN_UNCOMMITTED_METRIC = 'ovnc_txn_uncommitted'
OVNC_TXN_ABORTED_METRIC = 'ovnc_txn_aborted'
OVNC_TXN_ERROR_METRIC = 'ovnc_txn_error'
OVNC_TXN_METRICS = (
    OVNC_TXN_SUCCESS_METRIC,
    OVNC_TXN_UNCOMMITTED_METRIC,
    OVNC_TXN_ABORTED_METRIC,
    OVNC_TXN_ERROR_METRIC,
)
OVNC_METRIC_TO_EXTERNAL_ID = {
    OVNC_ENCAP_IP_METRIC: ('ovn-encap-ip', 'encap_ip'),
    OVNC_ENCAP_TYPE_METRIC: ('ovn-encap-type', 'encap_type'),
    OVNC_SB_CONNECTION_METHOD_METRIC: ('ovn-remote', 'sb_connection_method'),
    OVNC_MONITOR_ALL_METRIC: ('ovn-monitor-all', None),
    OVNC_BRIDGE_MAPPINGS_METRIC: ('ovn-bridge-mappings', None),
}
OVNC_TXN_METRIC_TO_COVERAGE_KEY = {
    OVNC_TXN_SUCCESS_METRIC: 'txn_success',
    OVNC_TXN_UNCOMMITTED_METRIC: 'txn_uncommitted',
    OVNC_TXN_ABORTED_METRIC: 'txn_aborted',
    OVNC_TXN_ERROR_METRIC: 'txn_error',
}
OVS_DATAPATH_FLOWS_TOTAL_METRIC = 'ovs_datapath_flows_total'
OVS_DATAPATH_LOOKUP_HITS_TOTAL_METRIC = 'ovs_datapath_lookup_hits_total'
OVS_DATAPATH_LOOKUP_MISSED_TOTAL_METRIC = 'ovs_datapath_lookup_missed_total'
OVS_DATAPATH_LOOKUP_LOST_TOTAL_METRIC = 'ovs_datapath_lookup_lost_total'
OVS_DATAPATH_METRIC_TO_DPCTL_KEY = {
    OVS_DATAPATH_FLOWS_TOTAL_METRIC: 'flows',
    OVS_DATAPATH_LOOKUP_HITS_TOTAL_METRIC: 'hit',
    OVS_DATAPATH_LOOKUP_MISSED_TOTAL_METRIC: 'missed',
    OVS_DATAPATH_LOOKUP_LOST_TOTAL_METRIC: 'lost',
}
OVS_PMD_TOTAL_ITERATIONS_METRIC = 'ovs_pmd_total_iterations'
OVS_PMD_IDLE_ITERATIONS_METRIC = 'ovs_pmd_idle_iterations'
OVS_PMD_BUSY_ITERATIONS_METRIC = 'ovs_pmd_busy_iterations'
OVS_PMD_RX_PACKETS_METRIC = 'ovs_pmd_rx_packets'
OVS_PMD_TX_PACKETS_METRIC = 'ovs_pmd_tx_packets'
OVS_PMD_ITERATION_METRICS = (
    OVS_PMD_TOTAL_ITERATIONS_METRIC,
    OVS_PMD_IDLE_ITERATIONS_METRIC,
    OVS_PMD_BUSY_ITERATIONS_METRIC,
)
OVS_PMD_PACKET_METRICS = (
    OVS_PMD_RX_PACKETS_METRIC,
    OVS_PMD_TX_PACKETS_METRIC,
)
OVS_PMD_CPU_OVERHEAD_METRIC = 'ovs_pmd_cpu_overhead'
OVS_PMD_RXQ_USAGE_METRIC = 'ovs_pmd_rxq_usage'
OVS_PMD_CPU_USAGE_METRICS = (
    OVS_PMD_CPU_OVERHEAD_METRIC,
    OVS_PMD_RXQ_USAGE_METRIC,
)
OVS_MEMORY_HANDLERS_TOTAL_METRIC = 'ovs_memory_handlers_total'
OVS_MEMORY_PORTS_TOTAL_METRIC = 'ovs_memory_ports_total'
OVS_MEMORY_REVALIDATORS_TOTAL_METRIC = 'ovs_memory_revalidators_total'
OVS_MEMORY_RULES_TOTAL_METRIC = 'ovs_memory_rules_total'
OVS_MEMORY_KEYS_TOTAL_METRIC = 'ovs_memory_keys_total'
OVS_MEMORY_METRICS = (
    OVS_MEMORY_HANDLERS_TOTAL_METRIC,
    OVS_MEMORY_PORTS_TOTAL_METRIC,
    OVS_MEMORY_REVALIDATORS_TOTAL_METRIC,
    OVS_MEMORY_RULES_TOTAL_METRIC,
    OVS_MEMORY_KEYS_TOTAL_METRIC,
)
OVS_MEMORY_METRIC_TO_SHOW_KEY = {
    OVS_MEMORY_HANDLERS_TOTAL_METRIC: 'handlers',
    OVS_MEMORY_PORTS_TOTAL_METRIC: 'ports',
    OVS_MEMORY_REVALIDATORS_TOTAL_METRIC: 'revalidators',
    OVS_MEMORY_RULES_TOTAL_METRIC: 'rules',
    OVS_MEMORY_KEYS_TOTAL_METRIC: 'keys',
}
OVS_PMD_METRIC_TO_PERF_STAT = {
    OVS_PMD_TOTAL_ITERATIONS_METRIC: 'Iterations',
    OVS_PMD_IDLE_ITERATIONS_METRIC: '- idle iterations',
    OVS_PMD_BUSY_ITERATIONS_METRIC: '- busy iterations',
    OVS_PMD_RX_PACKETS_METRIC: 'Rx packets',
    OVS_PMD_TX_PACKETS_METRIC: 'Tx packets',
}
NET_VF_INFO_METRIC = 'net_vf_info'
NET_VF_RECEIVE_PACKETS_METRIC = 'net_vf_receive_packets_total'
NET_VF_TRANSMIT_PACKETS_METRIC = 'net_vf_transmit_packets_total'
NET_VF_RECEIVE_BYTES_METRIC = 'net_vf_receive_bytes_total'
NET_VF_TRANSMIT_BYTES_METRIC = 'net_vf_transmit_bytes_total'
NET_VF_RECEIVE_DROPPED_METRIC = 'net_vf_receive_dropped_total'
NET_VF_TRANSMIT_DROPPED_METRIC = 'net_vf_transmit_dropped_total'
NET_VF_BROADCAST_PACKETS_METRIC = 'net_vf_broadcast_packets_total'
NET_VF_MULTICAST_PACKETS_METRIC = 'net_vf_multicast_packets_total'
NET_VF_COUNTER_METRICS = (
    NET_VF_RECEIVE_PACKETS_METRIC,
    NET_VF_TRANSMIT_PACKETS_METRIC,
    NET_VF_RECEIVE_BYTES_METRIC,
    NET_VF_TRANSMIT_BYTES_METRIC,
    NET_VF_BROADCAST_PACKETS_METRIC,
    NET_VF_MULTICAST_PACKETS_METRIC,
)
NET_VF_ALL_METRICS = (
    NET_VF_INFO_METRIC,
    NET_VF_RECEIVE_PACKETS_METRIC,
    NET_VF_TRANSMIT_PACKETS_METRIC,
    NET_VF_RECEIVE_BYTES_METRIC,
    NET_VF_TRANSMIT_BYTES_METRIC,
    NET_VF_RECEIVE_DROPPED_METRIC,
    NET_VF_TRANSMIT_DROPPED_METRIC,
    NET_VF_BROADCAST_PACKETS_METRIC,
    NET_VF_MULTICAST_PACKETS_METRIC,
)
# Host sysfs under .../sriov/vfN/stats/ (same source as net_vf exporter).
NET_VF_METRIC_TO_SYSFS_STAT = {
    NET_VF_RECEIVE_PACKETS_METRIC: 'rx_packets',
    NET_VF_TRANSMIT_PACKETS_METRIC: 'tx_packets',
    NET_VF_RECEIVE_BYTES_METRIC: 'rx_bytes',
    NET_VF_TRANSMIT_BYTES_METRIC: 'tx_bytes',
    NET_VF_RECEIVE_DROPPED_METRIC: 'rx_dropped',
    NET_VF_TRANSMIT_DROPPED_METRIC: 'tx_dropped',
}
# OVN/K8s service metrics (northd, controller, etc.), not compute :9105
OVN_K8S_METRICS_PORT = ':1981'
OVN_CONTROLLER_METRICS_POD_RE = r'ovn-controller-metrics.*'
OVN_CONTROLLER_METRICS_CONTAINER = 'ovn-controller-metrics'
OVN_CONTROLLER_METRICS_CURL = (
    'curl -sk https://127.0.0.1:1981/metrics 2>/dev/null || '
    'curl -s http://127.0.0.1:1981/metrics 2>/dev/null')
PROM_NUMBER_RE = r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'
PROM_METRIC_LINE_RE = re.compile(
    r'^(?P<metric>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'\{(?P<labels>[^}]*)\}\s+'
    r'(?P<value>' + PROM_NUMBER_RE + r')$')
PROM_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')
# openstack-network-exporter: 0=standby, 1=active, 2=paused
OVN_NORTHD_STATUS_VALUES = (0, 1, 2)
OVN_NORTHD_STATUS_ACTIVE = 1
# openstack-network-exporter: admin/link up=1, down=0; link unknown=-1
OVS_STATE_UP = 1
OVS_STATE_DOWN = 0
NETWORK_EXPORTER_INSTANCE_PORT = ':9105'
FLOW_COUNT_RE = re.compile(r'flow_count=(\d+)', re.IGNORECASE)
DPCTL_DATAPATH_HEADER_RE = re.compile(r'^([\w-]+)@([\w-]+):$')
DPCTL_LOOKUPS_LINE_RE = re.compile(
    r'^  lookups:\s*hit:\s*(\d+)\s+missed:\s*(\d+)\s+lost:(\d+)$')
DPCTL_FLOWS_LINE_RE = re.compile(r'^  flows:\s*(\d+)$')
DPCTL_SHOW_COMMANDS = (
    'sudo ovs-appctl dpctl/show 2>/dev/null',
    'ovs-appctl dpctl/show 2>/dev/null',
    'sudo ovs-dpctl show -s 2>/dev/null',
    'sudo ovs-dpctl show 2>/dev/null',
)
PMD_THREAD_RE = re.compile(r'^pmd thread numa_id (\d+) core_id (\d+):$')
PMD_PERF_STAT_RE = re.compile(r'^\s*([^:]+):\s+(\d+)\s*(.*)$')
PMD_PERF_SHOW_COMMANDS = (
    'sudo ovs-appctl dpif-netdev/pmd-perf-show 2>/dev/null',
    'ovs-appctl dpif-netdev/pmd-perf-show 2>/dev/null',
)
PMD_RXQ_SHOW_COMMANDS = (
    'sudo ovs-appctl dpif-netdev/pmd-rxq-show 2>/dev/null',
    'ovs-appctl dpif-netdev/pmd-rxq-show 2>/dev/null',
)
MEMORY_COUNT_RE = re.compile(r'(\w+):(\d+)')
MEMORY_METRICS_DELIMITER = '---NETWORK_EXPORTER_METRICS---'
MEMORY_SHOW_COMMANDS = (
    'sudo ovs-appctl memory/show 2>/dev/null',
    'ovs-appctl memory/show 2>/dev/null',
)
OVN_COVERAGE_TOTAL_RE = re.compile(
    r'^(\w+)\s+.*\s+total:\s*(\d+)\s*$')
OVN_COVERAGE_METRICS_DELIMITER = '---OVN_COVERAGE_METRICS---'
OVN_COVERAGE_SHOW_COMMANDS = (
    'sudo ovs-appctl -t ovn-controller coverage/show 2>/dev/null',
    'ovs-appctl -t ovn-controller coverage/show 2>/dev/null',
)
OVS_EXTERNAL_ID_RE = re.compile(r'([\w-]+)="([^"]*)"')
PMD_RXQ_OVERHEAD_RE = re.compile(r'^\s*overhead\s*:\s*([\d.]+)\s*%$')
PMD_RXQ_USAGE_RE = re.compile(
    r'^\s*port:\s*(\S+)\s+queue-id:\s*(\d+)\s+\((enabled|disabled)\)\s+'
    r'pmd usage:\s*([\d.]+)\s*%$')
METRIC_ROW_VALUE_RE = re.compile(r'(\d+)\s*\|?\s*$')
COMPUTE_METRICS_HOST_RE = re.compile(
    r'(\d+\.\d+\.\d+\.\d+)' + re.escape(NETWORK_EXPORTER_INSTANCE_PORT))
SSH_CONNECT_TIMEOUT = 30
# Linux IFNAMSIZ (16 bytes including NUL)
LINUX_MAX_IFNAME_LEN = 15
LEGACY_STATE_TEST_INTERFACES = (
    'tempest-ovs-state-test',
    'tempest-ovs-state-test-host',
)
PING_FAST_INTERVAL_SEC = 0.001
PING_SLOW_INTERVAL_SEC = 0.2
PING_MAX_WALL_SECONDS = 120
# Routed fast ping needs interval plus RTT per reply; 30s caps ~3k of 5k probes.
PING_FAST_FLOOD_PER_PACKET_SEC = 0.01
PING_FAST_FLOOD_MIN_WALL_SECONDS = 90
# Tempest SSH exec often returns after ~30s; batch routed floods to stay under it.
PING_FAST_BATCH_PACKETS = 600
PING_FAST_BATCH_MAX_WALL_SECONDS = 25


class NetworkExporterMetricsBase(base_test.BaseTest):
    """Shared helpers for openstack-network-exporter Tempest tests."""

    @staticmethod
    def _parse_prom_number(value_str):
        """Parse a Prometheus numeric sample (supports scientific notation)."""
        return int(float(value_str))

    def __init__(self, *args, **kwargs):
        super(NetworkExporterMetricsBase, self).__init__(*args, **kwargs)
        self._hypervisor_id_cache = {}
        self._prometheus_https_verify = (
            PROMETHEUS_CA_CERT if PROMETHEUS_CA_CERT else False)
        self._prometheus_query_urls = self._prometheus_query_url_candidates()

    @staticmethod
    def _prometheus_query_url_candidates():
        """Return metric-storage query URLs; HTTP first unless scheme is set."""
        base = '%s:%s/api/v1/query' % (PROMETHEUS_HOST, PROMETHEUS_PORT)
        if PROMETHEUS_SCHEME:
            return ['%s://%s' % (PROMETHEUS_SCHEME, base)]
        return ['http://%s' % base, 'https://%s' % base]

    @staticmethod
    def _prometheus_results_to_table(results):
        """Format Prometheus query results as a pipe-delimited table."""
        if not results:
            return ''
        all_labels = []
        seen = set()
        for result in results:
            for key in result['metric']:
                if key not in seen:
                    all_labels.append(key)
                    seen.add(key)
        cols = all_labels + ['value']
        widths = [len(column) for column in cols]
        rows = []
        for result in results:
            row = [str(result['metric'].get(column, ''))
                   for column in all_labels]
            row.append(str(result['value'][1]))
            rows.append(row)
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(cell))
        separator = '+' + '+'.join('-' * (width + 2) for width in widths) + '+'
        header = '| ' + ' | '.join(
            column.ljust(width) for column, width in zip(cols, widths)) + ' |'
        lines = [separator, header, separator]
        for row in rows:
            lines.append('| ' + ' | '.join(
                cell.ljust(width) for cell, width in zip(row, widths)) + ' |')
            lines.append(separator)
        return '\n'.join(lines) + '\n'

    def _query_prometheus(self, query):
        """Query metric-storage Prometheus; try HTTP then HTTPS by default."""
        errors = []
        for url in self._prometheus_query_urls:
            verify = (self._prometheus_https_verify
                      if url.startswith('https://') else True)
            try:
                resp = requests.get(
                    url, params={'query': query}, verify=verify, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                errors.append('%s: %s' % (url, exc))
                continue
            if data.get('status') != 'success':
                errors.append('%s: %s' % (
                    url, data.get('error', 'unknown error')))
                continue
            return data.get('data', {}).get('result', []), ''
        return None, '; '.join(errors) or 'Prometheus query failed'

    def _fetch_metric_storage_promql_results(self, metric_name):
        """Query metric-storage Prometheus for instant vector samples."""
        query = 'last_over_time(%s[5m])' % metric_name
        results, error = self._query_prometheus(query)
        if results:
            return results, ''
        return [], error or 'metric-storage query failed'

    @staticmethod
    def _is_ip_literal(value):
        """True when value is a bare IPv4/IPv6 address (not a hostname)."""
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    def _text_matches_hypervisor_identifier(self, text, ident):
        """Match a hypervisor identifier against label/row text.

        Do not derive short forms from IP addresses (e.g. 192.168.122.101
        must not use '192' as a substring match — that collides with every
        other 192.x compute). Hostname short forms require a token boundary
        so 'compute-0' does not match 'compute-01'.
        """
        if not text or not ident:
            return False
        if self._is_ip_literal(ident):
            # Match bare IP or Prometheus instance form IP:port. Never use the
            # first dotted octet as a short form (that matches every 192.x host).
            return bool(re.search(
                r'(^|[^0-9.])%s(:\d+)?(?=[^0-9.]|$)' % re.escape(ident),
                text))
        if ident in text:
            return True
        short = ident.split('.')[0]
        if not short or short == ident:
            return False
        return bool(re.search(
            r'(^|[^A-Za-z0-9-])%s([^A-Za-z0-9-]|$)' % re.escape(short),
            text))

    def _sample_matches_hypervisor(self, labels, hypervisor_ip):
        """True when a metric-storage sample belongs to hypervisor_ip."""
        row_text = ' '.join(
            labels.get(key, '') for key in ('instance', 'fqdn', 'hostname'))
        return any(
            self._text_matches_hypervisor_identifier(row_text, ident)
            for ident in self._hypervisor_identifiers(hypervisor_ip))

    def _metric_storage_samples(self, metric_name, hypervisor_ip=None,
                                required_labels=None):
        """Return metric-storage samples filtered by hypervisor and labels."""
        results, error = self._fetch_metric_storage_promql_results(metric_name)
        samples = []
        for result in results:
            labels = result.get('metric', {})
            value = result.get('value', [None, None])[1]
            if value is None:
                continue
            if (hypervisor_ip and
                    not self._sample_matches_hypervisor(labels, hypervisor_ip)):
                continue
            if required_labels and any(
                    labels.get(key) != val
                    for key, val in required_labels.items()):
                continue
            samples.append({
                'labels': labels,
                'value': int(float(value)),
            })
        return samples, error

    def _metric_show_output_usable(self, metric_name, stdout):
        """True when PromQL output contains metric_name samples."""
        stdout = stdout or ''
        return bool(stdout.strip()) and metric_name in stdout

    def _metric_show(self, metric_name):
        """Query metric-storage Prometheus for metric values."""
        query = 'last_over_time(%s[5m])' % metric_name
        LOG.info("Querying Prometheus (%s): %s",
                 self._prometheus_query_urls[0], query)
        try:
            results, error = self._query_prometheus(query)
            if results is None:
                LOG.warning("Prometheus query failed: %s", error)
                return '', error, 1
            stdout = self._prometheus_results_to_table(results)
            if not self._metric_show_output_usable(metric_name, stdout):
                return '', (
                    'metric-storage returned no samples for %s' % metric_name), 1
            return stdout, '', 0
        except Exception as exc:
            LOG.warning("Prometheus query error: %s", exc)
            return '', str(exc), 1

    def _scrape_compute_metrics_text(self, hypervisor_ip):
        """Return openstack-network-exporter Prometheus text from a compute node."""
        cmd_https = "curl -sk https://127.0.0.1:9105/metrics 2>/dev/null"
        metrics_output = self._ssh_run_on_hypervisor(hypervisor_ip, cmd_https)
        if metrics_output.strip():
            return metrics_output
        cmd_http = "curl -s http://127.0.0.1:9105/metrics 2>/dev/null"
        return self._ssh_run_on_hypervisor(hypervisor_ip, cmd_http)

    def _scrape_ovn_metrics_text(self, hypervisor_ip):
        """Return OVN controller exporter Prometheus text from a compute node."""
        return self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip, OVN_CONTROLLER_METRICS_CURL)

    def _ovn_controller_metrics_pods(self):
        return self.k8s_client.search_pods_using_regex(
            OVN_CONTROLLER_METRICS_POD_RE, OPENSTACK_NAMESPACE)

    def _scrape_ovn_controller_metrics_pod(self, hypervisor_ip=None):
        """Scrape ovn-controller-metrics :1981 via pod exec."""
        pod_name = None
        pod_obj = None
        if hypervisor_ip:
            for pod in self._ovn_controller_metrics_pods():
                node = (pod.get('spec') or {}).get('nodeName') or ''
                if not node:
                    continue
                node_short = node.split('.')[0]
                for ident in self._hypervisor_identifiers(hypervisor_ip):
                    if self._is_ip_literal(ident):
                        # nodeName is normally a hostname; only match exact IP.
                        if ident == node:
                            pod_name = pod['metadata']['name']
                            pod_obj = pod
                            break
                        continue
                    ident_short = ident.split('.')[0]
                    if (ident == node or ident in node or node in ident or
                            (ident_short and ident_short == node_short)):
                        pod_name = pod['metadata']['name']
                        pod_obj = pod
                        break
                if pod_name:
                    break
        if not pod_name:
            pods = self._ovn_controller_metrics_pods()
            if pods:
                pod_obj = pods[0]
                pod_name = pod_obj['metadata']['name']
        if not pod_name:
            return ''
        containers = [
            c.get('name') for c in (
                (pod_obj or {}).get('spec') or {}).get('containers', [])
            if c.get('name')]
        if OVN_CONTROLLER_METRICS_CONTAINER in containers:
            containers = [OVN_CONTROLLER_METRICS_CONTAINER] + [
                c for c in containers
                if c != OVN_CONTROLLER_METRICS_CONTAINER]
        elif not containers:
            containers = [OVN_CONTROLLER_METRICS_CONTAINER]
        last_exc = None
        for container in containers:
            try:
                output = self.k8s_client.execute_command_in_pod(
                    pod_name, OPENSTACK_NAMESPACE, container,
                    OVN_CONTROLLER_METRICS_CURL)
                if output.strip():
                    return output
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'ovn-controller-metrics scrape failed for pod %s '
                    'container %s: %s', pod_name, container, exc)
        if last_exc:
            LOG.warning(
                'ovn-controller-metrics scrape failed for pod %s: %s',
                pod_name, last_exc)
        return ''

    def _ovn_metric_scrape_outputs(self, hypervisor_ip=None):
        """Yield Prometheus text from OVN :1981 sources (node-local then pod)."""
        if hypervisor_ip:
            output = self._scrape_ovn_metrics_text(hypervisor_ip)
            if output.strip():
                yield ('compute:%s' % hypervisor_ip, output)
        pod_output = self._scrape_ovn_controller_metrics_pod(hypervisor_ip)
        if pod_output.strip():
            yield ('ovn-controller-metrics', pod_output)

    def _metric_on_ovn_scrape(self, metric_name):
        """Return True when metric_name appears on a best-effort OVN :1981 scrape."""
        hypervisors = self._get_hypervisor_ip_from_undercloud()
        for hypervisor_ip in hypervisors:
            for _source, output in self._ovn_metric_scrape_outputs(hypervisor_ip):
                for line in output.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(metric_name + '{') or stripped.startswith(
                            metric_name + ' '):
                        return True
        return False

    def _guest_has_passwordless_sudo(self, ssh_client):
        """Return True when the guest accepts ``sudo -n`` without prompting."""
        try:
            ssh_client.exec_command('sudo -n true')
            return True
        except Exception:
            return False

    def _ping_fast_flood_wall_seconds(self, count):
        """Wall-clock budget for ``ping -c N -i 0.001`` on routed networks."""
        return max(
            PING_FAST_FLOOD_MIN_WALL_SECONDS,
            int(count * PING_FAST_FLOOD_PER_PACKET_SEC) + 30)

    def _ping_fast_batch_wall_seconds(self, batch_count):
        """Per-batch wall clock kept under typical Tempest SSH exec limits."""
        return min(
            PING_FAST_BATCH_MAX_WALL_SECONDS,
            max(20, int(batch_count * PING_FAST_FLOOD_PER_PACKET_SEC) + 15))

    def _send_ping_packets(self, ssh_client, dest_ip, count, min_packets):
        """Send ICMP echo requests between guests for traffic counter tests.

        Unprivileged ``ping -i 0.001`` is rejected on RHEL/iputils (200ms floor).
        When passwordless sudo is unavailable, fall back to ``-i 0.2`` bounded by
        ``PING_MAX_WALL_SECONDS`` so SSH exec does not time out.
        """
        errors = []
        if self._guest_has_passwordless_sudo(ssh_client):
            for template in (
                    'timeout %d sudo -n ping -c %d -i %g -W 2 %s',
                    'ping -c %d -i %g -W 2 %s'):
                if 'sudo' in template:
                    wall = self._ping_fast_flood_wall_seconds(count)
                    cmd = template % (
                        wall, count, PING_FAST_INTERVAL_SEC, dest_ip)
                else:
                    cmd = template % (
                        count, PING_FAST_INTERVAL_SEC, dest_ip)
                LOG.warning('Sending dataplane ping: %s', cmd)
                try:
                    output = ssh_client.exec_command(cmd)
                except Exception as exc:
                    errors.append('%s -> %s' % (cmd, exc))
                    continue
                if '100% packet loss' in (output or ''):
                    errors.append('%s -> 100%% packet loss' % cmd)
                    continue
                return output

        slow_count = min(
            count,
            int(PING_MAX_WALL_SECONDS / PING_SLOW_INTERVAL_SEC))
        if slow_count < min_packets:
            self.fail(
                'Cannot send %d ICMP replies (need >=%d) within %ds without '
                'passwordless sudo on the guest. Lower the traffic ping count '
                'to <= %d (at %.1fs interval) or grant passwordless sudo for '
                'ping.' % (
                    count, min_packets, PING_MAX_WALL_SECONDS, slow_count,
                    PING_SLOW_INTERVAL_SEC))
        wall = int(slow_count * PING_SLOW_INTERVAL_SEC) + 15
        cmd = 'timeout %d ping -c %d -i %g -W 2 %s' % (
            wall, slow_count, PING_SLOW_INTERVAL_SEC, dest_ip)
        LOG.warning('Sending dataplane ping (slow path): %s', cmd)
        try:
            output = ssh_client.exec_command(cmd)
        except Exception as exc:
            errors.append('%s -> %s' % (cmd, exc))
        else:
            if '100% packet loss' not in (output or ''):
                return output
            errors.append('%s -> 100%% packet loss' % cmd)
        if errors:
            detail = '; '.join(errors)
        else:
            detail = 'slow ping path failed'
        self.fail('Ping to %s failed: %s' % (dest_ip, detail))

    def _guest_sudo_prefix(self, ssh_client):
        """Return sudo -n prefix when passwordless sudo is available."""
        return 'sudo -n' if self._guest_has_passwordless_sudo(ssh_client) else ''

    def _run_guest_python_script(self, ssh_client, script, timeout_sec=60):
        """Run a Python script on the guest via base64 pipe (avoids quoting)."""
        encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
        cmd = 'timeout %d sh -c \'echo %s | base64 -d | python3\'' % (
            timeout_sec, encoded)
        sudo = self._guest_sudo_prefix(ssh_client)
        if sudo:
            cmd = '%s %s' % (sudo, cmd)
        return ssh_client.exec_command(cmd)

    def _parse_ping_transmitted(self, output):
        """Return ICMP probes transmitted per ping(8) statistics output."""
        match = re.search(r'(\d+) packets transmitted', output or '')
        return int(match.group(1)) if match else 0

    def _bound_ping_cmd_output(self, ssh_client, cmd, accept_xmit_only=False):
        """Run a bound ping command; optionally ignore non-zero ping exit status."""
        run = '%s || true' % cmd if accept_xmit_only else cmd
        try:
            return ssh_client.exec_command(run) or ''
        except Exception as exc:
            if accept_xmit_only:
                return str(exc)
            raise

    def _bound_ping_output_ok(self, output, min_packets,
                              accept_xmit_only=False):
        transmitted = self._parse_ping_transmitted(output)
        if accept_xmit_only:
            return transmitted >= min_packets, transmitted
        if '100% packet loss' not in (output or ''):
            return True, transmitted
        return False, transmitted

    def _send_ping_packets_bound(self, ssh_client, bind_ip, dest_ip, count,
                                 min_packets, accept_xmit_only=False):
        """Send ICMP echo requests bound to a specific source address."""
        if (count > PING_FAST_BATCH_PACKETS and
                self._guest_has_passwordless_sudo(ssh_client)):
            return self._send_ping_packets_bound_batched(
                ssh_client, bind_ip, dest_ip, count, min_packets,
                accept_xmit_only=accept_xmit_only)
        output = self._send_ping_packets_bound_once(
            ssh_client, bind_ip, dest_ip, count, min_packets,
            accept_xmit_only=accept_xmit_only)
        if accept_xmit_only:
            return self._parse_ping_transmitted(output)
        return output

    def _send_ping_packets_bound_batched(self, ssh_client, bind_ip, dest_ip,
                                         count, min_packets,
                                         accept_xmit_only=False):
        """Send a large bound ping flood in SSH-friendly batches."""
        remaining = count
        last_output = ''
        total_xmit = 0
        batch_num = 0
        batch_min = 1 if accept_xmit_only else min_packets
        while remaining > 0:
            batch_num += 1
            batch = min(PING_FAST_BATCH_PACKETS, remaining)
            LOG.warning(
                'Bound ping batch %d: %d packets (%d remaining)',
                batch_num, batch, remaining - batch)
            last_output = self._send_ping_packets_bound_once(
                ssh_client, bind_ip, dest_ip, batch, min_packets=batch_min,
                fast_wall=self._ping_fast_batch_wall_seconds(batch),
                accept_xmit_only=accept_xmit_only)
            total_xmit += self._parse_ping_transmitted(last_output)
            remaining -= batch
        if accept_xmit_only:
            LOG.warning(
                'Bound ping xmit-only flood: %d/%d transmitted %s -> %s',
                total_xmit, count, bind_ip, dest_ip)
            return total_xmit
        return last_output

    def _send_ping_packets_bound_once(self, ssh_client, bind_ip, dest_ip, count,
                                      min_packets, fast_wall=None,
                                      accept_xmit_only=False):
        """Send one bound ping command (single SSH exec)."""
        errors = []
        if self._guest_has_passwordless_sudo(ssh_client):
            wall = (fast_wall if fast_wall is not None else
                    self._ping_fast_flood_wall_seconds(count))
            for template in (
                    'timeout %d sudo -n ping -c %d -I %s -i %g -W 2 %s',
                    'ping -c %d -I %s -i %g -W 2 %s'):
                if 'sudo' in template:
                    cmd = template % (
                        wall, count, bind_ip, PING_FAST_INTERVAL_SEC, dest_ip)
                else:
                    cmd = template % (
                        count, bind_ip, PING_FAST_INTERVAL_SEC, dest_ip)
                LOG.warning('Sending bound ping: %s', cmd)
                try:
                    output = self._bound_ping_cmd_output(
                        ssh_client, cmd, accept_xmit_only=accept_xmit_only)
                except Exception as exc:
                    errors.append('%s -> %s' % (cmd, exc))
                    continue
                ok, transmitted = self._bound_ping_output_ok(
                    output, min_packets, accept_xmit_only=accept_xmit_only)
                if ok:
                    if accept_xmit_only:
                        LOG.warning(
                            'Bound ping xmit-only OK: %d transmitted %s -> %s',
                            transmitted, bind_ip, dest_ip)
                    return output
                errors.append(
                    '%s -> xmit %d (need >= %d)' % (cmd, transmitted,
                                                    min_packets))
        capped = min(count, int(PING_MAX_WALL_SECONDS /
                                 PING_SLOW_INTERVAL_SEC))
        if capped < min_packets and not accept_xmit_only:
            LOG.warning(
                'Bound ping capped at %d without passwordless sudo '
                '(requested min %d); using capped count at %gs interval',
                capped, min_packets, PING_SLOW_INTERVAL_SEC)
            min_packets = capped
        wall = int(capped * PING_SLOW_INTERVAL_SEC) + 30
        cmd = 'timeout %d ping -c %d -I %s -i %g -W 2 %s' % (
            wall, capped, bind_ip, PING_SLOW_INTERVAL_SEC, dest_ip)
        LOG.warning('Sending bound ping (slow path): %s', cmd)
        try:
            output = self._bound_ping_cmd_output(
                ssh_client, cmd, accept_xmit_only=accept_xmit_only)
        except Exception as exc:
            errors.append('%s -> %s' % (cmd, exc))
        else:
            ok, transmitted = self._bound_ping_output_ok(
                output, min_packets, accept_xmit_only=accept_xmit_only)
            if ok:
                return output
            errors.append(
                '%s -> xmit %d (need >= %d)' % (cmd, transmitted,
                                                min_packets))
        detail = '; '.join(errors) if errors else 'bound ping failed'
        self.fail('Ping from %s to %s failed: %s' % (bind_ip, dest_ip, detail))

    def _probe_bound_ping(self, ssh_client, bind_ip, dest_ip, count=8):
        """Return True when at least one ICMP reply is received on bind_ip."""
        sudo = self._guest_sudo_prefix(ssh_client)
        base = (
            'ping -c %d -I %s -i 0.2 -W 3 %s 2>&1' % (count, bind_ip, dest_ip))
        cmd = '%s %s || true' % (sudo, base) if sudo else '%s || true' % base
        try:
            output = ssh_client.exec_command(cmd) or ''
        except Exception as exc:
            LOG.warning('Bound ping probe %s -> %s failed: %s',
                        bind_ip, dest_ip, exc)
            return False
        if '100% packet loss' in output:
            return False
        match = re.search(r'(\d+) received', output)
        return bool(match and int(match.group(1)) > 0)

    def _flood_udp_dataplane(self, ssh_client, bind_ip, dest_ip, packet_count,
                             broadcast=False):
        """Send UDP datagrams bound to bind_ip (no sudo required)."""
        bcast = 'True' if broadcast else 'False'
        script = (
            'import socket\n'
            's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
            's.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)\n'
            'if %s:\n'
            '    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)\n'
            's.bind((%r, 0))\n'
            'payload = b"x" * 1400\n'
            'dest = (%r, 9999)\n'
            'for _ in range(%d):\n'
            '    try:\n'
            '        s.sendto(payload, dest)\n'
            '    except OSError:\n'
            '        pass\n'
            % (bcast, bind_ip, dest_ip, packet_count))
        timeout_sec = max(180, int(packet_count / 500) + 60)
        self._run_guest_python_script(ssh_client, script, timeout_sec=timeout_sec)

    def _assert_ovs_interface_metric_reported(self, metric_name):
        """Assert metric on openstack metric show, compute :9105, metric-storage."""
        stdout, stderr, returncode = self._metric_show(metric_name)
        stdout = stdout or ''
        if self._metric_show_output_usable(metric_name, stdout):
            LOG.info(
                "Metric '%s' reported via openstack metric show or "
                "metric-storage fallback", metric_name)
        else:
            LOG.warning(
                "openstack metric show unavailable for '%s' (exit %s: %s); "
                "falling back to compute :9105 SSH scrape",
                metric_name, returncode, stderr)
            self._assert_metric_on_compute_scrape(metric_name)
        storage_samples, query_error = self._metric_storage_samples(metric_name)
        self.assertNotEmpty(
            storage_samples,
            '%s missing from metric-storage Prometheus (query: %s)' % (
                metric_name, query_error))

    def _assert_router_port_metric_reported(self, metric_name):
        """Assert ovnc_router_port_traffic_* via openstack metric show and storage."""
        self._assert_metric_reported(metric_name)
        storage_samples, query_error = self._metric_storage_samples(metric_name)
        self.assertNotEmpty(
            storage_samples,
            '%s missing from metric-storage Prometheus (query: %s)' % (
                metric_name, query_error))
        if not self._metric_on_ovn_scrape(metric_name):
            LOG.warning(
                '%s present in metric-storage but not on a Tempest-reachable '
                'OVN :1981 scrape; router port tests use metric-storage',
                metric_name)

    def _assert_ovn_controller_metric_best_effort(self, metric_name):
        """Best-effort federation check; require live OVN :1981 scrape."""
        stdout, stderr, returncode = self._metric_show(metric_name)
        stdout = stdout or ''
        if self._metric_show_output_usable(metric_name, stdout):
            LOG.info(
                "Metric '%s' reported via openstack metric show or "
                "metric-storage fallback", metric_name)
        else:
            LOG.warning(
                "openstack metric show unavailable for '%s' (exit %s: %s); "
                "continuing with live OVN :1981 scrape",
                metric_name, returncode, stderr)
        storage_samples, query_error = self._metric_storage_samples(metric_name)
        if storage_samples:
            LOG.info(
                "Metric '%s' present in metric-storage (%s samples)",
                metric_name, len(storage_samples))
        else:
            LOG.warning(
                '%s missing from metric-storage Prometheus (query: %s); '
                'continuing with live OVN :1981 scrape',
                metric_name, query_error)
        self.assertTrue(
            self._metric_on_ovn_scrape(metric_name),
            "Metric '%s' not found on a Tempest-reachable OVN :1981 scrape"
            % metric_name)

    def _assert_ovnc_txn_presence(self, metric_name):
        """Assert ovnc_txn_* path via live :1981 or coverage/show (not federated)."""
        coverage_key = OVNC_TXN_METRIC_TO_COVERAGE_KEY[metric_name]
        storage_samples, query_error = self._metric_storage_samples(metric_name)
        if not storage_samples:
            LOG.warning(
                '%s not federated to metric-storage (%s); using live OVN '
                'scrape and coverage/show',
                metric_name, query_error)

        hypervisors = self._get_ssh_hypervisors('')
        ovn_hypervisors = self._hypervisors_with_ovn_controller(hypervisors)
        self.assertNotEmpty(
            ovn_hypervisors,
            'No OVN controller on hypervisors %s for %s' % (
                hypervisors, metric_name))

        found_on = []
        for hypervisor_ip in ovn_hypervisors:
            if self._ovnc_live_samples(hypervisor_ip, metric_name):
                found_on.append(hypervisor_ip)
                continue
            try:
                totals = self._ovn_coverage_totals(hypervisor_ip)
                if coverage_key in totals or totals:
                    found_on.append(hypervisor_ip)
            except Exception as exc:
                LOG.warning(
                    'No coverage/show for %s on %s: %s',
                    metric_name, hypervisor_ip, exc)
        self.assertNotEmpty(
            found_on,
            "Metric '%s' not on OVN :1981 scrape and no coverage/show txn "
            "stats on hypervisors %s" % (metric_name, ovn_hypervisors))
        LOG.info(
            "Metric '%s' reachable on hypervisor(s) %s via :1981 or "
            "coverage/show",
            metric_name, found_on)

    def _router_port_label_key(self, labels):
        """Stable identity for ovnc_router_port_traffic series."""
        return (labels.get('datapath'), labels.get('port'))

    def _router_port_samples(self, hypervisor_ip, metric_name):
        """Return router-port samples from metric-storage (RHOSO federation path)."""
        samples, error = self._metric_storage_samples(metric_name)
        if samples:
            return samples
        LOG.warning(
            'No %s samples in metric-storage (%s); trying live OVN scrape',
            metric_name, error)
        return self._router_port_prom_samples(hypervisor_ip, metric_name)

    def _router_port_prom_samples(self, hypervisor_ip, metric_name):
        """Best-effort live scrape from OVN :1981 then compute :9105."""
        for _source, output in self._ovn_metric_scrape_outputs(hypervisor_ip):
            samples = self._parse_prom_samples(output, metric_name)
            if samples:
                return samples
        return self._parse_prom_samples(
            self._scrape_compute_metrics_text(hypervisor_ip), metric_name)

    def _prom_router_port_samples(self, hypervisor_ip, metric_name):
        """Return router-port samples (metric-storage first)."""
        return self._router_port_samples(hypervisor_ip, metric_name)

    def _router_port_sample_map(self, hypervisor_ip, metric_name, storage=False):
        """Map (datapath, port) -> value from metric-storage Prometheus."""
        del storage  # kept for callers; router metrics always use storage
        return {
            self._router_port_label_key(sample['labels']): sample['value']
            for sample in self._router_port_samples(hypervisor_ip, metric_name)
        }

    def _configured_datapath_name(self):
        return CONF.nfv_plugin_options.network_exporter_datapath_name

    def _configured_datapath_type(self):
        return CONF.nfv_plugin_options.network_exporter_datapath_type

    def _datapath_required_labels(self, datapath=None, datapath_type=None):
        """Prometheus labels for ovs_datapath_* (exporter uses type,name)."""
        labels = {
            'name': datapath or self._configured_datapath_name(),
        }
        dtype = (datapath_type if datapath_type is not None
                 else self._configured_datapath_type())
        if dtype:
            labels['type'] = dtype
        return labels

    def _datapath_label_key(self, labels):
        """Stable identity for ovs_datapath_* series."""
        return labels.get('name') or labels.get('datapath')

    def _datapath_headers_from_output(self, output):
        """Return type/name pairs from dpctl/show headers."""
        headers = []
        for line in (output or '').splitlines():
            header = DPCTL_DATAPATH_HEADER_RE.match(line)
            if header:
                headers.append({
                    'type': header.group(1),
                    'name': header.group(2),
                })
        return headers

    def _datapath_headers_on_hypervisor(self, hypervisor_ip):
        return self._datapath_headers_from_output(
            self._ovs_datapath_dpctl_show_output(hypervisor_ip))

    def _datapath_header_matches(self, headers, name, datapath_type=None):
        for header in headers:
            if header['name'] != name:
                continue
            if datapath_type and header['type'] != datapath_type:
                continue
            return True
        return False

    def _datapath_unfiltered_samples(self, hypervisor_ip, metric_name):
        """Return all ovs_datapath_* samples for one hypervisor."""
        samples, _error = self._metric_storage_samples(
            metric_name, hypervisor_ip=hypervisor_ip)
        if samples:
            return samples
        return self._parse_prom_samples(
            self._scrape_compute_metrics_text(hypervisor_ip), metric_name)

    def _sample_datapath_labels(self, labels):
        """Extract type/name labels from a Prometheus sample."""
        name = labels.get('name') or labels.get('datapath')
        if not name:
            return None
        resolved = {'name': name}
        dtype = labels.get('type')
        if dtype:
            resolved['type'] = dtype
        return resolved

    def _resolve_datapath_labels(self, hypervisor_ip, metric_name=None):
        """Pick datapath type/name present in both exporter and dpctl/show."""
        metric_name = metric_name or OVS_DATAPATH_FLOWS_TOTAL_METRIC
        cache = getattr(self, '_datapath_label_cache', None)
        if cache is None:
            cache = {}
            self._datapath_label_cache = cache
        cache_key = (hypervisor_ip, metric_name)
        if cache_key in cache:
            return dict(cache[cache_key])

        configured_name = self._configured_datapath_name()
        configured_type = self._configured_datapath_type()
        headers = self._datapath_headers_on_hypervisor(hypervisor_ip)
        sample_labels = []
        for sample in self._datapath_unfiltered_samples(
                hypervisor_ip, metric_name):
            labels = self._sample_datapath_labels(sample['labels'])
            if labels and labels not in sample_labels:
                sample_labels.append(labels)

        resolved = None
        if self._datapath_header_matches(
                headers, configured_name, configured_type):
            resolved = {
                'name': configured_name,
                'type': configured_type,
            }
        elif self._datapath_header_matches(headers, configured_name):
            resolved = next(
                header for header in headers
                if header['name'] == configured_name)
        else:
            for candidate in sample_labels:
                if self._datapath_header_matches(
                        headers, candidate['name'],
                        candidate.get('type')):
                    resolved = dict(candidate)
                    break
            if resolved is None and len(headers) == 1:
                resolved = dict(headers[0])
            elif resolved is None and sample_labels:
                resolved = dict(sample_labels[0])

        if resolved is None:
            self.fail(
                'Could not resolve ovs_datapath type/name on %s. '
                'dpctl/show headers: %s; metric samples: %s; configured '
                '%s@%s' % (
                    hypervisor_ip,
                    ['%s@%s' % (h['type'], h['name']) for h in headers],
                    sample_labels,
                    configured_type, configured_name))

        if (resolved['name'] != configured_name or
                resolved.get('type') != configured_type):
            LOG.warning(
                'Using datapath %s@%s on %s (configured %s@%s not in '
                'dpctl/show)',
                resolved.get('type'), resolved['name'], hypervisor_ip,
                configured_type, configured_name)

        cache[cache_key] = dict(resolved)
        return dict(resolved)

    def _ovs_datapath_dpctl_show_output(self, hypervisor_ip):
        """Fetch dpctl/show text (same source as openstack-network-exporter)."""
        last_output = ''
        for cmd in DPCTL_SHOW_COMMANDS:
            output = self._ssh_run_on_hypervisor(
                hypervisor_ip, cmd, check_rc=False)
            if output and output.strip():
                return output
            last_output = output or last_output
        self.fail(
            'Could not get dpctl/show output on %s (last output: %r)' % (
                hypervisor_ip, (last_output or '')[:500]))

    def _ovs_datapath_dpctl_stats(self, hypervisor_ip, datapath=None,
                                  datapath_type=None):
        """Parse ovs-appctl dpctl/show for flows and lookup counters."""
        datapath = datapath or self._configured_datapath_name()
        datapath_type = (datapath_type if datapath_type is not None
                         else self._configured_datapath_type())
        output = self._ovs_datapath_dpctl_show_output(hypervisor_ip)
        current_type = ''
        current_name = ''
        found_headers = []
        target_stats = None

        for line in output.splitlines():
            header = DPCTL_DATAPATH_HEADER_RE.match(line)
            if header:
                current_type, current_name = header.group(1), header.group(2)
                found_headers.append('%s@%s' % (current_type, current_name))
                continue

            if current_name != datapath:
                continue
            if datapath_type and current_type != datapath_type:
                continue

            if target_stats is None:
                target_stats = {}

            lookups = DPCTL_LOOKUPS_LINE_RE.match(line)
            if lookups:
                target_stats.update(
                    hit=int(lookups.group(1)),
                    missed=int(lookups.group(2)),
                    lost=int(lookups.group(3)))
                continue
            flows = DPCTL_FLOWS_LINE_RE.match(line)
            if flows:
                target_stats['flows'] = int(flows.group(1))

        if (target_stats and 'flows' in target_stats and
                'hit' in target_stats):
            return target_stats

        self.fail(
            'Datapath %s@%s not found in dpctl/show on %s (seen %s; output: %r)'
            % (datapath_type or '*', datapath, hypervisor_ip,
               found_headers, output[:800]))

    def _datapath_live_samples(self, hypervisor_ip, metric_name, datapath=None,
                               datapath_type=None):
        """Return ovs_datapath_* samples from live :9105 on the hypervisor."""
        if datapath is None and datapath_type is None:
            required_labels = self._resolve_datapath_labels(
                hypervisor_ip, metric_name)
        else:
            required_labels = self._datapath_required_labels(
                datapath=datapath, datapath_type=datapath_type)
        prom = self._prom_compute_metric_value(
            hypervisor_ip, metric_name, required_labels)
        if prom is not None:
            return [{'labels': required_labels, 'value': prom}]
        return self._parse_prom_samples(
            self._scrape_compute_metrics_text(hypervisor_ip), metric_name,
            required_labels=required_labels)

    def _datapath_live_sample_map(self, hypervisor_ip, metric_name,
                                  datapath=None, datapath_type=None):
        """Map datapath name label -> value from live :9105."""
        return {
            self._datapath_label_key(sample['labels']): sample['value']
            for sample in self._datapath_live_samples(
                hypervisor_ip, metric_name, datapath=datapath,
                datapath_type=datapath_type)
        }

    def _datapath_live_metric_value(self, hypervisor_ip, metric_name,
                                    datapath=None, datapath_type=None):
        """Return one ovs_datapath_* value from live :9105."""
        if datapath is None and datapath_type is None:
            labels = self._resolve_datapath_labels(
                hypervisor_ip, metric_name)
            datapath = labels['name']
            datapath_type = labels.get('type')
        sample_map = self._datapath_live_sample_map(
            hypervisor_ip, metric_name, datapath=datapath,
            datapath_type=datapath_type)
        datapath = datapath or self._configured_datapath_name()
        if datapath in sample_map:
            return sample_map[datapath]
        if len(sample_map) == 1:
            return next(iter(sample_map.values()))
        return None

    def _datapath_samples(self, hypervisor_ip, metric_name, datapath=None,
                          datapath_type=None):
        """Return datapath samples from metric-storage for one hypervisor."""
        if datapath is None and datapath_type is None:
            required_labels = self._resolve_datapath_labels(
                hypervisor_ip, metric_name)
        else:
            required_labels = self._datapath_required_labels(
                datapath=datapath, datapath_type=datapath_type)
        samples, error = self._metric_storage_samples(
            metric_name, hypervisor_ip=hypervisor_ip,
            required_labels=required_labels)
        if samples:
            return samples
        LOG.warning(
            'No %s samples in metric-storage for datapath %s on %s (%s); '
            'trying live :9105 scrape',
            metric_name, required_labels, hypervisor_ip, error)
        prom = self._prom_compute_metric_value(
            hypervisor_ip, metric_name, required_labels)
        if prom is not None:
            return [{'labels': required_labels, 'value': prom}]
        return self._parse_prom_samples(
            self._scrape_compute_metrics_text(hypervisor_ip), metric_name,
            required_labels=required_labels)

    def _datapath_sample_map(self, hypervisor_ip, metric_name, datapath=None,
                             datapath_type=None):
        """Map datapath name label -> value from metric-storage Prometheus."""
        return {
            self._datapath_label_key(sample['labels']): sample['value']
            for sample in self._datapath_samples(
                hypervisor_ip, metric_name, datapath=datapath,
                datapath_type=datapath_type)
        }

    def _datapath_metric_value(self, hypervisor_ip, metric_name, datapath=None,
                               datapath_type=None):
        """Return a single datapath series value for hypervisor_ip."""
        if datapath is None and datapath_type is None:
            labels = self._resolve_datapath_labels(
                hypervisor_ip, metric_name)
            datapath = labels['name']
            datapath_type = labels.get('type')
        sample_map = self._datapath_sample_map(
            hypervisor_ip, metric_name, datapath=datapath,
            datapath_type=datapath_type)
        datapath = datapath or self._configured_datapath_name()
        if datapath in sample_map:
            return sample_map[datapath]
        if len(sample_map) == 1:
            return next(iter(sample_map.values()))
        return None

    def _assert_datapath_metric_reported(self, metric_name):
        """Assert ovs_datapath_* via metric show, :9105, and metric-storage."""
        self._assert_ovs_interface_metric_reported(metric_name)
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for datapath metric %s' %
            metric_name)
        last_exc = None
        for hypervisor_ip in hypervisors:
            try:
                self._assert_datapath_matches_dpctl(
                    hypervisor_ip, metric_name)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Datapath dpctl check failed on %s for %s: %s',
                    hypervisor_ip, metric_name, exc)
        self.fail(
            'Datapath metric %s did not match dpctl/show on any hypervisor '
            '%s; last error: %s' % (metric_name, hypervisors, last_exc))

    def _assert_datapath_matches_dpctl(self, hypervisor_ip, metric_name,
                                       datapath=None, datapath_type=None):
        """Assert live :9105 datapath metric matches dpctl/show on hypervisor."""
        if datapath is None and datapath_type is None:
            labels = self._resolve_datapath_labels(
                hypervisor_ip, metric_name)
            datapath = labels['name']
            datapath_type = labels.get('type')
        else:
            datapath = datapath or self._configured_datapath_name()
            if datapath_type is None:
                datapath_type = self._configured_datapath_type()
        dpctl_key = OVS_DATAPATH_METRIC_TO_DPCTL_KEY.get(metric_name)
        self.assertIsNotNone(
            dpctl_key,
            'No dpctl mapping for datapath metric %s' % metric_name)
        datapath_label = '%s@%s' % (datapath_type or '*', datapath)
        last_exc = None
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            dpctl_stats = self._ovs_datapath_dpctl_stats(
                hypervisor_ip, datapath, datapath_type=datapath_type)
            expected = dpctl_stats[dpctl_key]
            reported = self._datapath_live_metric_value(
                hypervisor_ip, metric_name, datapath=datapath,
                datapath_type=datapath_type)
            try:
                self.assertIsNotNone(
                    reported,
                    '%s missing on live :9105 for datapath %s on %s' % (
                        metric_name, datapath_label, hypervisor_ip))
                self.assertEqual(
                    expected, reported,
                    '%s on %s datapath %s: dpctl %s=%s live exporter=%s' % (
                        metric_name, hypervisor_ip, datapath_label, dpctl_key,
                        expected, reported))
                LOG.warning(
                    'Datapath %s aligned with dpctl/show on %s (attempt %s): '
                    '%s=%s',
                    metric_name, hypervisor_ip, attempt + 1, dpctl_key,
                    expected)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for datapath %s on %s to match '
                    'dpctl/show: %s',
                    attempt + 1, METRIC_RETRY_ATTEMPTS, metric_name,
                    hypervisor_ip, exc)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        if last_exc is not None:
            raise last_exc
        self.fail(
            'Timed out waiting for datapath %s on %s to match dpctl/show' % (
                metric_name, hypervisor_ip))

    def _pmd_perf_show_output(self, hypervisor_ip):
        """Fetch dpif-netdev/pmd-perf-show (same source as network-exporter)."""
        last_output = ''
        for cmd in PMD_PERF_SHOW_COMMANDS:
            output = self._ssh_run_on_hypervisor(
                hypervisor_ip, cmd, check_rc=False)
            if output and output.strip():
                return output
            last_output = output or last_output
        self.fail(
            'Could not get pmd-perf-show on %s (last output: %r)' % (
                hypervisor_ip, (last_output or '')[:500]))

    def _ovs_pmd_perf_stats_map(self, hypervisor_ip, output=None):
        """Map (numa, cpu) -> pmd-perf-show stat name -> value."""
        if output is None:
            output = self._pmd_perf_show_output(hypervisor_ip)
        result = {}
        numa = cpu = None
        for line in output.splitlines():
            thread = PMD_THREAD_RE.match(line)
            if thread:
                numa, cpu = thread.group(1), thread.group(2)
                result[(numa, cpu)] = {}
                continue
            if numa is None:
                continue
            stat = PMD_PERF_STAT_RE.match(line)
            if stat:
                result[(numa, cpu)][stat.group(1).strip()] = int(stat.group(2))
        return result

    def _pmd_perf_and_live_output(self, hypervisor_ip):
        """Fetch pmd-perf-show and :9105 metrics in one SSH round-trip."""
        cmd = (
            'sudo ovs-appctl dpif-netdev/pmd-perf-show 2>/dev/null; '
            'curl -sk https://127.0.0.1:9105/metrics 2>/dev/null || '
            'curl -s http://127.0.0.1:9105/metrics 2>/dev/null')
        return self._ssh_run_on_hypervisor(hypervisor_ip, cmd, check_rc=False)

    def _pmd_alignment_allowed_delta(self, expected, reported):
        pct = CONF.nfv_plugin_options.network_exporter_pmd_alignment_tolerance_pct
        floor = CONF.nfv_plugin_options.network_exporter_pmd_alignment_min_delta
        largest = max(abs(expected), abs(reported), 1)
        return max(floor, int(largest * pct / 100.0))

    def _pmd_values_aligned(self, expected, reported):
        """PMD counters advance between perf-show and exporter reads."""
        if expected == reported:
            return True
        return abs(expected - reported) <= self._pmd_alignment_allowed_delta(
            expected, reported)

    def _pmd_thread_key(self, labels):
        return labels.get('numa'), labels.get('cpu')

    def _pmd_live_samples(self, hypervisor_ip, metric_name, metrics_output=None):
        """Return all ovs_pmd_* samples from live :9105 on one hypervisor."""
        if metrics_output is None:
            metrics_output = self._scrape_compute_metrics_text(hypervisor_ip)
        return self._parse_prom_samples(metrics_output, metric_name)

    def _pmd_live_total(self, hypervisor_ip, metric_name,
                        metrics_output=None):
        """Sum ovs_pmd_* across all PMD threads on one hypervisor."""
        return sum(
            sample['value']
            for sample in self._pmd_live_samples(
                hypervisor_ip, metric_name, metrics_output=metrics_output))

    def _pmd_perf_total(self, hypervisor_ip, metric_name):
        """Sum one pmd-perf-show stat across all PMD threads."""
        stat_name = OVS_PMD_METRIC_TO_PERF_STAT.get(metric_name)
        if not stat_name:
            return 0
        return sum(
            stats.get(stat_name, 0)
            for stats in self._ovs_pmd_perf_stats_map(hypervisor_ip).values())

    def _hypervisors_with_pmd_perf(self, hypervisors):
        """Return hypervisors that export pmd-perf-show stats."""
        found = []
        for hypervisor_ip in hypervisors:
            try:
                if self._ovs_pmd_perf_stats_map(hypervisor_ip):
                    found.append(hypervisor_ip)
            except Exception as exc:
                LOG.warning(
                    'No pmd-perf-show on %s: %s', hypervisor_ip, exc)
        return found

    def _assert_pmd_matches_perf_show(self, hypervisor_ip, metric_name):
        """Assert live :9105 ovs_pmd_* matches pmd-perf-show on hypervisor."""
        stat_name = OVS_PMD_METRIC_TO_PERF_STAT.get(metric_name)
        self.assertIsNotNone(
            stat_name, 'No pmd-perf-show mapping for metric %s' % metric_name)
        last_exc = None
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            combined = self._pmd_perf_and_live_output(hypervisor_ip)
            perf_map = self._ovs_pmd_perf_stats_map(
                hypervisor_ip, output=combined)
            live_by_key = {
                self._pmd_thread_key(sample['labels']): sample['value']
                for sample in self._pmd_live_samples(
                    hypervisor_ip, metric_name, metrics_output=combined)
            }
            try:
                self.assertNotEmpty(
                    perf_map,
                    'pmd-perf-show empty on %s' % hypervisor_ip)
                self.assertNotEmpty(
                    live_by_key,
                    '%s missing on live :9105 on %s' % (
                        metric_name, hypervisor_ip))
                for thread_key, stats in perf_map.items():
                    if stat_name not in stats:
                        continue
                    expected = stats[stat_name]
                    reported = live_by_key.get(thread_key)
                    if reported is None:
                        continue
                    allowed = self._pmd_alignment_allowed_delta(
                        expected, reported)
                    self.assertTrue(
                        self._pmd_values_aligned(expected, reported),
                        '%s on %s numa=%s cpu=%s: perf-show %s=%s live=%s '
                        '(delta %s, allowed %s)' % (
                            metric_name, hypervisor_ip, thread_key[0],
                            thread_key[1], stat_name, expected, reported,
                            abs(expected - reported), allowed))
                    LOG.warning(
                        'PMD %s aligned with pmd-perf-show on %s numa=%s '
                        'cpu=%s (attempt %s): perf=%s live=%s delta=%s',
                        metric_name, hypervisor_ip, thread_key[0],
                        thread_key[1], attempt + 1, expected, reported,
                        abs(expected - reported))
                    return
                self.fail(
                    '%s on %s: no pmd-perf-show thread matched live :9105 '
                    '(perf threads %s, live threads %s)' % (
                        metric_name, hypervisor_ip,
                        list(perf_map.keys()), list(live_by_key.keys())))
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for PMD %s on %s: %s',
                    attempt + 1, METRIC_RETRY_ATTEMPTS, metric_name,
                    hypervisor_ip, exc)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        if last_exc is not None:
            raise last_exc
        self.fail(
            'Timed out waiting for PMD %s on %s to match pmd-perf-show' % (
                metric_name, hypervisor_ip))

    def _assert_pmd_metric_reported(self, metric_name):
        """Assert ovs_pmd_* via metric show, :9105, and pmd-perf-show."""
        self._assert_ovs_interface_metric_reported(metric_name)
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for PMD metric %s' % metric_name)
        pmd_hypervisors = self._hypervisors_with_pmd_perf(hypervisors)
        self.assertNotEmpty(
            pmd_hypervisors,
            'No DPDK pmd-perf-show output on hypervisors %s for %s' % (
                hypervisors, metric_name))
        last_exc = None
        for hypervisor_ip in pmd_hypervisors:
            try:
                self._assert_pmd_matches_perf_show(
                    hypervisor_ip, metric_name)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'PMD perf-show check failed on %s for %s: %s',
                    hypervisor_ip, metric_name, exc)
        self.fail(
            'PMD metric %s did not match pmd-perf-show on any hypervisor %s; '
            'last error: %s' % (metric_name, pmd_hypervisors, last_exc))

    def _pmd_rxq_show_output(self, hypervisor_ip):
        """Fetch dpif-netdev/pmd-rxq-show (same source as network-exporter)."""
        last_output = ''
        for cmd in PMD_RXQ_SHOW_COMMANDS:
            output = self._ssh_run_on_hypervisor(
                hypervisor_ip, cmd, check_rc=False)
            if output and output.strip():
                return output
            last_output = output or last_output
        self.fail(
            'Could not get pmd-rxq-show on %s (last output: %r)' % (
                hypervisor_ip, (last_output or '')[:500]))

    def _ovs_pmd_rxq_stats(self, hypervisor_ip, output=None):
        """Parse pmd-rxq-show into overhead and per-Rxq usage percentages."""
        if output is None:
            output = self._pmd_rxq_show_output(hypervisor_ip)
        overhead = {}
        rxq_usage = {}
        numa = cpu = None
        for line in output.splitlines():
            thread = PMD_THREAD_RE.match(line)
            if thread:
                numa, cpu = thread.group(1), thread.group(2)
                continue
            if numa is None:
                continue
            oh = PMD_RXQ_OVERHEAD_RE.match(line)
            if oh:
                overhead[(numa, cpu)] = float(oh.group(1))
                continue
            rxq = PMD_RXQ_USAGE_RE.match(line)
            if rxq:
                interface = rxq.group(1)
                queue_id = rxq.group(2)
                usage = float(rxq.group(4))
                rxq_usage[(numa, cpu, interface, queue_id)] = usage
        return overhead, rxq_usage

    def _pmd_rxq_and_live_output(self, hypervisor_ip):
        """Fetch pmd-rxq-show and :9105 metrics in one SSH round-trip."""
        cmd = (
            'sudo ovs-appctl dpif-netdev/pmd-rxq-show 2>/dev/null; '
            'curl -sk https://127.0.0.1:9105/metrics 2>/dev/null || '
            'curl -s http://127.0.0.1:9105/metrics 2>/dev/null')
        return self._ssh_run_on_hypervisor(hypervisor_ip, cmd, check_rc=False)

    def _pmd_gauge_tolerance(self):
        return CONF.nfv_plugin_options.network_exporter_pmd_gauge_tolerance

    def _pmd_gauge_values_aligned(self, expected, reported):
        return abs(expected - reported) <= self._pmd_gauge_tolerance()

    def _pmd_rxq_label_key(self, labels):
        return (
            labels.get('numa'), labels.get('cpu'),
            labels.get('interface'), labels.get('rxq'))

    def _parse_prom_float_samples(self, metrics_output, metric_name,
                                  required_labels=None):
        """Parse Prometheus exposition text into float label/value samples."""
        samples = []
        for line in (metrics_output or '').splitlines():
            match = PROM_METRIC_LINE_RE.match(line.strip())
            if not match or match.group('metric') != metric_name:
                continue
            labels = dict(PROM_LABEL_RE.findall(match.group('labels')))
            if required_labels and any(
                    labels.get(key) != value
                    for key, value in required_labels.items()):
                continue
            samples.append({
                'labels': labels,
                'value': float(match.group('value')),
            })
        return samples

    def _hypervisors_with_pmd_rxq(self, hypervisors):
        """Return hypervisors that export pmd-rxq-show stats."""
        found = []
        for hypervisor_ip in hypervisors:
            try:
                overhead, rxq_usage = self._ovs_pmd_rxq_stats(hypervisor_ip)
                if overhead or rxq_usage:
                    found.append(hypervisor_ip)
            except Exception as exc:
                LOG.warning(
                    'No pmd-rxq-show on %s: %s', hypervisor_ip, exc)
        return found

    def _pmd_rxq_aligned_on_output(self, hypervisor_ip, metric_name, combined):
        """Return True when live :9105 gauge matches pmd-rxq-show in output."""
        overhead, rxq_usage = self._ovs_pmd_rxq_stats(
            hypervisor_ip, output=combined)
        live_samples = self._parse_prom_float_samples(combined, metric_name)
        if not live_samples:
            return False
        if metric_name == OVS_PMD_CPU_OVERHEAD_METRIC:
            if not overhead:
                return False
            live_by_key = {
                self._pmd_thread_key(sample['labels']): sample['value']
                for sample in live_samples}
            for thread_key, expected in overhead.items():
                reported = live_by_key.get(thread_key)
                if reported is None:
                    continue
                if self._pmd_gauge_values_aligned(expected, reported):
                    return True
            return False
        if metric_name == OVS_PMD_RXQ_USAGE_METRIC:
            if not rxq_usage:
                return False
            live_by_key = {
                self._pmd_rxq_label_key(sample['labels']): sample['value']
                for sample in live_samples}
            for rxq_key, expected in rxq_usage.items():
                reported = live_by_key.get(rxq_key)
                if reported is None:
                    continue
                if self._pmd_gauge_values_aligned(expected, reported):
                    return True
            return False
        return False

    def _assert_pmd_rxq_matches_show(self, hypervisor_ip, metric_name):
        """Assert live :9105 ovs_pmd_* gauge matches pmd-rxq-show."""
        last_exc = None
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            combined = self._pmd_rxq_and_live_output(hypervisor_ip)
            try:
                if self._pmd_rxq_aligned_on_output(
                        hypervisor_ip, metric_name, combined):
                    LOG.warning(
                        'PMD %s aligned with pmd-rxq-show on %s (attempt %s)',
                        metric_name, hypervisor_ip, attempt + 1)
                    return
                self.fail(
                    '%s on %s: no pmd-rxq-show series matched live :9105 '
                    'within tolerance %s' % (
                        metric_name, hypervisor_ip,
                        self._pmd_gauge_tolerance()))
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for PMD %s on %s: %s',
                    attempt + 1, METRIC_RETRY_ATTEMPTS, metric_name,
                    hypervisor_ip, exc)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        if last_exc is not None:
            raise last_exc
        self.fail(
            'Timed out waiting for PMD %s on %s to match pmd-rxq-show' % (
                metric_name, hypervisor_ip))

    def _assert_pmd_rxq_metric_reported(self, metric_name):
        """Assert ovs_pmd_cpu_overhead/rxq_usage via show and pmd-rxq-show."""
        self._assert_ovs_interface_metric_reported(metric_name)
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for PMD metric %s' % metric_name)
        pmd_hypervisors = self._hypervisors_with_pmd_rxq(hypervisors)
        self.assertNotEmpty(
            pmd_hypervisors,
            'No DPDK pmd-rxq-show output on hypervisors %s for %s' % (
                hypervisors, metric_name))
        last_exc = None
        for hypervisor_ip in pmd_hypervisors:
            try:
                self._assert_pmd_rxq_matches_show(
                    hypervisor_ip, metric_name)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'PMD rxq-show check failed on %s for %s: %s',
                    hypervisor_ip, metric_name, exc)
        self.fail(
            'PMD metric %s did not match pmd-rxq-show on any hypervisor %s; '
            'last error: %s' % (metric_name, pmd_hypervisors, last_exc))

    def _ovs_memory_show_output(self, hypervisor_ip):
        """Fetch ovs-appctl memory/show text (same source as network-exporter)."""
        last_output = ''
        for cmd in MEMORY_SHOW_COMMANDS:
            output = self._ssh_run_on_hypervisor(
                hypervisor_ip, cmd, check_rc=False)
            if output and output.strip():
                return output
            last_output = output or last_output
        self.fail(
            'Could not get memory/show output on %s (last output: %r)' % (
                hypervisor_ip, (last_output or '')[:500]))

    def _ovs_memory_show_stats(self, hypervisor_ip, output=None):
        """Parse memory/show counts for known exporter keys only."""
        if output is None:
            output = self._ovs_memory_show_output(hypervisor_ip)
        known_keys = set(OVS_MEMORY_METRIC_TO_SHOW_KEY.values())
        stats = {}
        for match in MEMORY_COUNT_RE.finditer(output):
            key = match.group(1)
            if key in known_keys:
                stats[key] = int(match.group(2))
        return stats

    def _memory_show_expected_value(self, stats, show_key):
        """Return memory/show count; OVS omits zero-valued keys from output."""
        return stats.get(show_key, 0)

    def _memory_live_metric_value(self, hypervisor_ip, metric_name,
                                  metrics_output=None):
        """Read one unlabeled ovs_memory_* gauge from a :9105 scrape."""
        if metrics_output is None:
            metrics_output = self._scrape_compute_metrics_text(hypervisor_ip)
        value = self._parse_prom_metric_text(
            metrics_output, metric_name, {})
        if value is None:
            return 0
        return value

    def _memory_show_and_live_output(self, hypervisor_ip):
        """Fetch memory/show and :9105 metrics in one SSH round-trip."""
        cmd = (
            "MEMORY_SHOW=$(sudo ovs-appctl memory/show 2>/dev/null || "
            "ovs-appctl memory/show 2>/dev/null); "
            "printf '%%s\\n%s\\n' \"$MEMORY_SHOW\" '%s'; "
            "curl -sk https://127.0.0.1:9105/metrics 2>/dev/null || "
            "curl -s http://127.0.0.1:9105/metrics 2>/dev/null"
            % (MEMORY_METRICS_DELIMITER, MEMORY_METRICS_DELIMITER))
        return self._ssh_run_on_hypervisor(hypervisor_ip, cmd, check_rc=False)

    def _split_memory_show_and_metrics(self, combined_output):
        """Split combined SSH output into memory/show text and :9105 scrape."""
        if MEMORY_METRICS_DELIMITER in (combined_output or ''):
            show_output, metrics_output = combined_output.split(
                MEMORY_METRICS_DELIMITER, 1)
            return show_output.strip(), metrics_output.strip()
        return (combined_output or '').strip(), ''

    def _hypervisors_with_memory_show(self, hypervisors):
        """Return hypervisors that respond to ovs-appctl memory/show."""
        found = []
        for hypervisor_ip in hypervisors:
            try:
                for cmd in MEMORY_SHOW_COMMANDS:
                    output = self._ssh_run_on_hypervisor(
                        hypervisor_ip, cmd, check_rc=False)
                    if output and output.strip():
                        found.append(hypervisor_ip)
                        break
            except Exception as exc:
                LOG.warning(
                    'No memory/show on %s: %s', hypervisor_ip, exc)
        return found

    def _assert_memory_matches_show(self, hypervisor_ip, metric_name):
        """Assert live :9105 ovs_memory_* equals memory/show on hypervisor."""
        show_key = OVS_MEMORY_METRIC_TO_SHOW_KEY.get(metric_name)
        self.assertIsNotNone(
            show_key, 'No memory/show mapping for metric %s' % metric_name)
        last_exc = None
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            combined = self._memory_show_and_live_output(hypervisor_ip)
            show_output, metrics_output = self._split_memory_show_and_metrics(
                combined)
            if not show_output:
                show_output = self._ovs_memory_show_output(hypervisor_ip)
            if not metrics_output:
                metrics_output = self._scrape_compute_metrics_text(
                    hypervisor_ip)
            stats = self._ovs_memory_show_stats(
                hypervisor_ip, output=show_output)
            expected = self._memory_show_expected_value(stats, show_key)
            reported = self._memory_live_metric_value(
                hypervisor_ip, metric_name, metrics_output=metrics_output)
            try:
                self.assertEqual(
                    expected, reported,
                    '%s on %s: memory/show %s=%s live=%s (show output: %r)' % (
                        metric_name, hypervisor_ip, show_key,
                        expected, reported, show_output[:300]))
                LOG.warning(
                    'Memory %s aligned with memory/show on %s (attempt %s): '
                    'show=%s live=%s',
                    metric_name, hypervisor_ip, attempt + 1,
                    expected, reported)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for memory %s on %s: %s',
                    attempt + 1, METRIC_RETRY_ATTEMPTS, metric_name,
                    hypervisor_ip, exc)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        if last_exc is not None:
            raise last_exc
        self.fail(
            'Timed out waiting for memory %s on %s to match memory/show' % (
                metric_name, hypervisor_ip))

    def _assert_memory_metric_reported(self, metric_name):
        """Assert ovs_memory_* via metric show, :9105, and memory/show."""
        self._assert_ovs_interface_metric_reported(metric_name)
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for memory metric %s' % (
                metric_name))
        memory_hypervisors = self._hypervisors_with_memory_show(hypervisors)
        self.assertNotEmpty(
            memory_hypervisors,
            'No memory/show output on hypervisors %s for %s' % (
                hypervisors, metric_name))
        last_exc = None
        for hypervisor_ip in memory_hypervisors:
            try:
                self._assert_memory_matches_show(
                    hypervisor_ip, metric_name)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'memory/show check failed on %s for %s: %s',
                    hypervisor_ip, metric_name, exc)
        self.fail(
            'Memory metric %s did not match memory/show on any hypervisor '
            '%s; last error: %s' % (
                metric_name, memory_hypervisors, last_exc))

    def _ovs_open_vswitch_external_ids(self, hypervisor_ip):
        """Parse Open_vSwitch external_ids from ovs-vsctl on a compute node."""
        last_output = ''
        for cmd in (
                'sudo ovs-vsctl get Open_vSwitch . external_ids 2>/dev/null',
                'ovs-vsctl get Open_vSwitch . external_ids 2>/dev/null'):
            output = self._ssh_run_on_hypervisor(
                hypervisor_ip, cmd, check_rc=False)
            if output and output.strip():
                return dict(OVS_EXTERNAL_ID_RE.findall(output))
            last_output = output or last_output
        self.fail(
            'Could not get Open_vSwitch external_ids on %s (last output: %r)' % (
                hypervisor_ip, (last_output or '')[:500]))

    def _ovnc_bridge_mappings_pairs(self, external_ids):
        """Return (network, bridge) pairs from ovn-bridge-mappings external_id."""
        mappings = external_ids.get('ovn-bridge-mappings', '')
        if not mappings:
            return []
        pairs = []
        for item in mappings.split(','):
            item = item.strip()
            if not item or ':' not in item:
                continue
            network, bridge = item.split(':', 1)
            pairs.append((network.strip(), bridge.strip()))
        return pairs

    def _ovn_coverage_show_output(self, hypervisor_ip):
        """Fetch ovn-controller coverage/show text (exporter ground truth)."""
        last_output = ''
        for cmd in OVN_COVERAGE_SHOW_COMMANDS:
            output = self._ssh_run_on_hypervisor(
                hypervisor_ip, cmd, check_rc=False)
            if output and output.strip():
                return output
            last_output = output or last_output
        self.fail(
            'Could not get ovn-controller coverage/show on %s (last output: %r)'
            % (hypervisor_ip, (last_output or '')[:500]))

    def _ovn_coverage_totals(self, hypervisor_ip, output=None):
        """Parse coverage/show totals for known ovnc_txn_* keys only."""
        if output is None:
            output = self._ovn_coverage_show_output(hypervisor_ip)
        known_keys = set(OVNC_TXN_METRIC_TO_COVERAGE_KEY.values())
        totals = {}
        for line in (output or '').splitlines():
            match = OVN_COVERAGE_TOTAL_RE.match(line.strip())
            if not match:
                continue
            key = match.group(1)
            if key in known_keys:
                totals[key] = int(match.group(2))
        return totals

    def _ovnc_live_samples(self, hypervisor_ip, metric_name,
                           metrics_output=None):
        """Parse labeled/unlabeled OVN :1981 samples for one metric."""
        if metrics_output is not None:
            return self._parse_prom_samples(metrics_output, metric_name)
        samples = []
        for _source, output in self._ovn_metric_scrape_outputs(hypervisor_ip):
            samples.extend(self._parse_prom_samples(output, metric_name))
        return samples

    def _ovnc_live_metric_value(self, hypervisor_ip, metric_name,
                                metrics_output=None):
        """Return peak live :1981 value for an unlabeled ovnc_txn_* counter."""
        samples = self._ovnc_live_samples(
            hypervisor_ip, metric_name, metrics_output=metrics_output)
        if not samples:
            return 0
        return max(sample['value'] for sample in samples)

    def _ovn_coverage_and_live_output(self, hypervisor_ip):
        """Fetch coverage/show and :1981 metrics in one SSH round-trip."""
        cmd = (
            "COVERAGE=$(sudo ovs-appctl -t ovn-controller coverage/show "
            "2>/dev/null || ovs-appctl -t ovn-controller coverage/show "
            "2>/dev/null); "
            "printf '%%s\\n%s\\n' \"$COVERAGE\" '%s'; "
            "curl -sk https://127.0.0.1:1981/metrics 2>/dev/null || "
            "curl -s http://127.0.0.1:1981/metrics 2>/dev/null"
            % (OVN_COVERAGE_METRICS_DELIMITER,
               OVN_COVERAGE_METRICS_DELIMITER))
        return self._ssh_run_on_hypervisor(hypervisor_ip, cmd, check_rc=False)

    def _split_ovn_coverage_and_metrics(self, combined_output):
        """Split combined SSH output into coverage/show text and :1981 scrape."""
        if OVN_COVERAGE_METRICS_DELIMITER in (combined_output or ''):
            coverage_output, metrics_output = combined_output.split(
                OVN_COVERAGE_METRICS_DELIMITER, 1)
            return coverage_output.strip(), metrics_output.strip()
        return (combined_output or '').strip(), ''

    def _hypervisors_with_ovn_controller(self, hypervisors):
        """Return hypervisors with OVN controller config or coverage/show."""
        found = []
        for hypervisor_ip in hypervisors:
            try:
                for cmd in (
                        'sudo ovs-vsctl get Open_vSwitch . external_ids '
                        '2>/dev/null',
                        'ovs-vsctl get Open_vSwitch . external_ids '
                        '2>/dev/null'):
                    output = self._ssh_run_on_hypervisor(
                        hypervisor_ip, cmd, check_rc=False)
                    if output.strip():
                        ext_ids = dict(OVS_EXTERNAL_ID_RE.findall(output))
                        if ext_ids.get('ovn-remote'):
                            found.append(hypervisor_ip)
                            break
                else:
                    for cmd in OVN_COVERAGE_SHOW_COMMANDS:
                        output = self._ssh_run_on_hypervisor(
                            hypervisor_ip, cmd, check_rc=False)
                        if output and output.strip():
                            found.append(hypervisor_ip)
                            break
            except Exception as exc:
                LOG.warning(
                    'No OVN controller on %s: %s', hypervisor_ip, exc)
        return found

    def _expected_ovnc_monitor_all(self, external_ids):
        """Map ovn-monitor-all external_id to exporter gauge 0/1."""
        value = external_ids.get('ovn-monitor-all', '')
        return 1 if value.lower() == 'true' else 0

    def _assert_ovnc_config_matches_external_ids(
            self, hypervisor_ip, metric_name):
        """Assert live :1981 ovnc_* config metrics match ovs-vsctl external_ids."""
        mapping = OVNC_METRIC_TO_EXTERNAL_ID.get(metric_name)
        self.assertIsNotNone(
            mapping, 'No external_ids mapping for metric %s' % metric_name)
        ext_id_key, label_key = mapping
        external_ids = self._ovs_open_vswitch_external_ids(hypervisor_ip)
        live_samples = self._ovnc_live_samples(hypervisor_ip, metric_name)
        self.assertNotEmpty(
            live_samples,
            '%s missing on live OVN :1981 on %s' % (
                metric_name, hypervisor_ip))

        if metric_name == OVNC_MONITOR_ALL_METRIC:
            expected = self._expected_ovnc_monitor_all(external_ids)
            unlabeled = [
                sample['value'] for sample in live_samples
                if not sample['labels']]
            self.assertNotEmpty(
                unlabeled,
                '%s has no unlabeled live samples on %s' % (
                    metric_name, hypervisor_ip))
            reported = unlabeled[0]
            self.assertEqual(
                expected, reported,
                '%s on %s: external_ids %s=%s live=%s' % (
                    metric_name, hypervisor_ip, ext_id_key,
                    external_ids.get(ext_id_key), reported))
            return

        if metric_name == OVNC_BRIDGE_MAPPINGS_METRIC:
            pairs = self._ovnc_bridge_mappings_pairs(external_ids)
            self.assertNotEmpty(
                pairs,
                'ovn-bridge-mappings missing on %s for %s' % (
                    hypervisor_ip, metric_name))
            live_map = {
                (sample['labels'].get('network'),
                 sample['labels'].get('bridge')): sample['value']
                for sample in live_samples
            }
            for network, bridge in pairs:
                reported = live_map.get((network, bridge))
                self.assertIsNotNone(
                    reported,
                    '%s missing network=%s bridge=%s on %s (live labels %s)' % (
                        metric_name, network, bridge, hypervisor_ip,
                        sorted(live_map.keys())))
                self.assertEqual(
                    1, reported,
                    '%s on %s network=%s bridge=%s: expected 1 live=%s' % (
                        metric_name, hypervisor_ip, network, bridge,
                        reported))
            return

        expected_value = external_ids.get(ext_id_key)
        self.assertIsNotNone(
            expected_value,
            '%s missing from external_ids on %s' % (
                ext_id_key, hypervisor_ip))
        matching = [
            sample for sample in live_samples
            if sample['labels'].get(label_key) == expected_value]
        self.assertNotEmpty(
            matching,
            '%s on %s: no live sample with %s=%r (external_ids); live %s' % (
                metric_name, hypervisor_ip, label_key, expected_value,
                [sample['labels'] for sample in live_samples]))
        for sample in matching:
            self.assertEqual(
                1, sample['value'],
                '%s on %s %s=%s: expected gauge 1 live=%s' % (
                    metric_name, hypervisor_ip, label_key, expected_value,
                    sample['value']))

    def _assert_ovnc_config_metric_reported(self, metric_name):
        """Assert ovnc_* config via live :1981 and ovs-vsctl external_ids."""
        self._assert_ovn_controller_metric_best_effort(metric_name)
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for OVN config metric %s' % (
                metric_name))
        ovn_hypervisors = self._hypervisors_with_ovn_controller(hypervisors)
        self.assertNotEmpty(
            ovn_hypervisors,
            'No OVN controller on hypervisors %s for %s' % (
                hypervisors, metric_name))
        if metric_name == OVNC_BRIDGE_MAPPINGS_METRIC:
            ovn_hypervisors = [
                hypervisor_ip for hypervisor_ip in ovn_hypervisors
                if self._ovnc_bridge_mappings_pairs(
                    self._ovs_open_vswitch_external_ids(hypervisor_ip))]
            self.assertNotEmpty(
                ovn_hypervisors,
                'No ovn-bridge-mappings on hypervisors for %s' % metric_name)
        last_exc = None
        for hypervisor_ip in ovn_hypervisors:
            try:
                self._assert_ovnc_config_matches_external_ids(
                    hypervisor_ip, metric_name)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'OVN config check failed on %s for %s: %s',
                    hypervisor_ip, metric_name, exc)
        self.fail(
            'OVN config metric %s did not match external_ids on any '
            'hypervisor %s; last error: %s' % (
                metric_name, ovn_hypervisors, last_exc))

    def _assert_ovnc_txn_matches_coverage(self, hypervisor_ip, metric_name):
        """Assert live :1981 ovnc_txn_* equals coverage/show on hypervisor."""
        coverage_key = OVNC_TXN_METRIC_TO_COVERAGE_KEY.get(metric_name)
        self.assertIsNotNone(
            coverage_key,
            'No coverage/show mapping for metric %s' % metric_name)
        last_exc = None
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            combined = self._ovn_coverage_and_live_output(hypervisor_ip)
            coverage_output, metrics_output = (
                self._split_ovn_coverage_and_metrics(combined))
            if not coverage_output:
                coverage_output = self._ovn_coverage_show_output(hypervisor_ip)
            if not metrics_output:
                for _source, output in self._ovn_metric_scrape_outputs(
                        hypervisor_ip):
                    if output.strip():
                        metrics_output = output
                        break
            totals = self._ovn_coverage_totals(
                hypervisor_ip, output=coverage_output)
            expected = totals.get(coverage_key, 0)
            reported = self._ovnc_live_metric_value(
                hypervisor_ip, metric_name, metrics_output=metrics_output)
            try:
                self.assertEqual(
                    expected, reported,
                    '%s on %s: coverage/show %s=%s live=%s' % (
                        metric_name, hypervisor_ip, coverage_key,
                        expected, reported))
                LOG.warning(
                    'OVN txn %s aligned with coverage/show on %s (attempt %s): '
                    'show=%s live=%s',
                    metric_name, hypervisor_ip, attempt + 1,
                    expected, reported)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for OVN txn %s on %s: %s',
                    attempt + 1, METRIC_RETRY_ATTEMPTS, metric_name,
                    hypervisor_ip, exc)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        if last_exc is not None:
            raise last_exc
        self.fail(
            'Timed out waiting for OVN txn %s on %s to match coverage/show' % (
                metric_name, hypervisor_ip))

    def _assert_ovnc_txn_metric_reported(self, metric_name):
        """Assert ovnc_txn_* via live :1981 and coverage/show."""
        self._assert_ovnc_txn_presence(metric_name)
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for OVN txn metric %s' % (
                metric_name))
        ovn_hypervisors = self._hypervisors_with_ovn_controller(hypervisors)
        self.assertNotEmpty(
            ovn_hypervisors,
            'No OVN controller on hypervisors %s for %s' % (
                hypervisors, metric_name))
        last_exc = None
        for hypervisor_ip in ovn_hypervisors:
            try:
                self._assert_ovnc_txn_matches_coverage(
                    hypervisor_ip, metric_name)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'OVN txn check failed on %s for %s: %s',
                    hypervisor_ip, metric_name, exc)
        self.fail(
            'OVN txn metric %s did not match coverage/show on any hypervisor '
            '%s; last error: %s' % (
                metric_name, ovn_hypervisors, last_exc))

    def _ovnc_txn_success_totals(self, hypervisor_ip):
        """Return (coverage/show total, live :1981 value) for txn_success."""
        combined = self._ovn_coverage_and_live_output(hypervisor_ip)
        coverage_output, metrics_output = (
            self._split_ovn_coverage_and_metrics(combined))
        if not coverage_output:
            coverage_output = self._ovn_coverage_show_output(hypervisor_ip)
        if not metrics_output:
            for _source, output in self._ovn_metric_scrape_outputs(
                    hypervisor_ip):
                if output.strip():
                    metrics_output = output
                    break
        totals = self._ovn_coverage_totals(
            hypervisor_ip, output=coverage_output)
        show_total = totals.get('txn_success', 0)
        live_total = self._ovnc_live_metric_value(
            hypervisor_ip, OVNC_TXN_SUCCESS_METRIC,
            metrics_output=metrics_output)
        return show_total, live_total

    def _max_rxq_usage_percent(self, hypervisor_ip, output=None):
        """Return peak RxQ usage percent from pmd-rxq-show and live :9105."""
        if output is None:
            output = self._pmd_rxq_and_live_output(hypervisor_ip)
        _overhead, rxq_usage = self._ovs_pmd_rxq_stats(
            hypervisor_ip, output=output)
        live_samples = self._parse_prom_float_samples(
            output, OVS_PMD_RXQ_USAGE_METRIC)
        values = list(rxq_usage.values())
        values.extend(sample['value'] for sample in live_samples)
        return max(values) if values else 0.0

    def _pmd_min_packet_threshold(self):
        configured = CONF.nfv_plugin_options.network_exporter_pmd_min_packets
        if configured:
            return configured
        count = CONF.nfv_plugin_options.network_exporter_traffic_ping_count
        tolerance = (
            CONF.nfv_plugin_options.network_exporter_traffic_packet_tolerance_pct)
        return int(count * (100 - tolerance) / 100)

    def _pmd_perf_activity_totals(self, hypervisor_ip):
        """Summed PMD perf counters used as traffic-activity fallback."""
        return {
            'busy': self._pmd_perf_total(
                hypervisor_ip, OVS_PMD_BUSY_ITERATIONS_METRIC),
            'rx': self._pmd_perf_total(
                hypervisor_ip, OVS_PMD_RX_PACKETS_METRIC),
        }

    def _pmd_live_activity_totals(self, hypervisor_ip, metrics_output=None):
        """Summed live :9105 PMD counters for traffic-activity fallback."""
        return {
            'busy': self._pmd_live_total(
                hypervisor_ip, OVS_PMD_BUSY_ITERATIONS_METRIC,
                metrics_output=metrics_output),
            'rx': self._pmd_live_total(
                hypervisor_ip, OVS_PMD_RX_PACKETS_METRIC,
                metrics_output=metrics_output),
        }

    def _assert_metric_on_compute_scrape(self, metric_name):
        """Verify metric_name is exported on at least one compute :9105 scrape."""
        hypervisors = self._get_hypervisor_ip_from_undercloud()
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisor IPs available for :9105 scrape')
        found_on = []
        for hypervisor_ip in hypervisors:
            for line in self._scrape_compute_metrics_text(
                    hypervisor_ip).splitlines():
                stripped = line.strip()
                if stripped.startswith(metric_name + '{') or stripped.startswith(
                        metric_name + ' '):
                    found_on.append(hypervisor_ip)
                    break
        self.assertNotEmpty(
            found_on,
            "Metric '%s' not found on :9105 scrape from hypervisors %s" % (
                metric_name, hypervisors))
        LOG.warning(
            "Metric '%s' found on compute :9105 scrape from %s",
            metric_name, found_on)

    def _assert_metric_reported(self, metric_name, output_markers=None):
        """Wait until metric_name is present in metric-storage Prometheus."""
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
                                    'metric-storage Prometheus returned empty '
                                    'output for %s' % metric_name)
                    LOG.info("Metric '%s' is reported (%s bytes)",
                                metric_name, len(stdout))
                    return stdout
            LOG.warning("Attempt %s/%s for metric '%s' failed: exit %s, "
                        "stderr: %s", attempt + 1, METRIC_RETRY_ATTEMPTS,
                        metric_name, returncode, stderr)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        stdout = stdout or ''
        msg = ("Metric '%s' not found or metric-storage Prometheus query "
               "failed (exit %s). stderr: %s stdout: %s" %
               (metric_name, returncode, stderr, stdout))
        self.assertEqual(0, returncode, msg)
        self.assertIn(metric_name, stdout,
                      "Metric '%s' not present in Prometheus output. stdout: %s"
                      % (metric_name, stdout))
        missing = [m for m in output_markers if m not in stdout]
        self.assertFalse(
            missing,
            "Metric '%s' output missing required markers %s. stdout: %s" %
            (metric_name, missing, stdout))
        return stdout

    def _split_metric_table_row(self, line):
        """Split an openstack metric show table row into columns."""
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
        return any(
            self._text_matches_hypervisor_identifier(row_text, identifier)
            for identifier in self._hypervisor_identifiers(hypervisor_ip))

    def _line_matches_hypervisor(self, line, hypervisor_ip):
        return any(
            self._text_matches_hypervisor_identifier(line, identifier)
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

    def _parse_compute_metric_show_values(
            self, metric_stdout, hypervisor_ip=None, first_column=None,
            row_contains=None):
        """Parse :9105 metric values from openstack metric show output."""
        if not metric_stdout:
            return []
        values = []
        for line in metric_stdout.splitlines():
            if row_contains and row_contains not in line:
                continue
            parts = self._split_metric_table_row(line)
            if parts:
                if first_column and parts[0] != first_column:
                    continue
                if hypervisor_ip and not self._row_matches_hypervisor(
                        parts, hypervisor_ip):
                    continue
                value = self._parse_metric_row_value(parts)
                if value is not None:
                    values.append(value)
                    continue
            if (not parts and row_contains and hypervisor_ip and
                    NETWORK_EXPORTER_INSTANCE_PORT in line and
                    'ovn-controller-metrics' not in line and
                    self._line_matches_hypervisor(line, hypervisor_ip)):
                match = METRIC_ROW_VALUE_RE.search(line.strip())
                if match:
                    values.append(int(match.group(1)))
        return values

    def _parse_compute_metric_show_value(
            self, metric_stdout, metric_name, hypervisor_ip, row_contains=None,
            first_column=None):
        """Return the first matching value for one compute metric row."""
        if not metric_stdout:
            return None
        for line in metric_stdout.splitlines():
            if metric_name not in line:
                continue
            if row_contains and row_contains not in line:
                continue
            if (NETWORK_EXPORTER_INSTANCE_PORT not in line or
                    'ovn-controller-metrics' in line):
                continue
            if hypervisor_ip and not self._line_matches_hypervisor(
                    line, hypervisor_ip):
                continue
            parts = self._split_metric_table_row(line)
            if parts:
                if first_column and parts[0] != first_column:
                    continue
                value = self._parse_metric_row_value(parts)
                if value is not None:
                    return value
            match = METRIC_ROW_VALUE_RE.search(line.strip())
            if match:
                return int(match.group(1))
        return None

    def _parse_metric_values_for_bridge(self, metric_stdout, bridge,
                                        hypervisor_ip=None):
        """Parse bridge metric values from openstack metric show."""
        values = self._parse_compute_metric_show_values(
            metric_stdout, hypervisor_ip=hypervisor_ip, first_column=bridge)
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

    def _ssh_run_on_hypervisor(self, hypervisor_ip, command, check_rc=True):
        """Run command on hypervisor over SSH with connect timeout."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_opts = {
            'allow_agent': False,
            'timeout': SSH_CONNECT_TIMEOUT,
            'banner_timeout': SSH_CONNECT_TIMEOUT,
        }
        user = CONF.nfv_plugin_options.overcloud_node_user
        try:
            if CONF.nfv_plugin_options.overcloud_node_pkey_file_key_object:
                ssh.connect(
                    hypervisor_ip, username=user,
                    pkey=CONF.nfv_plugin_options.
                    overcloud_node_pkey_file_key_object,
                    **connect_opts)
            else:
                ssh.connect(
                    hypervisor_ip, username=user,
                    password=CONF.nfv_plugin_options.overcloud_node_pass,
                    **connect_opts)
            LOG.info("Executing on %s: %s", hypervisor_ip, command)
            _stdin, stdout, stderr = ssh.exec_command(command)
            out = stdout.read().decode('UTF-8')
            err = stderr.read().decode('UTF-8')
            rc = stdout.channel.recv_exit_status()
        except EOFError as exc:
            self.fail(
                'SSH to hypervisor %s failed (connection closed). Error: %s' %
                (hypervisor_ip, exc))
        except Exception as exc:
            self.fail(
                'SSH to hypervisor %s failed running %r: %s' % (
                    hypervisor_ip, command, exc))
        finally:
            ssh.close()
        if check_rc and rc != 0:
            self.fail(
                'SSH command on %s exited %s: %r\nstderr: %s\nstdout: %s' % (
                    hypervisor_ip, rc, command, err, out))
        return out

    def _ssh_run_unchecked_on_hypervisor(self, hypervisor_ip, command):
        """SSH without enforcing exit status (cleanup helpers)."""
        return self._ssh_run_on_hypervisor(
            hypervisor_ip, command, check_rc=False)

    def _list_ovs_bridges_on_hypervisor(self, hypervisor_ip):
        """Return OVS bridge names on a compute hypervisor (ovs-vsctl list-br)."""
        cmd = 'sudo ovs-vsctl list-br 2>/dev/null'
        out = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
        return sorted({line.strip() for line in out.splitlines() if line.strip()})

    def _prom_compute_metric_value(self, hypervisor_ip, metric_name, labels):
        """Read one gauge from the local :9105 scrape matching Prometheus labels."""
        value_pattern = re.compile(
            r'%s\{[^}]*\}\s+(%s)\s*$' % (
                re.escape(metric_name), PROM_NUMBER_RE))
        grep = "grep '^%s{'" % metric_name
        for key in sorted(labels):
            grep += " | grep '%s=\"%s\"'" % (key, labels[key])
        for cmd in (
                "curl -sk https://127.0.0.1:9105/metrics 2>/dev/null | %s"
                % grep,
                "curl -s http://127.0.0.1:9105/metrics 2>/dev/null | %s"
                % grep):
            out = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
            for line in out.splitlines():
                match = value_pattern.search(line.strip())
                if match:
                    return self._parse_prom_number(match.group(1))
        return self._parse_prom_metric_text(
            self._scrape_compute_metrics_text(hypervisor_ip),
            metric_name, labels)

    def _parse_prom_metric_text(self, metrics_output, metric_name, labels):
        """Parse a gauge from Prometheus exposition text."""
        for line in metrics_output.splitlines():
            if not line.startswith(metric_name):
                continue
            if any('%s="%s"' % (key, labels[key]) not in line
                   for key in labels):
                continue
            parts = line.rsplit(None, 1)
            if len(parts) == 2:
                try:
                    return self._parse_prom_number(parts[1])
                except ValueError:
                    continue
        return None

    def _state_test_bridge(self):
        return CONF.nfv_plugin_options.network_exporter_state_test_bridge

    def _state_test_interface(self):
        return CONF.nfv_plugin_options.network_exporter_state_test_interface

    def _veth_peer_name(self, interface):
        """Host-side veth leg (not attached to OVS); must fit IFNAMSIZ."""
        suffix = '-h'
        if len(interface) + len(suffix) < LINUX_MAX_IFNAME_LEN:
            return '%s%s' % (interface, suffix)
        return 'tpst-ovs-pe'

    def _assert_valid_ifnames(self, interface):
        """Fail fast when configured names exceed Linux IFNAMSIZ."""
        peer = self._veth_peer_name(interface)
        for name in (interface, peer):
            if not name or len(name) >= LINUX_MAX_IFNAME_LEN:
                self.fail(
                    'Invalid network_exporter_state_test_interface %r: '
                    'Linux interface names must be 1-%s characters (peer=%r).'
                    % (interface, LINUX_MAX_IFNAME_LEN - 1, peer))

    def _ovs_state_to_metric(self, ovs_value):
        """Map OVS admin_state/link_state strings to exporter gauge values."""
        if ovs_value == 'up':
            return OVS_STATE_UP
        if ovs_value == 'down':
            return OVS_STATE_DOWN
        return -1

    def _ovs_field(self, hypervisor_ip, interface, field):
        raw = self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl get Interface %s %s 2>/dev/null' %
            (interface, field)).strip().strip('"')
        if raw in ('', '[]'):
            return None
        return raw

    def _ovs_interface_states(self, hypervisor_ip, interface):
        """Return (admin_state, link_state) strings from OVSDB."""
        return (self._ovs_field(hypervisor_ip, interface, 'admin_state'),
                self._ovs_field(hypervisor_ip, interface, 'link_state'))

    def _ovs_states_valid(self, ovs_admin, ovs_link):
        return ovs_admin in ('up', 'down') and ovs_link in ('up', 'down')

    def _netdev_exists(self, hypervisor_ip, dev):
        try:
            out = self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip, 'ip link show %s 2>/dev/null' % dev)
        except Exception:
            return False
        return bool(out.strip()) and 'does not exist' not in out

    def _netdev_is_up(self, hypervisor_ip, dev):
        if not self._netdev_exists(hypervisor_ip, dev):
            return False
        out = self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip, 'ip link show %s 2>/dev/null' % dev)
        return 'state UP' in out

    def _set_kernel_link_state(self, hypervisor_ip, dev, state):
        if not self._netdev_exists(hypervisor_ip, dev):
            return
        self._ssh_run_on_hypervisor(
            hypervisor_ip, 'sudo ip link set dev %s %s' % (dev, state))

    def _set_ovs_admin_only(self, hypervisor_ip, interface, state):
        """Set OVS admin_state; mirror veth link (system ports stay up otherwise)."""
        link_state = 'up' if state == 'up' else 'down'
        for dev in (self._veth_peer_name(interface), interface):
            self._set_kernel_link_state(hypervisor_ip, dev, link_state)
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s admin_state=%s' %
            (interface, state))

    def _set_interface_link_state(self, hypervisor_ip, interface, state):
        """Toggle kernel link on veth legs; keep OVS admin up (link metric test)."""
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s admin_state=up' % interface)
        for dev in (self._veth_peer_name(interface), interface):
            self._set_kernel_link_state(hypervisor_ip, dev, state)

    def _ensure_port_up(self, hypervisor_ip, interface):
        """Bring disposable veth and OVS admin up before metric assertions."""
        for dev in (self._veth_peer_name(interface), interface):
            self._set_kernel_link_state(hypervisor_ip, dev, 'up')
        self._set_ovs_admin_only(hypervisor_ip, interface, 'up')

    def _metric_values_match_ovs(self, ovs_value, reported, prom_value):
        if ovs_value not in ('up', 'down'):
            return False
        if None in (reported, prom_value) or -1 in (reported, prom_value):
            return False
        expected = self._ovs_state_to_metric(ovs_value)
        return reported == expected and prom_value == expected

    def _port_bridge(self, hypervisor_ip, interface):
        try:
            out = self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip,
                'sudo ovs-vsctl port-to-br %s 2>/dev/null' % interface)
        except Exception:
            return None
        bridge = out.strip().strip('"')
        return bridge or None

    def _interface_on_bridge(self, hypervisor_ip, bridge, interface):
        return self._port_bridge(hypervisor_ip, interface) == bridge

    def _ovs_interface_diagnostic(self, hypervisor_ip, bridge, interface):
        """Best-effort dump for failure/skip messages (must not raise on SSH)."""
        peer = self._veth_peer_name(interface)
        chunks = []
        for label, cmd in (
                ('Interface', 'sudo ovs-vsctl list Interface %s 2>/dev/null'),
                ('Port', 'sudo ovs-vsctl list Port %s 2>/dev/null'),
                ('ip', 'ip link show %s 2>/dev/null'),
                ('ip-peer', 'ip link show %s 2>/dev/null')):
            name = interface if label != 'ip-peer' else peer
            try:
                out = self._ssh_run_unchecked_on_hypervisor(
                    hypervisor_ip, cmd % name).strip()
            except Exception as exc:
                out = str(exc)
            chunks.append('[%s]\n%s' % (label, out or '(not present)'))
        return '\n'.join(chunks)

    def _cleanup_test_interface(self, hypervisor_ip, interface):
        """Remove stale veth and OVS interface records from any bridge."""
        names = {interface, self._veth_peer_name(interface)}
        names.update(LEGACY_STATE_TEST_INTERFACES)
        for iface in sorted(names):
            self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip,
                'for br in $(sudo ovs-vsctl list-br 2>/dev/null); do '
                'sudo ovs-vsctl --if-exists del-port "$br" %(iface)s; '
                'done; '
                'sudo ovs-vsctl --if-exists destroy Interface %(iface)s; '
                'sudo ovs-vsctl --if-exists destroy Port %(iface)s; '
                'sudo ip link del %(iface)s 2>/dev/null' % {'iface': iface})

    def _state_test_bridge_candidates(self, hypervisor_ip):
        """Bridges to try: configured first, then br-link0, then non-DPDK."""
        preferred = self._state_test_bridge()
        available = self._list_ovs_bridges_on_hypervisor(hypervisor_ip)
        candidates = []
        for bridge in (preferred, 'br-link0'):
            if bridge in available and bridge not in candidates:
                candidates.append(bridge)
        for bridge in available:
            if 'dpdk' in bridge.lower():
                continue
            if bridge not in candidates:
                candidates.append(bridge)
        return candidates

    def _ovs_interface_healthy(self, hypervisor_ip, bridge, interface):
        """True when the veth is attached and OVS reports a real ofport."""
        actual_bridge = self._port_bridge(hypervisor_ip, interface)
        if actual_bridge != bridge:
            return False, 'port not on bridge %s (port-to-br=%r)' % (
                bridge, actual_bridge)
        peer = self._veth_peer_name(interface)
        if not self._netdev_is_up(hypervisor_ip, interface):
            return False, 'kernel netdev %s is not UP' % interface
        ofport = self._ovs_field(hypervisor_ip, interface, 'ofport')
        if ofport is None or int(ofport) < 1:
            return False, 'ofport=%s' % ofport
        error = self._ovs_field(hypervisor_ip, interface, 'error')
        if error:
            return False, 'error=%s' % error
        admin, link = self._ovs_interface_states(hypervisor_ip, interface)
        if not self._ovs_states_valid(admin, link):
            return False, 'admin=%s link=%s' % (admin, link)
        if not self._netdev_is_up(hypervisor_ip, peer):
            return False, 'kernel netdev %s is not UP' % peer
        return True, ''

    def _create_test_interface_on_bridge(self, hypervisor_ip, bridge, interface):
        """Create veth (down), add-port, then bring links up."""
        peer = self._veth_peer_name(interface)
        self._cleanup_test_interface(hypervisor_ip, interface)
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ip link add %(peer)s type veth peer name %(iface)s' % {
                'peer': peer, 'iface': interface})
        self._ssh_run_on_hypervisor(
            hypervisor_ip, 'sudo ip link set %s down' % peer)
        self._ssh_run_on_hypervisor(
            hypervisor_ip, 'sudo ip link set %s down' % interface)
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl add-port %s %s' % (bridge, interface))
        actual_bridge = self._port_bridge(hypervisor_ip, interface)
        if actual_bridge != bridge:
            raise RuntimeError(
                'add-port %s to %s failed (port-to-br=%r)' % (
                    interface, bridge, actual_bridge))
        self._ssh_run_on_hypervisor(
            hypervisor_ip, 'sudo ip link set %s up' % peer)
        self._ssh_run_on_hypervisor(
            hypervisor_ip, 'sudo ip link set %s up' % interface)
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s admin_state=up' % interface)

    def _create_test_interface(self, hypervisor_ip, interface):
        """Attach disposable veth; skip test if no bridge accepts it."""
        failures = []
        for bridge in self._state_test_bridge_candidates(hypervisor_ip):
            try:
                self._create_test_interface_on_bridge(
                    hypervisor_ip, bridge, interface)
                healthy, reason = self._ovs_interface_healthy(
                    hypervisor_ip, bridge, interface)
                if healthy:
                    LOG.warning(
                        'Created veth test port %s on bridge %s on %s',
                        interface, bridge, hypervisor_ip)
                    return bridge
                failures.append('%s: %s' % (bridge, reason))
            except Exception as exc:
                failures.append('%s: %s' % (bridge, exc))
            self._cleanup_test_interface(hypervisor_ip, interface)
        raise unittest.SkipTest(
            'Skipping interface state test: could not attach ephemeral veth '
            '%s on %s (%s). The test only uses disposable tempest ports and '
            'does not toggle existing dataplane interfaces. Tried: %s. '
            'Configure network_exporter_state_test_bridge to a kernel bridge '
            'that accepts system ports, or run on a deployment that allows '
            'manual veth attach. Last dump: %s' % (
                interface, hypervisor_ip, failures,
                self._state_test_bridge_candidates(hypervisor_ip),
                self._ovs_interface_diagnostic(
                    hypervisor_ip, self._state_test_bridge(), interface)))

    def _delete_test_interface(self, hypervisor_ip, bridge, interface):
        peer = self._veth_peer_name(interface)
        try:
            self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip,
                'sudo ovs-vsctl --if-exists del-port %s %s; '
                'sudo ip link del %s 2>/dev/null' % (bridge, interface, peer))
            LOG.warning(
                'Removed test veth %s / %s from bridge %s on %s',
                interface, peer, bridge, hypervisor_ip)
        except Exception as exc:
            LOG.warning('Could not remove test interface %s on %s: %s',
                        interface, hypervisor_ip, exc)

    def _setup_state_test_port(self, metric_name):
        """Assert metric exists, attach disposable veth, register cleanup."""
        metric_stdout = self._assert_metric_reported(metric_name)
        hypervisors = self._get_ssh_hypervisors(metric_stdout)
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors with %s metrics found' %
            NETWORK_EXPORTER_INSTANCE_PORT)
        hypervisor_ip = hypervisors[0]
        interface = self._state_test_interface()
        self._assert_valid_ifnames(interface)
        bridge = self._create_test_interface(hypervisor_ip, interface)
        self._active_state_test_bridge = bridge
        self.addCleanup(
            self._delete_test_interface, hypervisor_ip, bridge, interface)
        self._ensure_port_up(hypervisor_ip, interface)
        return hypervisor_ip, interface

    def _parse_prom_samples(self, metrics_output, metric_name,
                            required_labels=None):
        """Parse Prometheus exposition text into label/value samples."""
        samples = []
        for line in (metrics_output or '').splitlines():
            match = PROM_METRIC_LINE_RE.match(line.strip())
            if not match or match.group('metric') != metric_name:
                continue
            labels = dict(PROM_LABEL_RE.findall(match.group('labels')))
            if required_labels and any(
                    labels.get(key) != value
                    for key, value in required_labels.items()):
                continue
            samples.append({
                'labels': labels,
                'value': self._parse_prom_number(match.group('value')),
            })
        return samples
