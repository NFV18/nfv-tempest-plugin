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


class TestOvsPmdIterationMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_pmd_* iteration counters with VM traffic (Test Suite 5.1)."""

    TEST_NAME = 'network_exporter_pmd_iterations'

    def _ensure_test_setup(self):
        if self.TEST_NAME not in self.test_setup_dict:
            self.test_setup_dict[self.TEST_NAME] = {
                'flavor-id': self.flavor_ref,
                'router': True,
                'aggregate': None,
            }

    def _filter_pmd_iteration_test_networks(self, test_networks):
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
            'PMD iteration metrics will create test-networks: %s',
            [net.get('name') for net in filtered])
        return filtered

    def _build_pmd_iteration_boot_kwargs(self):
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

    def _boot_pmd_iteration_vms(self):
        self._ensure_test_setup()
        boot_kwargs = self._build_pmd_iteration_boot_kwargs()
        full_test_networks = self.external_config['test-networks']
        self.external_config['test-networks'] = (
            self._filter_pmd_iteration_test_networks(full_test_networks))
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

    def _baseline_iteration_totals(self, hypervisor_ip):
        return {
            metric: self._pmd_live_total(hypervisor_ip, metric)
            for metric in metrics_base.OVS_PMD_ITERATION_METRICS
        }

    def _perf_iteration_totals(self, hypervisor_ip):
        return {
            metric: self._pmd_perf_total(hypervisor_ip, metric)
            for metric in metrics_base.OVS_PMD_ITERATION_METRICS
        }

    def _wait_for_pmd_iteration_increase(self, hypervisor_ip, live_baseline,
                                       perf_baseline):
        min_inc = CONF.nfv_plugin_options.network_exporter_pmd_min_iterations
        peaks = dict(live_baseline)
        last_exc = None
        last = {}
        key_metrics = (
            metrics_base.OVS_PMD_TOTAL_ITERATIONS_METRIC,
            metrics_base.OVS_PMD_BUSY_ITERATIONS_METRIC,
        )
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            current = {
                metric: self._pmd_live_total(hypervisor_ip, metric)
                for metric in metrics_base.OVS_PMD_ITERATION_METRICS
            }
            for metric, value in current.items():
                peaks[metric] = max(peaks.get(metric, 0), value)
            perf_current = self._perf_iteration_totals(hypervisor_ip)
            live_deltas = {
                metric: peaks[metric] - live_baseline.get(metric, 0)
                for metric in metrics_base.OVS_PMD_ITERATION_METRICS
            }
            perf_deltas = {
                metric: perf_current[metric] - perf_baseline.get(metric, 0)
                for metric in metrics_base.OVS_PMD_ITERATION_METRICS
            }
            last = {
                'live_baseline': live_baseline,
                'live_current': current,
                'live_peaks': dict(peaks),
                'live_deltas': live_deltas,
                'perf_baseline': perf_baseline,
                'perf_current': perf_current,
                'perf_deltas': perf_deltas,
            }
            try:
                for metric in key_metrics:
                    increased = (
                        live_deltas[metric] >= min_inc or
                        perf_deltas[metric] >= min_inc)
                    self.assertTrue(
                        increased,
                        '%s did not increase by %s via live :9105 or '
                        'pmd-perf-show: %s' % (metric, min_inc, last))
                for metric in key_metrics:
                    self._assert_pmd_matches_perf_show(
                        hypervisor_ip, metric)
                LOG.warning(
                    'PMD iteration counters increased (attempt %s): %s',
                    attempt + 1, last)
                return last
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for PMD iterations: %s; %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    exc, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for ovs_pmd_* iteration increase. '
            'Last %s; last error: %s' % (last, last_exc))

    def test_ovs_pmd_total_iterations_reported(self):
        """Verify ovs_pmd_total_iterations on compute and metric-storage."""
        self._assert_pmd_metric_reported(
            metrics_base.OVS_PMD_TOTAL_ITERATIONS_METRIC)

    def test_ovs_pmd_idle_iterations_reported(self):
        """Verify ovs_pmd_idle_iterations on compute and metric-storage."""
        self._assert_pmd_metric_reported(
            metrics_base.OVS_PMD_IDLE_ITERATIONS_METRIC)

    def test_ovs_pmd_busy_iterations_reported(self):
        """Verify ovs_pmd_busy_iterations on compute and metric-storage."""
        self._assert_pmd_metric_reported(
            metrics_base.OVS_PMD_BUSY_ITERATIONS_METRIC)

    def test_ovs_pmd_iterations_increase_with_vm_traffic(self):
        """Boot two VMs, send ICMP, verify PMD iteration counters grow."""
        servers, key_pair = self._boot_pmd_iteration_vms()
        self.assertEqual(2, len(servers), 'Test requires exactly two VMs')
        if not servers[0].get('provider_networks'):
            if self.test_all_provider_networks and servers[0].get('fip'):
                self.verify_provider_networks(servers, key_pair)
            else:
                self._populate_provider_networks(servers)

        sender, receiver = servers[0], servers[1]
        hypervisor_ip = sender['hypervisor_ip']
        peer_ip = self._dataplane_peer_ip(sender, receiver)
        live_baseline = self._baseline_iteration_totals(hypervisor_ip)
        perf_baseline = self._perf_iteration_totals(hypervisor_ip)
        LOG.warning(
            'PMD iteration test: %s -> %s on %s, live baseline %s, '
            'perf baseline %s, ping count %s',
            sender.get('name', sender['id']), peer_ip, hypervisor_ip,
            live_baseline, perf_baseline, self._traffic_ping_count())

        ssh_sender = self.get_remote_client(
            sender['fip'], self.instance_user, key_pair['private_key'])
        self._send_ping_packets(
            ssh_sender, peer_ip, self._traffic_ping_count(),
            self._min_expected_packets())

        result = self._wait_for_pmd_iteration_increase(
            hypervisor_ip, live_baseline, perf_baseline)
        LOG.warning(
            'PMD iterations OK: total +%s busy +%s (live peaks %s)',
            result['live_deltas'][
                metrics_base.OVS_PMD_TOTAL_ITERATIONS_METRIC],
            result['live_deltas'][
                metrics_base.OVS_PMD_BUSY_ITERATIONS_METRIC],
            result['live_peaks'])
