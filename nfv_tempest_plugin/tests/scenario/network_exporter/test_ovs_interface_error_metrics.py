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

import base64
import time
import unittest

from tempest import config

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))
ERROR_VETH_IFACE = 'tpst-ovs-er'


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

    def _prom_error_value(self, hypervisor_ip, interface, stat_key):
        metric_name = metrics_base.OVS_INTERFACE_ERROR_STAT_TO_METRIC[stat_key]
        return self._prom_compute_metric_value(
            hypervisor_ip, metric_name, {'interface': interface})

    def _error_progress(self, baseline_stats, current_stats, baseline_prom,
                        current_prom, stat_key):
        return max(
            self._error_counter_delta(current_stats, baseline_stats, stat_key),
            (current_prom or 0) - (baseline_prom or 0))

    def _hypervisor_udp_flood(self, hypervisor_ip, bind_iface, src_ip,
                              dst_ip, packet_count, port=9999):
        """Send a UDP flood from the hypervisor bound to a host netdev."""
        script = (
            'import socket\n'
            'SO_BINDTODEVICE = 25\n'
            's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
            's.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)\n'
            's.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, %r)\n'
            's.bind((%r, 0))\n'
            'payload = b"x" * 1400\n'
            'dest = (%r, %d)\n'
            'for _ in range(%d):\n'
            '    try:\n'
            '        s.sendto(payload, dest)\n'
            '    except OSError:\n'
            '        pass\n'
            % (bind_iface.encode(), src_ip, dst_ip, port, packet_count))
        encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
        cmd = (
            "sudo timeout 180 sh -c 'echo %s | base64 -d | python3'" % encoded)
        self._ssh_run_on_hypervisor(hypervisor_ip, cmd)

    def _hypervisor_ping_flood(self, hypervisor_ip, iface, dest_ip,
                               packet_count):
        """Send a rapid ping burst from a hypervisor netdev."""
        count = min(max(packet_count // 50, 100), 3000)
        cmd = (
            'sudo ping -f -W 1 -c %d -I %s %s 2>/dev/null || true' % (
                count, iface, dest_ip))
        self._ssh_run_on_hypervisor(hypervisor_ip, cmd)

    def _set_ovs_admin_db_only(self, hypervisor_ip, interface, state):
        """Set OVS admin_state only (leave kernel veth legs unchanged)."""
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s admin_state=%s' % (
                interface, state))

    def _ensure_veth_port_up(self, hypervisor_ip, iface):
        """Bring both veth legs and OVS admin up without coupling link toggles."""
        peer = self._veth_peer_name(iface)
        self._set_kernel_link_state(hypervisor_ip, peer, 'up')
        self._set_kernel_link_state(hypervisor_ip, iface, 'up')
        self._set_ovs_admin_db_only(hypervisor_ip, iface, 'up')
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s link_state=up' % iface)

    def _install_tc_ingress_drop(self, hypervisor_ip, iface):
        """Drop all ingress on the OVS netdev (increments kernel rx_dropped)."""
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo tc qdisc add dev %s handle ffff: ingress 2>/dev/null || true; '
            'sudo tc filter add dev %s parent ffff: protocol all u32 '
            'match u32 0 0 action drop 2>/dev/null || true' % (iface, iface))
        self.addCleanup(self._remove_tc_ingress, hypervisor_ip, iface)

    def _remove_tc_ingress(self, hypervisor_ip, iface):
        self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo tc qdisc del dev %s ingress 2>/dev/null || true' % iface)

    def _try_install_tc_netem(self, hypervisor_ip, iface, netem_opts):
        """Install a netem root qdisc; return False if sch_netem is unavailable."""
        self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo modprobe sch_netem 2>/dev/null || true')
        out = self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo tc qdisc replace dev %s root netem %s 2>&1; echo __rc__:$?' % (
                iface, netem_opts))
        if '__rc__:0' not in out or (
                out and ('unknown' in out.lower() or 'error:' in out.lower())):
            LOG.warning(
                'tc netem unavailable on %s for %s: %s',
                hypervisor_ip, iface, out.strip())
            return False
        self.addCleanup(self._remove_tc_root, hypervisor_ip, iface)
        return True

    def _restore_iface_mtu(self, hypervisor_ip, iface, mtu):
        self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo ip link set dev %s mtu %d 2>/dev/null || true' % (
                iface, mtu))

    def _induce_tx_errors_via_small_mtu(self, hypervisor_ip, iface, peer_ip,
                                        iface_ip, flood_count, baseline):
        """Oversize frames on a tiny MTU may increment netdev tx_errors."""
        mtu_out = self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'cat /sys/class/net/%s/mtu 2>/dev/null' % iface).strip()
        try:
            old_mtu = int(mtu_out)
        except (TypeError, ValueError):
            old_mtu = 1500
        self._ssh_run_on_hypervisor(
            hypervisor_ip, 'sudo ip link set dev %s mtu 256' % iface)
        self.addCleanup(self._restore_iface_mtu, hypervisor_ip, iface, old_mtu)
        time.sleep(1)
        LOG.warning(
            'Veth tx_errors induce: mtu 256 on %s, transmit to %s',
            iface, peer_ip)
        return self._induce_tx_errors_on_iface(
            hypervisor_ip, iface, peer_ip, iface_ip, flood_count, baseline,
            'Veth TX small MTU')

    def _remove_tc_root(self, hypervisor_ip, iface):
        self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo tc qdisc del dev %s root 2>/dev/null || true' % iface)

    def _induce_rx_dropped_on_iface(self, hypervisor_ip, iface, peer,
                                    peer_ip, iface_ip, flood_count, baseline,
                                    label):
        """Flood the OVS leg from the host peer; return exporter-mapped delta."""
        self._hypervisor_ping_flood(
            hypervisor_ip, peer, iface_ip, flood_count)
        self._hypervisor_udp_flood(
            hypervisor_ip, peer, peer_ip, iface_ip, flood_count)
        after = self._ovs_interface_stats(hypervisor_ip, iface)
        return self._log_error_induce_stats(label, baseline, after, 'rx_dropped')

    def _induce_veth_rx_dropped(self, hypervisor_ip, iface, peer, peer_ip,
                                iface_ip, flood_count):
        """Drop inbound traffic on the OVS port while the host peer stays up."""
        self._ensure_veth_port_up(hypervisor_ip, iface)
        baseline = self._ovs_interface_stats(hypervisor_ip, iface)

        self._install_tc_ingress_drop(hypervisor_ip, iface)
        time.sleep(1)
        LOG.warning(
            'Veth rx_dropped induce: tc ingress drop on %s, flood from %s',
            iface, peer)
        delta = self._induce_rx_dropped_on_iface(
            hypervisor_ip, iface, peer, peer_ip, iface_ip, flood_count,
            baseline, 'Veth RX tc ingress drop')
        self._remove_tc_ingress(hypervisor_ip, iface)

        if delta <= 0:
            LOG.warning(
                'Retrying rx_dropped via OVS admin down on %s', iface)
            self._set_ovs_admin_db_only(hypervisor_ip, iface, 'down')
            time.sleep(1)
            delta = self._induce_rx_dropped_on_iface(
                hypervisor_ip, iface, peer, peer_ip, iface_ip, flood_count,
                baseline, 'Veth RX OVS admin down')
            self._set_ovs_admin_db_only(hypervisor_ip, iface, 'up')

        if delta <= 0:
            LOG.warning(
                'Retrying rx_dropped via kernel down on OVS leg %s', iface)
            self._set_kernel_link_state(hypervisor_ip, iface, 'down')
            time.sleep(1)
            delta = self._induce_rx_dropped_on_iface(
                hypervisor_ip, iface, peer, peer_ip, iface_ip, flood_count,
                baseline, 'Veth RX kernel down')
            self._set_kernel_link_state(hypervisor_ip, iface, 'up')

        self._ensure_veth_port_up(hypervisor_ip, iface)
        if delta <= 0:
            LOG.warning(
                'rx_dropped induce finished with delta=0 on %s (baseline=%s)',
                iface, baseline)

    def _induce_tx_errors_on_iface(self, hypervisor_ip, iface, peer_ip,
                                   iface_ip, flood_count, baseline, label):
        """Transmit from the OVS leg toward the host peer address."""
        self._hypervisor_ping_flood(
            hypervisor_ip, iface, peer_ip, flood_count)
        self._hypervisor_udp_flood(
            hypervisor_ip, iface, iface_ip, peer_ip, flood_count)
        after = self._ovs_interface_stats(hypervisor_ip, iface)
        return self._log_error_induce_stats(label, baseline, after, 'tx_errors')

    def _induce_veth_tx_errors(self, hypervisor_ip, iface, peer, iface_ip,
                               peer_ip, flood_count):
        """Force TX failures on the OVS leg (peer address is on the host peer)."""
        self._ensure_veth_port_up(hypervisor_ip, iface)
        baseline = self._ovs_interface_stats(hypervisor_ip, iface)

        self._set_ovs_admin_db_only(hypervisor_ip, iface, 'down')
        time.sleep(1)
        LOG.warning(
            'Veth tx_errors induce: OVS admin down on %s, transmit to %s',
            iface, peer_ip)
        delta = self._induce_tx_errors_on_iface(
            hypervisor_ip, iface, peer_ip, iface_ip, flood_count, baseline,
            'Veth TX OVS admin down')
        self._set_ovs_admin_db_only(hypervisor_ip, iface, 'up')

        if delta <= 0:
            LOG.warning(
                'Retrying tx_errors via peer %s down, transmit from %s',
                peer, iface)
            self._ensure_veth_port_up(hypervisor_ip, iface)
            self._set_kernel_link_state(hypervisor_ip, peer, 'down')
            time.sleep(1)
            delta = self._induce_tx_errors_on_iface(
                hypervisor_ip, iface, peer_ip, iface_ip, flood_count, baseline,
                'Veth TX peer down')
            self._set_kernel_link_state(hypervisor_ip, peer, 'up')

        if delta <= 0:
            LOG.warning(
                'Retrying tx_errors via OVS link_state down on %s', iface)
            self._ensure_veth_port_up(hypervisor_ip, iface)
            self._ssh_run_on_hypervisor(
                hypervisor_ip,
                'sudo ovs-vsctl set Interface %s link_state=down' % iface)
            time.sleep(1)
            delta = self._induce_tx_errors_on_iface(
                hypervisor_ip, iface, peer_ip, iface_ip, flood_count, baseline,
                'Veth TX OVS link down')
            self._ssh_run_on_hypervisor(
                hypervisor_ip,
                'sudo ovs-vsctl set Interface %s link_state=up' % iface)

        if delta <= 0:
            LOG.warning(
                'Retrying tx_errors via tc netem corrupt on %s', iface)
            self._ensure_veth_port_up(hypervisor_ip, iface)
            if self._try_install_tc_netem(
                    hypervisor_ip, iface, 'corrupt 100%'):
                time.sleep(1)
                delta = self._induce_tx_errors_on_iface(
                    hypervisor_ip, iface, peer_ip, iface_ip, flood_count,
                    baseline, 'Veth TX tc corrupt')
                self._remove_tc_root(hypervisor_ip, iface)

        if delta <= 0:
            delta = self._induce_tx_errors_via_small_mtu(
                hypervisor_ip, iface, peer_ip, iface_ip, flood_count, baseline)

        self._ensure_veth_port_up(hypervisor_ip, iface)
        if delta <= 0:
            LOG.warning(
                'tx_errors induce finished with delta=0 on %s (baseline=%s)',
                iface, baseline)

    def _attach_error_test_veth(self, hypervisor_ip, preferred_bridge, iface):
        """Attach disposable veth to the VM bridge or a fallback kernel bridge."""
        self._cleanup_test_interface(hypervisor_ip, iface)
        if preferred_bridge:
            try:
                self._create_test_interface_on_bridge(
                    hypervisor_ip, preferred_bridge, iface)
                return preferred_bridge
            except Exception as exc:
                LOG.warning(
                    'Could not attach %s to bridge %s on %s (%s); trying '
                    'fallback bridges',
                    iface, preferred_bridge, hypervisor_ip, exc)
        return self._create_test_interface(hypervisor_ip, iface)

    def _run_veth_error_induces(self, hypervisor_ip, bridge, flood_count):
        """Induce rx_dropped/tx_errors on a disposable kernel veth port."""
        iface = ERROR_VETH_IFACE
        self._assert_valid_ifnames(iface)
        bridge = self._attach_error_test_veth(
            hypervisor_ip, bridge, iface)
        self.addCleanup(
            self._delete_test_interface, hypervisor_ip, bridge, iface)
        peer = self._veth_peer_name(iface)
        peer_ip = '198.18.99.1'
        iface_ip = '198.18.99.2'
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ip addr add %s/32 dev %s 2>/dev/null || true' % (
                peer_ip, peer))
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ip addr add %s/32 dev %s 2>/dev/null || true' % (
                iface_ip, iface))

        def restore_addrs():
            self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip,
                'sudo ip addr del %s/32 dev %s 2>/dev/null || true; '
                'sudo ip addr del %s/32 dev %s 2>/dev/null || true' % (
                    peer_ip, peer, iface_ip, iface))

        self.addCleanup(restore_addrs)
        self._ensure_veth_port_up(hypervisor_ip, iface)

        baseline_rx = self._ovs_interface_stats(hypervisor_ip, iface)
        baseline_rx_prom = self._prom_error_value(
            hypervisor_ip, iface, 'rx_dropped')
        self._induce_veth_rx_dropped(
            hypervisor_ip, iface, peer, peer_ip, iface_ip, flood_count)

        baseline_tx = self._ovs_interface_stats(hypervisor_ip, iface)
        baseline_tx_prom = self._prom_error_value(
            hypervisor_ip, iface, 'tx_errors')
        self._induce_veth_tx_errors(
            hypervisor_ip, iface, peer, iface_ip, peer_ip, flood_count)

        after = self._ovs_interface_stats(hypervisor_ip, iface)
        self._log_error_induce_stats(
            'Veth final rx', baseline_rx, after, 'rx_dropped')
        self._log_error_induce_stats(
            'Veth final tx', baseline_tx, after, 'tx_errors')

        veth_ctx = {
            'receiver': {'hypervisor_ip': hypervisor_ip},
            'sender': {'hypervisor_ip': hypervisor_ip},
            'receiver_iface': iface,
            'sender_iface': iface,
        }
        return (veth_ctx, baseline_rx, baseline_tx,
                baseline_rx_prom, baseline_tx_prom)

    def _wait_for_induced_errors(self, ctx, baseline_rx, baseline_tx,
                                 baseline_rx_prom=None, baseline_tx_prom=None):
        last_exc = None
        last = {}
        receiver_hyp = ctx['receiver']['hypervisor_ip']
        sender_hyp = ctx['sender']['hypervisor_ip']
        receiver_iface = ctx['receiver_iface']
        sender_iface = ctx['sender_iface']
        if baseline_rx_prom is None:
            baseline_rx_prom = self._prom_error_value(
                receiver_hyp, receiver_iface, 'rx_dropped')
        if baseline_tx_prom is None:
            baseline_tx_prom = self._prom_error_value(
                sender_hyp, sender_iface, 'tx_errors')
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            receiver_stats = self._ovs_interface_stats(
                receiver_hyp, receiver_iface)
            sender_stats = self._ovs_interface_stats(
                sender_hyp, sender_iface)
            rx_prom = self._prom_error_value(
                receiver_hyp, receiver_iface, 'rx_dropped')
            tx_prom = self._prom_error_value(
                sender_hyp, sender_iface, 'tx_errors')
            rx_drop_delta = self._error_progress(
                baseline_rx, receiver_stats, baseline_rx_prom, rx_prom,
                'rx_dropped')
            tx_err_delta = self._error_progress(
                baseline_tx, sender_stats, baseline_tx_prom, tx_prom,
                'tx_errors')
            last = {
                'rx_dropped_delta': rx_drop_delta,
                'tx_errors_delta': tx_err_delta,
                'receiver_rx_dropped': self._ovs_interface_stat_int(
                    receiver_stats, 'rx_dropped'),
                'receiver_rx_missed_errors': self._ovs_interface_stat_int(
                    receiver_stats, 'rx_missed_errors'),
                'receiver_ovs_rx_qos_drops': self._ovs_interface_stat_int(
                    receiver_stats, 'ovs_rx_qos_drops'),
                'receiver_prom_rx_dropped': rx_prom,
                'sender_tx_errors': self._ovs_interface_stat_int(
                    sender_stats, 'tx_errors'),
                'sender_ovs_tx_failure_drops': self._ovs_interface_stat_int(
                    sender_stats, 'ovs_tx_failure_drops'),
                'sender_tx_dropped': self._ovs_interface_stat_int(
                    sender_stats, 'tx_dropped'),
                'sender_prom_tx_errors': tx_prom,
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
            'Last %s; last error: %s; diagnostics: %s' % (
                last, last_exc,
                self._ovs_interface_diagnostic(
                    receiver_hyp,
                    self._port_bridge(receiver_hyp, receiver_iface),
                    receiver_iface)))

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
        """Boot two VMs, verify dataplane traffic, induce error counters.

        vhost-user VM ports do not reliably expose incrementing rx_dropped or
        tx_errors counters under guest traffic. After VM ping sanity, error
        counter growth is validated on a disposable kernel veth attached to
        the same compute bridge.
        """
        ctx = self._prepare_traffic_context()
        if ':' in ctx['peer_ip']:
            raise unittest.SkipTest(
                'OVS error induce tests currently require IPv4 dataplane peers')

        self._send_ping_packets(
            ctx['ssh_sender'], ctx['peer_ip'], self._traffic_ping_count(),
            self._min_expected_packets())

        flood_count = CONF.nfv_plugin_options.network_exporter_error_udp_flood_packets
        hypervisor_ip = ctx['sender']['hypervisor_ip']
        bridge = self._port_bridge(hypervisor_ip, ctx['sender_iface'])
        LOG.warning(
            'Inducing ovs_interface error counters on disposable veth %s '
            '(VM bridge %s on %s) after dataplane ping',
            ERROR_VETH_IFACE, bridge, hypervisor_ip)
        (veth_ctx, baseline_rx, baseline_tx,
         baseline_rx_prom, baseline_tx_prom) = self._run_veth_error_induces(
            hypervisor_ip, bridge, flood_count)

        deltas = self._wait_for_induced_errors(
            veth_ctx, baseline_rx, baseline_tx,
            baseline_rx_prom, baseline_tx_prom)
        LOG.warning(
            'OVS error counters OK: rx_dropped +%s rx_dropped_delta, '
            'tx_errors +%s',
            deltas['rx_dropped_delta'], deltas['tx_errors_delta'])
