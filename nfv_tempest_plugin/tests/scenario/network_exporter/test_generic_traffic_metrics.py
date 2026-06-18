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

OVS_INTERFACE_STAT_KEYS = tuple(metrics_base.OVS_INTERFACE_STAT_TO_METRIC.keys())


class TestGenericTrafficMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_interface rx/tx packet and byte counters with VM traffic."""

    TEST_NAME = 'network_exporter_generic_traffic'

    def _ensure_test_setup(self):
        """Default test config when tests-setup omits this test name."""
        if self.TEST_NAME not in self.test_setup_dict:
            self.test_setup_dict[self.TEST_NAME] = {
                'flavor-id': self.flavor_ref,
                'router': True,
                'aggregate': None,
            }

    def _filter_generic_traffic_test_networks(self, test_networks):
        """Keep mgmt + normal OVS dataplane networks; skip direct SR-IOV ports."""
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
            'Generic traffic metrics will create test-networks: %s',
            [net.get('name') for net in filtered])
        return filtered

    def _build_generic_traffic_boot_kwargs(self):
        """Boot kwargs: mgmt router only, normal + external ports for dataplane."""
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

    def _boot_generic_traffic_vms(self):
        """Create networks, ports, and boot two VMs for dataplane traffic."""
        self._ensure_test_setup()
        boot_kwargs = self._build_generic_traffic_boot_kwargs()
        LOG.warning(
            'Booting VMs for %s with ports_filter=%s mgmt_subnet_only=True',
            self.TEST_NAME, boot_kwargs['srv_details'][0]['ports_filter'])
        full_test_networks = self.external_config['test-networks']
        self.external_config['test-networks'] = (
            self._filter_generic_traffic_test_networks(full_test_networks))
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

    def _populate_provider_networks(self, servers):
        """Attach provider network metadata used to find peer IPs."""
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
                network = [val for val in self.test_network_dict.values()
                           if port['network_id'] in val['net-id']]
                if len(network) == 1:
                    provider_dict['provider:network_type'] = \
                        network[0]['provider:network_type']
                server['provider_networks'].append(provider_dict)
                if self.external_resources_data is not None:
                    server = self.map_external_provider_network_types(server)

    def _mgmt_network_id(self):
        mgmt_name = getattr(self, 'mgmt_network', None)
        if not mgmt_name:
            return None
        return self.test_network_dict.get(mgmt_name, {}).get('net-id')

    def _dataplane_peer_ip(self, sender, receiver):
        """Return receiver data-plane IP on a network shared with sender."""
        mgmt_net_id = self._mgmt_network_id()
        shared = []
        for recv_net in receiver['provider_networks']:
            for send_net in sender['provider_networks']:
                if recv_net['network_id'] == send_net['network_id']:
                    shared.append(recv_net)
                    break
        if not shared:
            self.fail(
                'No shared provider network between VMs %s and %s; cannot '
                'send dataplane traffic. Configure test-networks with a common '
                'provider net and enable provider connectivity.' % (
                    sender.get('name', sender['id']),
                    receiver.get('name', receiver['id'])))
        for net in shared:
            if mgmt_net_id and net['network_id'] == mgmt_net_id:
                continue
            return net['ip_address']
        return shared[0]['ip_address']

    def _resolve_ovs_interface(self, server):
        """Return OVS interface name for the VM dataplane port."""
        if server.get('other_port'):
            return server['other_port']
        mgmt_net_id = self._mgmt_network_id()
        provider_networks = list(server.get('provider_networks', []))
        if mgmt_net_id:
            dataplane = [
                net for net in provider_networks
                if net.get('network_id') != mgmt_net_id]
            if dataplane:
                provider_networks = dataplane + [
                    net for net in provider_networks
                    if net.get('network_id') == mgmt_net_id]
        for net in provider_networks:
            port_id = net.get('port_id')
            if not port_id:
                continue
            cmd = (
                'sudo ovs-vsctl --bare --columns=name find Interface '
                'external-ids:iface-id=%s 2>/dev/null' % port_id)
            out = self._ssh_run_on_hypervisor(
                server['hypervisor_ip'], cmd).strip()
            if out:
                return out.strip('"')
        self.fail(
            'Could not resolve OVS interface for VM %s on %s' % (
                server.get('name', server['id']), server['hypervisor_ip']))

    def _ovs_interface_stats(self, hypervisor_ip, interface):
        stats = self.get_ovs_interface_statistics(
            [interface], hypervisor=hypervisor_ip)
        return stats[interface]

    def _storage_counter_value(self, hypervisor_ip, metric_name, interface):
        """Read one ovs_interface counter from metric-storage."""
        samples, query_error = self._metric_storage_samples(
            metric_name, hypervisor_ip=hypervisor_ip,
            required_labels={'interface': interface})
        if not samples:
            LOG.warning(
                'No metric-storage sample for %s on %s interface %s: %s',
                metric_name, hypervisor_ip, interface, query_error)
            return None
        if len(samples) > 1:
            self.fail(
                '%s in metric-storage on %s matched multiple series for '
                'interface %s' % (metric_name, hypervisor_ip, interface))
        return samples[0]['value']

    def _assert_counters_match_ovs(self, hypervisor_ip, interface, ovs_stats,
                                   metric_stdout_cache):
        """Assert openstack metric show, :9105, and metric-storage match OVS."""
        labels = {'interface': interface}
        for stat_key, metric_name in (
                metrics_base.OVS_INTERFACE_STAT_TO_METRIC.items()):
            ovs_value = int(ovs_stats[stat_key])
            if metric_name not in metric_stdout_cache:
                metric_stdout_cache[metric_name], _, _ = self._metric_show(
                    metric_name)
            reported = self._parse_compute_metric_show_value(
                metric_stdout_cache[metric_name], metric_name,
                hypervisor_ip, row_contains=interface)
            prom = self._prom_compute_metric_value(
                hypervisor_ip, metric_name, labels)
            storage = self._storage_counter_value(
                hypervisor_ip, metric_name, interface)
            self.assertIsNotNone(
                reported,
                '%s missing row for %s on %s in openstack metric show' % (
                    metric_name, interface, hypervisor_ip))
            self.assertIsNotNone(
                prom,
                '%s missing on :9105 for %s on %s' % (
                    metric_name, interface, hypervisor_ip))
            self.assertIsNotNone(
                storage,
                '%s missing in metric-storage for %s on %s' % (
                    metric_name, interface, hypervisor_ip))
            self.assertEqual(
                ovs_value, reported,
                '%s openstack metric show=%s OVS=%s for %s on %s' % (
                    metric_name, reported, ovs_value, interface, hypervisor_ip))
            self.assertEqual(
                ovs_value, prom,
                '%s prom=%s OVS=%s for %s on %s' % (
                    metric_name, prom, ovs_value, interface, hypervisor_ip))
            self.assertEqual(
                ovs_value, storage,
                '%s metric-storage=%s OVS=%s for %s on %s' % (
                    metric_name, storage, ovs_value, interface, hypervisor_ip))

    def _wait_for_traffic_counters(
            self, sender, receiver, sender_iface, receiver_iface,
            baseline_sender, baseline_receiver):
        """Wait until OVS and exporter counters reflect the ping run."""
        min_packets = self._min_expected_packets()
        min_bytes = self._min_expected_bytes()
        metric_stdout_cache = {}
        last = {}
        last_exc = None
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            sender_stats = self._ovs_interface_stats(
                sender['hypervisor_ip'], sender_iface)
            receiver_stats = self._ovs_interface_stats(
                receiver['hypervisor_ip'], receiver_iface)
            sender_delta = {
                key: int(sender_stats[key]) - int(baseline_sender[key])
                for key in OVS_INTERFACE_STAT_KEYS}
            receiver_delta = {
                key: int(receiver_stats[key]) - int(baseline_receiver[key])
                for key in OVS_INTERFACE_STAT_KEYS}
            last = {
                'sender_delta': sender_delta,
                'receiver_delta': receiver_delta,
            }
            try:
                self.assertGreaterEqual(
                    sender_delta['tx_packets'], min_packets,
                    'sender tx_packets delta %s' % sender_delta)
                self.assertGreaterEqual(
                    receiver_delta['rx_packets'], min_packets,
                    'receiver rx_packets delta %s' % receiver_delta)
                self.assertGreaterEqual(
                    sender_delta['tx_bytes'], min_bytes,
                    'sender tx_bytes delta %s' % sender_delta)
                self.assertGreaterEqual(
                    receiver_delta['rx_bytes'], min_bytes,
                    'receiver rx_bytes delta %s' % receiver_delta)
                self._assert_counters_match_ovs(
                    sender['hypervisor_ip'], sender_iface, sender_stats,
                    metric_stdout_cache)
                self._assert_counters_match_ovs(
                    receiver['hypervisor_ip'], receiver_iface, receiver_stats,
                    metric_stdout_cache)
                LOG.warning(
                    'Interface counters consistent after traffic '
                    '(attempt %s): %s', attempt + 1, last)
                return sender_delta, receiver_delta
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for traffic counters: %s; last %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    exc, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for OVS vs openstack metric show vs :9105 vs '
            'metric-storage alignment after traffic (packet deltas were %s). '
            'Last alignment error: %s' % (last, last_exc))

    # --- Presence: one Tempest result per ovs_interface counter metric ---

    def test_ovs_interface_rx_packets_reported(self):
        """Verify ovs_interface_rx_packets on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_RX_PACKETS_METRIC)

    def test_ovs_interface_tx_packets_reported(self):
        """Verify ovs_interface_tx_packets on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_TX_PACKETS_METRIC)

    def test_ovs_interface_rx_bytes_reported(self):
        """Verify ovs_interface_rx_bytes on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_RX_BYTES_METRIC)

    def test_ovs_interface_tx_bytes_reported(self):
        """Verify ovs_interface_tx_bytes on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_TX_BYTES_METRIC)

    # --- Traffic: one Tempest result for all four counters ---

    def test_ovs_interface_rx_tx_counters_with_vm_traffic(self):
        """Boot two VMs, send ICMP traffic, verify interface rx/tx metrics."""
        servers, key_pair = self._boot_generic_traffic_vms()
        self.assertEqual(2, len(servers),
                         'Test requires exactly two VMs')

        if not servers[0].get('provider_networks'):
            if self.test_all_provider_networks and servers[0].get('fip'):
                self.verify_provider_networks(servers, key_pair)
            else:
                self._populate_provider_networks(servers)
        try:
            self.get_ovs_port_names(servers)
        except (KeyError, TypeError) as exc:
            LOG.warning(
                'get_ovs_port_names failed (%s); resolving iface-id on OVS',
                exc)

        sender, receiver = servers[0], servers[1]
        peer_ip = self._dataplane_peer_ip(sender, receiver)
        sender_iface = self._resolve_ovs_interface(sender)
        receiver_iface = self._resolve_ovs_interface(receiver)

        LOG.warning(
            'Traffic test: %s (%s:%s) -> %s (%s:%s) peer %s, ping count %s',
            sender.get('name', sender['id']), sender['hypervisor_ip'],
            sender_iface, receiver.get('name', receiver['id']),
            receiver['hypervisor_ip'], receiver_iface, peer_ip,
            self._traffic_ping_count())

        baseline_sender = self._ovs_interface_stats(
            sender['hypervisor_ip'], sender_iface)
        baseline_receiver = self._ovs_interface_stats(
            receiver['hypervisor_ip'], receiver_iface)

        ssh_sender = self.get_remote_client(
            sender['fip'], self.instance_user, key_pair['private_key'])
        self._send_ping_packets(
            ssh_sender, peer_ip, self._traffic_ping_count(),
            self._min_expected_packets())

        sender_delta, receiver_delta = self._wait_for_traffic_counters(
            sender, receiver, sender_iface, receiver_iface,
            baseline_sender, baseline_receiver)

        LOG.warning(
            'Traffic counters OK: sender tx_packets +%s rx_packets +%s; '
            'receiver rx_packets +%s tx_packets +%s',
            sender_delta['tx_packets'], sender_delta['rx_packets'],
            receiver_delta['rx_packets'], receiver_delta['tx_packets'])
