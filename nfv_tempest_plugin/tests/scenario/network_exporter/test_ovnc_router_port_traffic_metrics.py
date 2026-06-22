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
            net = dict(network)
            # Attach the ICMP-capable security group to every router-test port,
            # not only mgmt (dataplane ports otherwise keep the default SG).
            if net.get('sec_groups') is not False:
                net['sec_groups'] = True
            filtered.append(net)
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

    def _min_expected_packets(self, transmitted=None):
        count = self._traffic_ping_count()
        tolerance = (
            CONF.nfv_plugin_options.
            network_exporter_router_traffic_packet_tolerance_pct)
        configured = int(count * (100 - tolerance) / 100)
        floor = CONF.nfv_plugin_options.network_exporter_router_traffic_min_packets
        if transmitted:
            scale_pct = (
                CONF.nfv_plugin_options.
                network_exporter_router_traffic_metric_scale_pct)
            scaled = max(floor, int(transmitted * scale_pct / 100))
            return min(configured, scaled)
        return max(floor, configured)

    def _min_expected_bytes(self, min_packets=None):
        min_packets = (min_packets if min_packets is not None else
                       self._min_expected_packets())
        return (min_packets *
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

    @staticmethod
    def _same_ipv4_subnet(ip_a, ip_b):
        return ip_a.split('.')[:3] == ip_b.split('.')[:3]

    def _cross_subnet_peer_candidates(self, sender, receiver):
        """Return ordered (bind_ip, peer_ip) pairs that traverse the router.

        Prefer mgmt -> dataplane: binding to an external IP and pinging a
        directly-connected mgmt address often fails because Linux selects the
        mgmt interface for the destination subnet.
        """
        mgmt_net_id = self._mgmt_network_id()
        scored = []
        seen = set()
        for s_net in sender['provider_networks']:
            for r_net in receiver['provider_networks']:
                if s_net['network_id'] == r_net['network_id']:
                    continue
                bind_ip = s_net['ip_address']
                peer_ip = r_net['ip_address']
                if self._same_ipv4_subnet(bind_ip, peer_ip):
                    continue
                key = (bind_ip, peer_ip)
                if key in seen:
                    continue
                seen.add(key)
                s_mgmt = mgmt_net_id and s_net['network_id'] == mgmt_net_id
                r_mgmt = mgmt_net_id and r_net['network_id'] == mgmt_net_id
                if s_mgmt and not r_mgmt:
                    score = 0
                elif not s_mgmt and not r_mgmt:
                    score = 1
                elif s_mgmt and r_mgmt:
                    score = 2
                else:
                    score = 3
                scored.append((score, bind_ip, peer_ip))
        scored.sort(key=lambda item: item[0])
        return [(bind_ip, peer_ip) for _, bind_ip, peer_ip in scored]

    def _router_traffic_endpoints(self, ssh_sender, ssh_receiver, sender,
                                  receiver):
        """Return ordered (ssh, bind_ip, peer_ip) triples for router traffic."""
        probed = []
        fallback = []
        seen = set()
        for bind_ip, peer_ip in self._cross_subnet_peer_candidates(
                sender, receiver):
            forward = (bind_ip, peer_ip)
            reverse = (peer_ip, bind_ip)
            if forward not in seen:
                LOG.warning(
                    'Probing cross-subnet pair bind %s -> %s', bind_ip, peer_ip)
                if self._probe_bound_ping(ssh_sender, bind_ip, peer_ip):
                    LOG.warning('Probe OK (sender) %s -> %s', bind_ip, peer_ip)
                    probed.append((ssh_sender, bind_ip, peer_ip))
                    seen.add(forward)
                    continue
                if self._probe_bound_ping(ssh_receiver, peer_ip, bind_ip):
                    LOG.warning(
                        'Probe OK (receiver) %s -> %s', peer_ip, bind_ip)
                    probed.append((ssh_receiver, peer_ip, bind_ip))
                    seen.add(reverse)
                    continue
                fallback.append((ssh_sender, bind_ip, peer_ip))
                seen.add(forward)
        if probed:
            return probed + [
                ep for ep in fallback
                if (ep[1], ep[2]) not in {(p[1], p[2]) for p in probed}]
        if fallback:
            LOG.warning(
                'No cross-subnet ICMP probe succeeded; using ranked '
                'candidates (ensure sec_groups on all router test-networks)')
            return fallback
        return []

    def _send_router_cross_subnet_traffic(self, endpoints, packet_count):
        """Generate L3 ICMP through the Neutron router for port counter tests."""
        if not endpoints:
            self.fail('No cross-subnet traffic endpoints between VMs')
        last_err = None
        for ssh, bind_ip, peer_ip in endpoints:
            LOG.warning(
                'Sending %d bound ICMP (xmit-only) from %s to %s',
                packet_count, bind_ip, peer_ip)
            try:
                total_xmit = self._send_ping_packets_bound(
                    ssh, bind_ip, peer_ip, packet_count,
                    min_packets=1, accept_xmit_only=True)
                LOG.warning(
                    'Transmitted %d ICMP probes on pair %s -> %s',
                    total_xmit, bind_ip, peer_ip)
                return ssh, bind_ip, peer_ip, total_xmit
            except AssertionError as exc:
                last_err = exc
                LOG.warning(
                    'Cross-subnet xmit failed for %s -> %s: %s',
                    bind_ip, peer_ip, exc)
        self.fail(
            'All cross-subnet traffic endpoints failed to transmit ICMP: %s' %
            last_err)

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

    def _router_port_delta_summary(self, pkts_after, pkts_before,
                                   bytes_after, bytes_before):
        pkts_delta = self._sample_map_deltas(pkts_after, pkts_before)
        bytes_delta = self._sample_map_deltas(bytes_after, bytes_before)
        pkts_key, pkts_peak = self._best_delta_key(pkts_delta)
        bytes_key, bytes_peak = self._best_delta_key(bytes_delta)
        return {
            'pkts_key': pkts_key,
            'pkts_delta': sum(pkts_delta.values()),
            'pkts_peak_delta': pkts_peak,
            'pkts_by_port': pkts_delta,
            'bytes_key': bytes_key,
            'bytes_delta': sum(bytes_delta.values()),
            'bytes_peak_delta': bytes_peak,
            'bytes_by_port': bytes_delta,
        }

    def _wait_for_router_traffic_counters(self, hypervisor_ip, baseline_pkts,
                                          baseline_bytes, min_packets=None,
                                          min_bytes=None, ssh_traffic=None,
                                          packet_count=None,
                                          traffic_pair=None):
        min_packets = (min_packets if min_packets is not None else
                       self._min_expected_packets())
        min_bytes = (min_bytes if min_bytes is not None else
                     self._min_expected_bytes(min_packets))
        last_exc = None
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            if (attempt > 0 and ssh_traffic is not None and traffic_pair and
                    packet_count):
                bind_ip, peer_ip = traffic_pair
                LOG.warning(
                    'Re-sending router traffic (attempt %s/%s) %s -> %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    bind_ip, peer_ip)
                self._send_ping_packets_bound(
                    ssh_traffic, bind_ip, peer_ip, packet_count,
                    min_packets=1, accept_xmit_only=True)
            pkts_after = self._router_port_sample_map(
                hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_PKTS_METRIC)
            bytes_after = self._router_port_sample_map(
                hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_BYTES_METRIC)
            last = self._router_port_delta_summary(
                pkts_after, baseline_pkts, bytes_after, baseline_bytes)
            try:
                self.assertNotEqual(last['pkts_delta'], 0,
                                    'ovnc_router_port_traffic_pkts delta %s' %
                                    last)
                self.assertGreaterEqual(
                    last['pkts_delta'], min_packets,
                    'ovnc_router_port_traffic_pkts delta %s' % last)
                self.assertGreaterEqual(
                    last['bytes_delta'], min_bytes,
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
        """Boot two VMs on routed subnets, send L3 traffic, verify counters."""
        servers, key_pair = self._boot_router_traffic_vms()
        self.assertEqual(2, len(servers), 'Test requires exactly two VMs')
        if not servers[0].get('provider_networks'):
            if self.test_all_provider_networks and servers[0].get('fip'):
                self.verify_provider_networks(servers, key_pair)
            else:
                self._populate_provider_networks(servers)

        sender, receiver = servers[0], servers[1]
        hypervisor_ip = sender['hypervisor_ip']
        ssh_sender = self.get_remote_client(
            sender['fip'], self.instance_user, key_pair['private_key'])
        ssh_receiver = self.get_remote_client(
            receiver['fip'], self.instance_user, key_pair['private_key'])
        endpoints = self._router_traffic_endpoints(
            ssh_sender, ssh_receiver, sender, receiver)
        if not endpoints:
            self.fail(
                'No cross-subnet IPs between VMs %s and %s' % (
                    sender.get('name', sender['id']),
                    receiver.get('name', receiver['id'])))
        packet_count = self._traffic_ping_count()

        baseline_pkts = self._router_port_sample_map(
            hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_PKTS_METRIC)
        baseline_bytes = self._router_port_sample_map(
            hypervisor_ip, metrics_base.OVNC_ROUTER_PORT_TRAFFIC_BYTES_METRIC)

        ssh_traffic, bind_ip, peer_ip, total_xmit = (
            self._send_router_cross_subnet_traffic(endpoints, packet_count))
        min_packets = self._min_expected_packets(transmitted=total_xmit)
        min_bytes = self._min_expected_bytes(min_packets)
        LOG.warning(
            'Router traffic test: %s bind %s -> %s (hypervisor %s), xmit %s, '
            'min pkts %s',
            sender.get('name', sender['id']), bind_ip, peer_ip, hypervisor_ip,
            total_xmit, min_packets)

        result = self._wait_for_router_traffic_counters(
            hypervisor_ip, baseline_pkts, baseline_bytes,
            min_packets=min_packets, min_bytes=min_bytes,
            ssh_traffic=ssh_traffic, packet_count=packet_count,
            traffic_pair=(bind_ip, peer_ip))
        LOG.warning(
            'Router port traffic OK: pkts total +%s (peak port %s +%s), '
            'bytes total +%s (peak port %s +%s)',
            result['pkts_delta'], result['pkts_key'],
            result['pkts_peak_delta'], result['bytes_delta'],
            result['bytes_key'], result['bytes_peak_delta'])
