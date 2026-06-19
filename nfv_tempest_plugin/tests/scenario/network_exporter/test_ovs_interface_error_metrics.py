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


class TestOvsInterfaceErrorMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_interface error/drop counters with VM traffic and induce."""

    TEST_NAME = 'network_exporter_ovs_interface_errors'

    def _ensure_test_setup(self):
        if self.TEST_NAME not in self.test_setup_dict:
            self.test_setup_dict[self.TEST_NAME] = {
                'flavor-id': self.flavor_ref,
                'router': True,
                'aggregate': None,
            }

    def _filter_error_test_networks(self, test_networks):
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
                'for %s. Add port_type: normal (e.g. tag: external) plus mgmt.'
                % self.TEST_NAME)
        LOG.warning(
            'OVS interface error metrics will create test-networks: %s',
            [net.get('name') for net in filtered])
        return filtered

    def _build_error_boot_kwargs(self):
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

    def _boot_error_test_vms(self):
        self._ensure_test_setup()
        boot_kwargs = self._build_error_boot_kwargs()
        full_test_networks = self.external_config['test-networks']
        self.external_config['test-networks'] = self._filter_error_test_networks(
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

    def _dataplane_bind_ip(self, server):
        mgmt_net_id = self._mgmt_network_id()
        for net in server['provider_networks']:
            if mgmt_net_id and net['network_id'] == mgmt_net_id:
                continue
            return net['ip_address']
        for net in server['provider_networks']:
            return net['ip_address']
        self.fail('No dataplane IP for VM %s' % server.get('name', server['id']))

    def _resolve_ovs_interface(self, server):
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

    def _assert_error_stat_match_ovs(self, hypervisor_ip, interface,
                                     stat_key, ovs_stats):
        metric_name = metrics_base.OVS_INTERFACE_ERROR_STAT_TO_METRIC[stat_key]
        ovs_value = self._ovs_interface_stat_int(ovs_stats, stat_key)
        prom = self._prom_compute_metric_value(
            hypervisor_ip, metric_name, {'interface': interface})
        storage = self._storage_counter_value(
            hypervisor_ip, metric_name, interface)
        self.assertIsNotNone(
            prom,
            '%s missing on :9105 for %s on %s' % (
                metric_name, interface, hypervisor_ip))
        self.assertIsNotNone(
            storage,
            '%s missing in metric-storage for %s on %s' % (
                metric_name, interface, hypervisor_ip))
        self.assertEqual(
            ovs_value, prom,
            '%s :9105=%s OVS=%s for %s on %s' % (
                metric_name, prom, ovs_value, interface, hypervisor_ip))
        self.assertEqual(
            ovs_value, storage,
            '%s metric-storage=%s OVS=%s for %s on %s' % (
                metric_name, storage, ovs_value, interface, hypervisor_ip))

    def _flood_udp_dataplane(self, ssh_client, bind_ip, dest_ip, packet_count):
        script = (
            'import socket\n'
            's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
            's.bind((%r, 0))\n'
            'payload = b"x" * 1400\n'
            'dest = (%r, 9999)\n'
            'for _ in range(%d):\n'
            '    try:\n'
            '        s.sendto(payload, dest)\n'
            '    except OSError:\n'
            '        pass\n'
            % (bind_ip, dest_ip, packet_count))
        self._run_guest_python_script(ssh_client, script, timeout_sec=180)

    def _prepare_traffic_context(self):
        servers, key_pair = self._boot_error_test_vms()
        self.assertEqual(2, len(servers), 'Test requires exactly two VMs')
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
        bind_ip = self._dataplane_bind_ip(sender)
        ssh_sender = self.get_remote_client(
            sender['fip'], self.instance_user, key_pair['private_key'])
        return {
            'sender': sender,
            'receiver': receiver,
            'sender_iface': sender_iface,
            'receiver_iface': receiver_iface,
            'peer_ip': peer_ip,
            'bind_ip': bind_ip,
            'ssh_sender': ssh_sender,
            'key_pair': key_pair,
        }

    def _wait_for_induced_errors(self, ctx, baseline_receiver, baseline_sender):
        last_exc = None
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            receiver_stats = self._ovs_interface_stats(
                ctx['receiver']['hypervisor_ip'], ctx['receiver_iface'])
            sender_stats = self._ovs_interface_stats(
                ctx['sender']['hypervisor_ip'], ctx['sender_iface'])
            rx_drop_delta = (
                self._ovs_interface_stat_int(receiver_stats, 'rx_dropped') -
                self._ovs_interface_stat_int(baseline_receiver, 'rx_dropped'))
            tx_err_delta = (
                self._ovs_interface_stat_int(sender_stats, 'tx_errors') -
                self._ovs_interface_stat_int(baseline_sender, 'tx_errors'))
            last = {
                'rx_dropped_delta': rx_drop_delta,
                'tx_errors_delta': tx_err_delta,
                'receiver_stats_keys': sorted(receiver_stats.keys()),
                'sender_stats_keys': sorted(sender_stats.keys()),
            }
            try:
                self.assertGreater(
                    rx_drop_delta, 0,
                    'receiver rx_dropped did not increase: %s' % last)
                self.assertGreater(
                    tx_err_delta, 0,
                    'sender tx_errors did not increase: %s' % last)
                self._assert_error_stat_match_ovs(
                    ctx['receiver']['hypervisor_ip'], ctx['receiver_iface'],
                    'rx_dropped', receiver_stats)
                self._assert_error_stat_match_ovs(
                    ctx['sender']['hypervisor_ip'], ctx['sender_iface'],
                    'tx_errors', sender_stats)
                LOG.warning(
                    'Error counters consistent after induce (attempt %s): %s',
                    attempt + 1, last)
                return last
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for error counters: %s; last %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    exc, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for induced ovs_interface error counters. '
            'Last %s; last error: %s' % (last, last_exc))

    # --- Presence: one Tempest result per error metric ---

    def test_ovs_interface_rx_errors_reported(self):
        """Verify ovs_interface_rx_errors on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_RX_ERRORS_METRIC)

    def test_ovs_interface_rx_dropped_reported(self):
        """Verify ovs_interface_rx_dropped on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_RX_DROPPED_METRIC)

    def test_ovs_interface_tx_errors_reported(self):
        """Verify ovs_interface_tx_errors on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_TX_ERRORS_METRIC)

    def test_ovs_interface_tx_retries_reported(self):
        """Verify ovs_interface_tx_retries on compute and metric-storage."""
        self._assert_ovs_interface_metric_reported(
            metrics_base.OVS_INTERFACE_TX_RETRIES_METRIC)

    # --- Traffic: induce rx_dropped and tx_errors ---

    def test_ovs_interface_error_counters_with_vm_traffic(self):
        """Boot two VMs, induce rx_dropped and tx_errors, verify alignment."""
        ctx = self._prepare_traffic_context()
        if ':' in ctx['peer_ip']:
            raise unittest.SkipTest(
                'OVS error induce tests currently require IPv4 dataplane peers')

        baseline_receiver = self._ovs_interface_stats(
            ctx['receiver']['hypervisor_ip'], ctx['receiver_iface'])
        baseline_sender = self._ovs_interface_stats(
            ctx['sender']['hypervisor_ip'], ctx['sender_iface'])

        self._send_ping_packets(
            ctx['ssh_sender'], ctx['peer_ip'], self._traffic_ping_count(),
            self._min_expected_packets())

        flood_count = CONF.nfv_plugin_options.network_exporter_error_udp_flood_packets
        LOG.warning(
            'Inducing rx_dropped: %d UDP datagrams %s -> %s',
            flood_count, ctx['bind_ip'], ctx['peer_ip'])
        self._flood_udp_dataplane(
            ctx['ssh_sender'], ctx['bind_ip'], ctx['peer_ip'], flood_count)

        sender_hyp = ctx['sender']['hypervisor_ip']
        sender_iface = ctx['sender_iface']

        def restore_sender_iface():
            self._set_interface_link_state(sender_hyp, sender_iface, 'up')
            self._set_ovs_admin_only(sender_hyp, sender_iface, 'up')

        self.addCleanup(restore_sender_iface)
        LOG.warning(
            'Inducing tx_errors: admin/link down on %s:%s during UDP flood',
            sender_hyp, sender_iface)
        self._set_ovs_admin_only(sender_hyp, sender_iface, 'down')
        self._set_interface_link_state(sender_hyp, sender_iface, 'down')
        try:
            self._flood_udp_dataplane(
                ctx['ssh_sender'], ctx['bind_ip'], ctx['peer_ip'], 5000)
        except Exception as exc:
            LOG.warning('UDP flood while iface down finished: %s', exc)
        restore_sender_iface()

        deltas = self._wait_for_induced_errors(
            ctx, baseline_receiver, baseline_sender)
        LOG.warning(
            'OVS error counters OK: rx_dropped +%s rx_dropped_delta, '
            'tx_errors +%s',
            deltas['rx_dropped_delta'], deltas['tx_errors_delta'])
