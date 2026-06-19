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


class TestOvncRouterPortTrafficMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovnc_router_port_traffic_* counters with cross-subnet VM traffic."""

    TEST_NAME = 'network_exporter_router_traffic'

    def _ensure_test_setup(self):
        if self.TEST_NAME not in self.test_setup_dict:
            self.test_setup_dict[self.TEST_NAME] = {
                'flavor-id': self.flavor_ref,
                'router': True,
                'aggregate': None,
            }

    def _filter_router_test_networks(self, test_networks):
        filtered = []
        for network in test_networks:
            if network.get('port_type') == 'direct':
                continue
            filtered.append(network)
        non_mgmt = [net for net in filtered if not net.get('mgmt')]
        if len(non_mgmt) < 1:
            raise unittest.SkipTest(
                'Need mgmt plus at least one normal test-network for %s' %
                self.TEST_NAME)
        cidrs = {net.get('cidr') for net in filtered if net.get('cidr')}
        if len(cidrs) < 2:
            raise unittest.SkipTest(
                'Need at least two test-networks with distinct CIDRs for %s '
                '(mgmt + one normal net with a different subnet, e.g. vlan1500 '
                'external plus vlan1600 mgmt).' % self.TEST_NAME)
        LOG.warning(
            'Router traffic metrics will create test-networks: %s',
            [net.get('name') for net in filtered])
        return filtered

    def _build_router_boot_kwargs(self):
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
            'test_networks_only': True,
            'srv_details': srv_details,
        }

    def _boot_router_traffic_vms(self):
        self._ensure_test_setup()
        boot_kwargs = self._build_router_boot_kwargs()
        full_test_networks = self.external_config['test-networks']
        self.external_config['test-networks'] = self._filter_router_test_networks(
            full_test_networks)
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

    def _min_expected_bytes(self):
        return (self._min_expected_packets() *
                CONF.nfv_plugin_options.network_exporter_traffic_min_bytes_per_packet)

    def _slow_ping_cap(self):
        return min(
            self._traffic_ping_count(),
            int(metrics_base.PING_MAX_WALL_SECONDS /
                metrics_base.PING_SLOW_INTERVAL_SEC))

    def _effective_traffic_expectations(self, ssh_sender):
        """Return (min_packets, min_bytes) achievable on this guest."""
        min_packets = self._min_expected_packets()
        min_bytes = self._min_expected_bytes()
        if self._guest_has_passwordless_sudo(ssh_sender):
            return min_packets, min_bytes
        capped = self._slow_ping_cap()
        if capped >= min_packets:
            return min_packets, min_bytes
        LOG.warning(
            'No passwordless sudo on sender; expecting >= %d router pkts/bytes '
            '(slow ping cap %d, configured min pkts %d)',
            capped, capped, min_packets)
        return capped, (
            capped *
            CONF.nfv_plugin_options.network_exporter_traffic_min_bytes_per_packet)

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

    def _cross_subnet_peer_ips(self, sender, receiver):
        """Return (sender_bind_ip, receiver_ip) on different subnets."""
        mgmt_net_id = self._mgmt_network_id()
        recv_ip = None
        for r_net in receiver['provider_networks']:
            if mgmt_net_id and r_net['network_id'] == mgmt_net_id:
                recv_ip = r_net['ip_address']
                break
        if not recv_ip:
            recv_ip = receiver['provider_networks'][0]['ip_address']
        recv_net_id = None
        for r_net in receiver['provider_networks']:
            if r_net['ip_address'] == recv_ip:
                recv_net_id = r_net['network_id']
                break
        send_bind_ip = None
        for s_net in sender['provider_networks']:
            if recv_net_id and s_net['network_id'] != recv_net_id:
                send_bind_ip = s_net['ip_address']
                break
        if not send_bind_ip:
            for s_net in sender['provider_networks']:
                if mgmt_net_id and s_net['network_id'] != mgmt_net_id:
                    send_bind_ip = s_net['ip_address']
                    break
        if not send_bind_ip or not recv_ip:
            self.fail(
                'Could not find cross-subnet IPs between VMs %s and %s. '
                'Ensure mgmt and at least one other routed network.' % (
                    sender.get('name', sender['id']),
                    receiver.get('name', receiver['id'])))
        if send_bind_ip.split('.')[:3] == recv_ip.split('.')[:3]:
            self.fail(
                'Sender bind %s and receiver %s appear to share a subnet; '
                'router traffic test requires different subnets.' % (
                    send_bind_ip, recv_ip))
        return send_bind_ip, recv_ip

    def _sample_map_deltas(self, after, before):
        deltas = {}
        for key, value in after.items():
            deltas[key] = value - before.get(key, 0)
        return deltas

    def _best_delta_key(self, deltas):
        if not deltas:
            return None, 0
        key = max(deltas, key=deltas.get)
        return key, deltas[key]

    def _wait_for_router_traffic_counters(self, hypervisor_ip, baseline_pkts,
                                          baseline_bytes, min_packets=None,
                                          min_bytes=None):
        min_packets = (min_packets if min_packets is not None else
                       self._min_expected_packets())
        min_bytes = (min_bytes if min_bytes is not None else
                     self._min_expected_bytes())
        last_exc = None
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            pkts_after = self._router_port_sample_map(
                hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_PKTS_METRIC)
            bytes_after = self._router_port_sample_map(
                hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_BYTES_METRIC)
            pkts_delta = self._sample_map_deltas(pkts_after, baseline_pkts)
            bytes_delta = self._sample_map_deltas(bytes_after, baseline_bytes)
            pkts_key, pkts_inc = self._best_delta_key(pkts_delta)
            bytes_key, bytes_inc = self._best_delta_key(bytes_delta)
            last = {
                'pkts_key': pkts_key,
                'pkts_delta': pkts_inc,
                'bytes_key': bytes_key,
                'bytes_delta': bytes_inc,
            }
            try:
                self.assertIsNotNone(pkts_key)
                self.assertGreaterEqual(
                    pkts_inc, min_packets,
                    'ovnc_router_port_traffic_pkts delta %s' % last)
                self.assertGreaterEqual(
                    bytes_inc, min_bytes,
                    'ovnc_router_port_traffic_bytes delta %s' % last)
                LOG.warning(
                    'Router port counters increased (attempt %s): %s',
                    attempt + 1, last)
                return last
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for router port counters: %s; %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    exc, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for ovnc_router_port_traffic counters after '
            'cross-subnet traffic. Last %s; last error: %s' % (last, last_exc))

    # --- Presence ---

    def test_ovnc_router_port_traffic_pkts_reported(self):
        """Verify ovnc_router_port_traffic_pkts in metric-storage."""
        self._assert_router_port_metric_reported(
            metrics_base.OVNC_ROUTER_PORT_TRAFFIC_PKTS_METRIC)

    def test_ovnc_router_port_traffic_bytes_reported(self):
        """Verify ovnc_router_port_traffic_bytes in metric-storage."""
        self._assert_router_port_metric_reported(
            metrics_base.OVNC_ROUTER_PORT_TRAFFIC_BYTES_METRIC)

    # --- Traffic ---

    def test_ovnc_router_port_traffic_increments_with_cross_subnet_traffic(self):
        """Boot two VMs on routed subnets, ping across router, verify counters."""
        servers, key_pair = self._boot_router_traffic_vms()
        self.assertEqual(2, len(servers), 'Test requires exactly two VMs')
        if not servers[0].get('provider_networks'):
            if self.test_all_provider_networks and servers[0].get('fip'):
                self.verify_provider_networks(servers, key_pair)
            else:
                self._populate_provider_networks(servers)

        sender, receiver = servers[0], servers[1]
        bind_ip, peer_ip = self._cross_subnet_peer_ips(sender, receiver)
        hypervisor_ip = sender['hypervisor_ip']
        LOG.warning(
            'Router traffic test: %s ping -I %s -> %s (hypervisor %s), count %s',
            sender.get('name', sender['id']), bind_ip, peer_ip, hypervisor_ip,
            self._traffic_ping_count())

        baseline_pkts = self._router_port_sample_map(
            hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_PKTS_METRIC)
        baseline_bytes = self._router_port_sample_map(
            hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_BYTES_METRIC)

        ssh_sender = self.get_remote_client(
            sender['fip'], self.instance_user, key_pair['private_key'])
        min_packets, min_bytes = self._effective_traffic_expectations(
            ssh_sender)
        self._send_ping_packets_bound(
            ssh_sender, bind_ip, peer_ip, self._traffic_ping_count(),
            min_packets)

        result = self._wait_for_router_traffic_counters(
            hypervisor_ip, baseline_pkts, baseline_bytes,
            min_packets=min_packets, min_bytes=min_bytes)
        LOG.warning(
            'Router port traffic OK: pkts series %s +%s, bytes series %s +%s',
            result['pkts_key'], result['pkts_delta'],
            result['bytes_key'], result['bytes_delta'])
