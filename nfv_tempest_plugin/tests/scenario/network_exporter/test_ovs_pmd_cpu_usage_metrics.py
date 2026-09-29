#!/usr/bin/env python
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

import time
import unittest

from tempest import config

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))


class TestOvsPmdCpuUsageMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_pmd_cpu_overhead and ovs_pmd_rxq_usage (Test Suite 5.3)."""

    TEST_NAME = 'network_exporter_pmd_cpu_usage'

    def _ensure_test_setup(self):
        if self.TEST_NAME not in self.test_setup_dict:
            self.test_setup_dict[self.TEST_NAME] = {
                'flavor-id': self.flavor_ref,
                'router': True,
                'aggregate': None,
            }

    def _filter_pmd_cpu_usage_test_networks(self, test_networks):
        filtered = []
        for network in test_networks:
            if network.get('mgmt'):
                filtered.append(network)
                continue
            if network.get('port_type') == 'direct':
                continue
            filtered.append(network)
        if not any(not net.get('mgmt') for net in filtered):
            raise unittest.SkipTest(
                'No shared normal provider test-network in tempest_config.yml '
                'for %s. Add a test-network with port_type: normal (e.g. '
                'tag: external) in addition to mgmt.' % self.TEST_NAME)
        LOG.warning(
            'PMD CPU usage metrics will create test-networks: %s',
            [net.get('name') for net in filtered])
        return filtered

    def _build_pmd_cpu_usage_boot_kwargs(self):
        ports_filter = 'external,normal'
        srv_details = {
            0: {'ports_filter': ports_filter},
            1: {'ports_filter': ports_filter},
        }
        hypervisor = CONF.nfv_plugin_options.target_hypervisor
        if hypervisor:
            for index in srv_details:
                srv_details[index]['availability_zone'] = 'nova:%s' % hypervisor
        return {
            'num_servers': 2,
            'mgmt_subnet_only': True,
            'srv_details': srv_details,
        }

    def _boot_pmd_cpu_usage_vms(self):
        self._ensure_test_setup()
        boot_kwargs = self._build_pmd_cpu_usage_boot_kwargs()
        full_test_networks = self.external_config['test-networks']
        self.external_config['test-networks'] = (
            self._filter_pmd_cpu_usage_test_networks(full_test_networks))
        try:
            return self.create_and_verify_resources(
                test=self.TEST_NAME, **boot_kwargs)
        finally:
            self.external_config['test-networks'] = full_test_networks

    def _traffic_ping_count(self):
        return CONF.nfv_plugin_options.network_exporter_traffic_ping_count

    def _min_expected_packets(self):
        count = self._traffic_ping_count()
        tolerance = (
            CONF.nfv_plugin_options.network_exporter_traffic_packet_tolerance_pct)
        return int(count * (100 - tolerance) / 100)

    def _populate_provider_networks(self, servers):
        for server in servers:
            server['provider_networks'] = server.get('trunk_networks', [])
            server['provider_networks'] += server.get('transparent_networks', [])
            ports = self.os_admin.ports_client.list_ports(
                device_id=server['id'])['ports']
            for port in ports:
                if not port.get('fixed_ips'):
                    continue
                provider_dict = {
                    'network_id': port['network_id'],
                    'mac_address': port['mac_address'],
                    'ip_address': port['fixed_ips'][0]['ip_address'],
                    'port_id': port['id'],
                }
                server['provider_networks'].append(provider_dict)

    def _mgmt_network_id(self):
        mgmt_name = getattr(self, 'mgmt_network', None)
        if not mgmt_name:
            return None
        return self.test_network_dict.get(mgmt_name, {}).get('net-id')

    def _dataplane_peer_ip(self, sender, receiver):
        mgmt_net_id = self._mgmt_network_id()
        shared = []
        for recv_net in receiver['provider_networks']:
            for send_net in sender['provider_networks']:
                if recv_net['network_id'] == send_net['network_id']:
                    shared.append(recv_net)
                    break
        if not shared:
            self.fail(
                'No shared provider network between VMs %s and %s' % (
                    sender.get('name', sender['id']),
                    receiver.get('name', receiver['id'])))
        for net in shared:
            if mgmt_net_id and net['network_id'] == mgmt_net_id:
                continue
            return net['ip_address']
        return shared[0]['ip_address']

    def _wait_for_pmd_cpu_usage_with_traffic(self, hypervisor_ip,
                                             baseline_max_rxq, perf_baseline):
        min_rxq = CONF.nfv_plugin_options.network_exporter_pmd_min_rxq_usage_pct
        min_busy = CONF.nfv_plugin_options.network_exporter_pmd_min_iterations
        min_rx = self._pmd_min_packet_threshold()
        peak_rxq = baseline_max_rxq
        peak_busy = perf_baseline['busy']
        peak_rx = perf_baseline['rx']
        last_exc = None
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            combined = self._pmd_rxq_and_live_output(hypervisor_ip)
            overhead, rxq_usage = self._ovs_pmd_rxq_stats(
                hypervisor_ip, output=combined)
            current_max_rxq = self._max_rxq_usage_percent(
                hypervisor_ip, output=combined)
            peak_rxq = max(peak_rxq, current_max_rxq)
            perf_current = self._pmd_perf_activity_totals(hypervisor_ip)
            live_current = self._pmd_live_activity_totals(
                hypervisor_ip, metrics_output=combined)
            peak_busy = max(
                peak_busy, perf_current['busy'], live_current['busy'])
            peak_rx = max(peak_rx, perf_current['rx'], live_current['rx'])
            busy_delta = peak_busy - perf_baseline['busy']
            rx_delta = peak_rx - perf_baseline['rx']
            last = {
                'baseline_max_rxq': baseline_max_rxq,
                'peak_max_rxq': peak_rxq,
                'min_rxq_required': min_rxq,
                'busy_delta': busy_delta,
                'rx_delta': rx_delta,
                'min_busy_required': min_busy,
                'min_rx_required': min_rx,
                'overhead_threads': len(overhead),
                'rxq_series': len(rxq_usage),
            }
            try:
                rxq_ok = (
                    (min_rxq > 0 and peak_rxq >= min_rxq) or
                    peak_rxq > baseline_max_rxq)
                perf_ok = busy_delta >= min_busy or rx_delta >= min_rx
                self.assertTrue(
                    rxq_ok or perf_ok,
                    'No PMD CPU usage activity after traffic (need rxq peak '
                    '>=%s or busy +%s or rx +%s): %s' % (
                        min_rxq, min_busy, min_rx, last))
                for metric_name in metrics_base.OVS_PMD_CPU_USAGE_METRICS:
                    self.assertTrue(
                        self._pmd_rxq_aligned_on_output(
                            hypervisor_ip, metric_name, combined),
                        '%s not aligned with pmd-rxq-show: %s' % (
                            metric_name, last))
                last['activity'] = 'rxq_usage' if rxq_ok else 'pmd_perf'
                LOG.warning(
                    'PMD CPU usage validated via %s (attempt %s): %s',
                    last['activity'], attempt + 1, last)
                return last
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for PMD CPU usage: %s; %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    exc, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for ovs_pmd CPU usage metrics after traffic. '
            'Last %s; last error: %s' % (last, last_exc))

    def test_ovs_pmd_cpu_overhead_reported(self):
        """Verify ovs_pmd_cpu_overhead on compute and metric-storage."""
        self._assert_pmd_rxq_metric_reported(
            metrics_base.OVS_PMD_CPU_OVERHEAD_METRIC)

    def test_ovs_pmd_rxq_usage_reported(self):
        """Verify ovs_pmd_rxq_usage on compute and metric-storage."""
        self._assert_pmd_rxq_metric_reported(
            metrics_base.OVS_PMD_RXQ_USAGE_METRIC)

    def test_ovs_pmd_cpu_usage_with_vm_traffic(self):
        """Boot two VMs, send ICMP, verify PMD overhead and RxQ usage."""
        servers, key_pair = self._boot_pmd_cpu_usage_vms()
        self.assertEqual(2, len(servers), 'Test requires exactly two VMs')
        if not servers[0].get('provider_networks'):
            if self.test_all_provider_networks and servers[0].get('fip'):
                self.verify_provider_networks(servers, key_pair)
            else:
                self._populate_provider_networks(servers)

        sender, receiver = servers[0], servers[1]
        hypervisor_ip = sender['hypervisor_ip']
        peer_ip = self._dataplane_peer_ip(sender, receiver)
        baseline_max_rxq = self._max_rxq_usage_percent(hypervisor_ip)
        perf_baseline = self._pmd_perf_activity_totals(hypervisor_ip)
        LOG.warning(
            'PMD CPU usage test: %s -> %s on %s, baseline max rxq usage %s%%, '
            'perf baseline %s, ping count %s',
            sender.get('name', sender['id']), peer_ip, hypervisor_ip,
            baseline_max_rxq, perf_baseline, self._traffic_ping_count())

        ssh_sender = self.get_remote_client(
            sender['fip'], self.instance_user, key_pair['private_key'])
        self._send_ping_packets(
            ssh_sender, peer_ip, self._traffic_ping_count(),
            self._min_expected_packets())

        result = self._wait_for_pmd_cpu_usage_with_traffic(
            hypervisor_ip, baseline_max_rxq, perf_baseline)
        LOG.warning(
            'PMD CPU usage OK via %s: peak rxq %s%% busy +%s rx +%s',
            result.get('activity', 'unknown'),
            result['peak_max_rxq'], result['busy_delta'], result['rx_delta'])
