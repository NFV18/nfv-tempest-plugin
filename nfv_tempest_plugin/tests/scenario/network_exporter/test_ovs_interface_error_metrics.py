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

    def _dataplane_provider_net(self, server):
        mgmt_net_id = self._mgmt_network_id()
        for net in server['provider_networks']:
            if mgmt_net_id and net['network_id'] == mgmt_net_id:
                continue
            return net
        for net in server['provider_networks']:
            return net
        self.fail(
            'No dataplane provider network for VM %s' % (
                server.get('name', server['id'])))

    def _lookup_guest_iface_by_mac(self, ssh_client, mac_address):
        mac = mac_address.lower()
        cmd = (
            "ip -o link | grep -i '%s' | awk -F': ' '{print $2; exit}'" % mac)
        raw = ssh_client.exec_command(cmd).strip()
        return raw.split('@')[0].strip() if raw else None

    def _set_guest_iface_link(self, ssh_client, mac_address, state):
        """Bring guest dataplane netdev up or down."""
        sudo = self._guest_sudo_prefix(ssh_client)
        if not sudo:
            LOG.warning(
                'No passwordless sudo on guest; cannot set dataplane link %s',
                state)
            return False
        iface = self._lookup_guest_iface_by_mac(ssh_client, mac_address)
        if not iface:
            LOG.warning(
                'Could not resolve guest iface for MAC %s', mac_address)
            return False
        ssh_client.exec_command(
            '%s ip link set dev %s %s' % (sudo, iface, state))
        return True

    def _restore_guest_iface_link(self, ssh_client, mac_address):
        self._set_guest_iface_link(ssh_client, mac_address, 'up')

    def _maybe_shrink_guest_rx_ring(self, ssh_client, mac_address):
        """Temporarily shrink RX ring on guest dataplane NIC to ease RX drops."""
        sudo = self._guest_sudo_prefix(ssh_client)
        if not sudo:
            LOG.warning(
                'No passwordless sudo on guest; skipping RX ring shrink')
            return None
        iface = self._lookup_guest_iface_by_mac(ssh_client, mac_address)
        if not iface:
            LOG.warning(
                'Could not resolve guest iface for MAC %s; skipping RX shrink',
                mac_address)
            return None
        ssh_client.exec_command(
            '%s ethtool -G %s rx 32 2>/dev/null || '
            '%s ethtool -G %s rx 64 2>/dev/null || true' % (
                sudo, iface, sudo, iface))

        def restore():
            ssh_client.exec_command(
                '%s ethtool -G %s rx 512 2>/dev/null || true' % (
                    sudo, iface))

        self.addCleanup(restore)
        return iface

    def _maybe_shrink_guest_tx_ring(self, ssh_client, mac_address):
        """Temporarily shrink TX ring on guest dataplane NIC to ease TX drops."""
        sudo = self._guest_sudo_prefix(ssh_client)
        if not sudo:
            LOG.warning(
                'No passwordless sudo on guest; skipping TX ring shrink')
            return None
        iface = self._lookup_guest_iface_by_mac(ssh_client, mac_address)
        if not iface:
            LOG.warning(
                'Could not resolve guest iface for MAC %s; skipping TX shrink',
                mac_address)
            return None
        ssh_client.exec_command(
            '%s ethtool -G %s tx 32 2>/dev/null || '
            '%s ethtool -G %s tx 64 2>/dev/null || true' % (
                sudo, iface, sudo, iface))

        def restore():
            ssh_client.exec_command(
                '%s ethtool -G %s tx 512 2>/dev/null || true' % (
                    sudo, iface))

        self.addCleanup(restore)
        return iface

    def _is_vhostuser_interface(self, hypervisor_ip, interface):
        if interface.startswith('vhu'):
            return True
        itype = self._ovs_field(hypervisor_ip, interface, 'type') or ''
        itype = itype.lower()
        return 'vhost' in itype or 'dpdkvhost' in itype

    def _set_ovs_oper_state(self, hypervisor_ip, interface, admin, link):
        """Set OVS admin/link via OVSDB (required for vhost-user VM ports)."""
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s admin_state=%s link_state=%s' % (
                interface, admin, link))

    def _restore_ovs_interface(self, hypervisor_ip, interface):
        if self._is_vhostuser_interface(hypervisor_ip, interface):
            self._set_ovs_oper_state(
                hypervisor_ip, interface, 'up', 'up')
            return
        self._set_ovs_admin_only(hypervisor_ip, interface, 'up')
        self._set_interface_link_state(hypervisor_ip, interface, 'up')

    def _log_error_induce_stats(self, label, baseline, current, stat_key):
        delta = self._error_counter_delta(current, baseline, stat_key)
        rx_pkts = (
            self._ovs_interface_stat_int(current, 'rx_packets') -
            self._ovs_interface_stat_int(baseline, 'rx_packets'))
        tx_pkts = (
            self._ovs_interface_stat_int(current, 'tx_packets') -
            self._ovs_interface_stat_int(baseline, 'tx_packets'))
        LOG.warning(
            '%s: %s_delta=%s rx_packets_delta=%s tx_packets_delta=%s '
            'rx_dropped=%s rx_missed_errors=%s tx_errors=%s '
            'ovs_tx_failure_drops=%s',
            label, stat_key, delta, rx_pkts, tx_pkts,
            self._ovs_interface_stat_int(current, 'rx_dropped'),
            self._ovs_interface_stat_int(current, 'rx_missed_errors'),
            self._ovs_interface_stat_int(current, 'tx_errors'),
            self._ovs_interface_stat_int(current, 'ovs_tx_failure_drops'))
        return delta

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
        ovs_value = self._ovs_interface_error_exporter_value(
            ovs_stats, stat_key)
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
            's.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)\n'
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
        ssh_receiver = self.get_remote_client(
            receiver['fip'], self.instance_user, key_pair['private_key'])
        receiver_net = self._dataplane_provider_net(receiver)
        sender_net = self._dataplane_provider_net(sender)
        return {
            'sender': sender,
            'receiver': receiver,
            'sender_iface': sender_iface,
            'receiver_iface': receiver_iface,
            'peer_ip': peer_ip,
            'bind_ip': bind_ip,
            'ssh_sender': ssh_sender,
            'ssh_receiver': ssh_receiver,
            'receiver_mac': receiver_net['mac_address'],
            'sender_mac': sender_net['mac_address'],
            'key_pair': key_pair,
        }

    def _error_counter_delta(self, current_stats, baseline_stats, stat_key):
        return (
            self._ovs_interface_error_exporter_value(current_stats, stat_key) -
            self._ovs_interface_error_exporter_value(baseline_stats, stat_key))

    def _induce_rx_dropped(self, ctx, flood_count):
        """Drop guest->OVS traffic on the receiver vhost-user port.

        On vhu* ports rx_dropped counts packets received from the guest that
        OVS could not accept. Flood from the receiver guest while its OVS
        port is constrained.
        """
        receiver_hyp = ctx['receiver']['hypervisor_ip']
        receiver_iface = ctx['receiver_iface']
        receiver_bind = self._dataplane_bind_ip(ctx['receiver'])
        baseline = self._ovs_interface_stats(receiver_hyp, receiver_iface)

        self.addCleanup(self._restore_ovs_interface, receiver_hyp, receiver_iface)
        self._maybe_shrink_guest_tx_ring(
            ctx['ssh_receiver'], ctx['receiver_mac'])

        self._set_ovs_oper_state(
            receiver_hyp, receiver_iface, 'up', 'down')
        time.sleep(1)
        LOG.warning(
            'Inducing rx_dropped: receiver guest flood %s -> %s with OVS '
            'link down on %s:%s',
            receiver_bind, ctx['bind_ip'], receiver_hyp, receiver_iface)
        self._flood_udp_dataplane(
            ctx['ssh_receiver'], receiver_bind, ctx['bind_ip'], flood_count)
        after = self._ovs_interface_stats(receiver_hyp, receiver_iface)
        delta = self._log_error_induce_stats(
            'RX induce receiver guest flood + OVS link down', baseline, after,
            'rx_dropped')

        if delta <= 0:
            LOG.warning(
                'Retrying rx_dropped: receiver admin+link down during guest '
                'flood')
            self._set_ovs_oper_state(
                receiver_hyp, receiver_iface, 'down', 'down')
            time.sleep(1)
            self._flood_udp_dataplane(
                ctx['ssh_receiver'], receiver_bind, ctx['bind_ip'], flood_count)
            after = self._ovs_interface_stats(receiver_hyp, receiver_iface)
            self._log_error_induce_stats(
                'RX induce receiver guest flood + OVS admin down', baseline,
                after, 'rx_dropped')

        self._restore_ovs_interface(receiver_hyp, receiver_iface)
        return baseline

    def _induce_tx_errors(self, ctx, flood_count):
        """Drop OVS->guest delivery on the sender vhost-user port.

        ovs_interface_tx_errors maps to ovs_tx_failure_drops on vhu* ports,
        which counts packets OVS could not deliver to the guest. Flood to
        the sender from the receiver while the sender guest cannot receive.
        """
        sender_hyp = ctx['sender']['hypervisor_ip']
        sender_iface = ctx['sender_iface']
        receiver_bind = self._dataplane_bind_ip(ctx['receiver'])
        baseline = self._ovs_interface_stats(sender_hyp, sender_iface)

        self.addCleanup(self._restore_guest_iface_link,
                        ctx['ssh_sender'], ctx['sender_mac'])
        self.addCleanup(self._restore_ovs_interface, sender_hyp, sender_iface)

        guest_down = self._set_guest_iface_link(
            ctx['ssh_sender'], ctx['sender_mac'], 'down')
        time.sleep(1)
        LOG.warning(
            'Inducing tx_errors: receiver flood %s -> %s with sender guest '
            'down (guest_down=%s)',
            receiver_bind, ctx['bind_ip'], guest_down)
        try:
            self._flood_udp_dataplane(
                ctx['ssh_receiver'], receiver_bind, ctx['bind_ip'],
                flood_count)
        except Exception as exc:
            LOG.warning('Receiver flood toward sender finished: %s', exc)
        after = self._ovs_interface_stats(sender_hyp, sender_iface)
        delta = self._log_error_induce_stats(
            'TX induce sender guest down', baseline, after, 'tx_errors')

        if delta <= 0:
            LOG.warning(
                'Retrying tx_errors: sender OVS link down during receiver flood')
            self._restore_guest_iface_link(
                ctx['ssh_sender'], ctx['sender_mac'])
            time.sleep(1)
            self._set_ovs_oper_state(
                sender_hyp, sender_iface, 'up', 'down')
            time.sleep(1)
            try:
                self._flood_udp_dataplane(
                    ctx['ssh_receiver'], receiver_bind, ctx['bind_ip'],
                    flood_count)
            except Exception as exc:
                LOG.warning(
                    'Receiver flood with sender OVS down finished: %s', exc)
            after = self._ovs_interface_stats(sender_hyp, sender_iface)
            self._log_error_induce_stats(
                'TX induce sender OVS link down', baseline, after, 'tx_errors')
            self._restore_ovs_interface(sender_hyp, sender_iface)
        else:
            self._restore_guest_iface_link(
                ctx['ssh_sender'], ctx['sender_mac'])

        return baseline

    def _wait_for_induced_errors(self, ctx, baseline_rx, baseline_tx):
        last_exc = None
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            receiver_stats = self._ovs_interface_stats(
                ctx['receiver']['hypervisor_ip'], ctx['receiver_iface'])
            sender_stats = self._ovs_interface_stats(
                ctx['sender']['hypervisor_ip'], ctx['sender_iface'])
            rx_drop_delta = self._error_counter_delta(
                receiver_stats, baseline_rx, 'rx_dropped')
            tx_err_delta = self._error_counter_delta(
                sender_stats, baseline_tx, 'tx_errors')
            last = {
                'rx_dropped_delta': rx_drop_delta,
                'tx_errors_delta': tx_err_delta,
                'receiver_rx_dropped': self._ovs_interface_stat_int(
                    receiver_stats, 'rx_dropped'),
                'receiver_rx_missed_errors': self._ovs_interface_stat_int(
                    receiver_stats, 'rx_missed_errors'),
                'receiver_ovs_rx_qos_drops': self._ovs_interface_stat_int(
                    receiver_stats, 'ovs_rx_qos_drops'),
                'sender_tx_errors': self._ovs_interface_stat_int(
                    sender_stats, 'tx_errors'),
                'sender_ovs_tx_failure_drops': self._ovs_interface_stat_int(
                    sender_stats, 'ovs_tx_failure_drops'),
                'sender_tx_dropped': self._ovs_interface_stat_int(
                    sender_stats, 'tx_dropped'),
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

        self._send_ping_packets(
            ctx['ssh_sender'], ctx['peer_ip'], self._traffic_ping_count(),
            self._min_expected_packets())

        flood_count = CONF.nfv_plugin_options.network_exporter_error_udp_flood_packets
        baseline_rx = self._induce_rx_dropped(ctx, flood_count)
        baseline_tx = self._induce_tx_errors(ctx, flood_count)

        deltas = self._wait_for_induced_errors(ctx, baseline_rx, baseline_tx)
        LOG.warning(
            'OVS error counters OK: rx_dropped +%s rx_dropped_delta, '
            'tx_errors +%s',
            deltas['rx_dropped_delta'], deltas['tx_errors_delta'])
