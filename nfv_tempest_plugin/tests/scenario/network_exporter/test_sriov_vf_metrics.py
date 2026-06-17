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
from nfv_tempest_plugin.tests.scenario.network_exporter import (
    net_vf_metrics_mixin)
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger("{} [-] nfv_plugin_test".format(__name__))


class TestSriovVfMetrics(net_vf_metrics_mixin.NetVfMetricsMixin,
                         metrics_base.NetworkExporterMetricsBase):
    """Validate SR-IOV VF metrics presence, labels, and traffic movement."""

    TEST_NAME = 'network_exporter_sriov_vf_metrics'

    def _sriov_ports_filter(self):
        """Match TestSriovScenarios: attach external (SSH/FIP) + direct SR-IOV."""
        physnet = CONF.nfv_plugin_options.network_exporter_sriov_physnet.strip()
        return "external,direct:%s" % physnet if physnet else "external,direct"

    def _ensure_test_setup(self):
        """Default test config when tests-setup omits this test name."""
        if self.TEST_NAME not in self.test_setup_dict:
            self.test_setup_dict[self.TEST_NAME] = {
                'flavor-id': self.flavor_ref,
                'router': True,
                'aggregate': None,
            }

    def _filter_sriov_test_networks(self, test_networks):
        """Create mgmt + external + direct ports (same pattern as SR-IOV use cases).

        Keeps provider/external networks for SSH and floating IP access via
        ports_filter external,direct[:physnet]. Skips unrelated networks (e.g.
        DPDK-only) to avoid unnecessary port creation.
        """
        physnet = CONF.nfv_plugin_options.network_exporter_sriov_physnet.strip()
        filtered = []
        for network in test_networks:
            if network.get('mgmt'):
                filtered.append(network)
                continue
            if network.get('tag') == 'external':
                filtered.append(network)
                continue
            if network.get('port_type') != 'direct':
                continue
            if physnet and network.get('physical_network') != physnet:
                continue
            filtered.append(network)
        if not any(net.get('port_type') == 'direct' for net in filtered):
            raise unittest.SkipTest(
                'No direct SR-IOV test-network in tempest_config.yml for '
                '%s. Add a test-network with port_type: direct and '
                'physical_network matching Neutron (set '
                'network_exporter_sriov_physnet to select one physnet).' %
                self.TEST_NAME)
        LOG.warning(
            'SR-IOV VF metrics will create test-networks: %s',
            [net.get('name') for net in filtered])
        return filtered

    def _build_sriov_boot_kwargs(self):
        """Resource kwargs aligned with test_nfv_sriov_usecases boot pattern."""
        ports_filter = self._sriov_ports_filter()
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

    def _collect_vm_ports(self, servers):
        by_server = {}
        for server in servers:
            ports = self.os_admin.ports_client.list_ports(
                device_id=server["id"])["ports"]
            by_server[server["id"]] = ports
        return by_server

    def _sriov_port_by_server(self, servers):
        ports_by_server = self._collect_vm_ports(servers)
        server_ports = {}
        for server in servers:
            direct_ports = [
                port for port in ports_by_server[server["id"]]
                if (port.get("binding:vnic_type") == "direct" and
                    port.get("fixed_ips"))
            ]
            if direct_ports:
                server_ports[server["id"]] = direct_ports
        return server_ports

    def _select_peer_ports(self, servers):
        ports_by_server = self._sriov_port_by_server(servers)
        if len(ports_by_server) < 2:
            raise unittest.SkipTest(
                "SR-IOV VF metrics test needs at least two VMs with direct "
                "ports attached. Set network_exporter_sriov_physnet or "
                "adjust test resources.")
        for sender in servers:
            sender_ports = ports_by_server.get(sender["id"], [])
            for receiver in servers:
                if receiver["id"] == sender["id"]:
                    continue
                receiver_ports = ports_by_server.get(receiver["id"], [])
                for sender_port in sender_ports:
                    for receiver_port in receiver_ports:
                        if (sender_port["network_id"] ==
                                receiver_port["network_id"]):
                            return (sender, sender_port, receiver,
                                    receiver_port)
        raise unittest.SkipTest(
            "No shared SR-IOV direct network between test VMs. Check that "
            "test resources provide at least one common direct network.")

    def _ping_count(self):
        return CONF.nfv_plugin_options.network_exporter_sriov_traffic_ping_count

    def _min_expected_packets_for_count(self, count):
        tolerance = (
            CONF.nfv_plugin_options.network_exporter_sriov_counter_tolerance_pct)
        return int(count * (100 - tolerance) / 100)

    def _min_expected_packets(self):
        return self._min_expected_packets_for_count(self._ping_count())

    def _min_expected_bytes(self):
        return (self._min_expected_packets() *
                CONF.nfv_plugin_options.network_exporter_traffic_min_bytes_per_packet)

    def _min_expected_bytes_for_count(self, packet_count):
        return (self._min_expected_packets_for_count(packet_count) *
                CONF.nfv_plugin_options.network_exporter_traffic_min_bytes_per_packet)

    def _boot_sriov_vms(self):
        """Create networks, ports, flavor (if configured), and boot two VMs."""
        self._ensure_test_setup()
        boot_kwargs = self._build_sriov_boot_kwargs()
        LOG.warning(
            'Booting SR-IOV VMs for %s with ports_filter=%s',
            self.TEST_NAME, boot_kwargs['srv_details'][0]['ports_filter'])
        full_test_networks = self.external_config['test-networks']
        self.external_config['test-networks'] = self._filter_sriov_test_networks(
            full_test_networks)
        try:
            return self.create_and_verify_resources(
                test=self.TEST_NAME, **boot_kwargs)
        finally:
            self.external_config['test-networks'] = full_test_networks

    def _subnet_broadcast_ip(self, ip_address):
        """Return IPv4 subnet broadcast address for ip_address, or None."""
        if ':' in ip_address:
            return None
        octets = ip_address.split('.')
        if len(octets) != 4:
            return None
        return '.'.join(octets[:3] + ['255'])

    def _build_traffic_context(self):
        """Boot SR-IOV peer VMs and return sender/receiver dataplane context."""
        servers, key_pair = self._boot_sriov_vms()
        sender, sender_port, receiver, receiver_port = self._select_peer_ports(
            servers)
        sender_ctx = {
            "server": sender,
            "port": sender_port,
            "hypervisor_ip": sender["hypervisor_ip"],
            "vf_labels": self._vf_labels_from_mac(
                sender["hypervisor_ip"], sender_port["mac_address"]),
        }
        receiver_ctx = {
            "server": receiver,
            "port": receiver_port,
            "hypervisor_ip": receiver["hypervisor_ip"],
            "vf_labels": self._vf_labels_from_mac(
                receiver["hypervisor_ip"], receiver_port["mac_address"]),
        }
        peer_ip = receiver_port["fixed_ips"][0]["ip_address"]
        ip_for_access = sender.get('fip') or sender.get('fixed_ip')
        ssh_sender = self.get_remote_client(
            ip_for_access, self.instance_user, key_pair["private_key"])
        receiver_access_ip = receiver.get('fip') or receiver.get('fixed_ip')
        ssh_receiver = self.get_remote_client(
            receiver_access_ip, self.instance_user, key_pair["private_key"])
        return {
            "sender": sender_ctx,
            "receiver": receiver_ctx,
            "ssh_sender": ssh_sender,
            "ssh_receiver": ssh_receiver,
            "peer_ip": peer_ip,
            "broadcast_ip": self._subnet_broadcast_ip(peer_ip),
        }

    def _send_multicast_packets(self, ctx, count, min_packets):
        """Send IPv4 UDP multicast on the SR-IOV L2 segment (no listener)."""
        bind_ip = ctx['sender']['port']['fixed_ips'][0]['ip_address']
        if ':' in bind_ip:
            raise unittest.SkipTest(
                'Multicast VF metric test needs an IPv4 SR-IOV sender address')
        group = '224.0.0.1'
        flood_count = (
            CONF.nfv_plugin_options.network_exporter_sriov_multicast_flood_packets)
        LOG.warning(
            'Sending multicast UDP: %d datagrams to %s:9997 from %s',
            flood_count, group, bind_ip)
        script = (
            'import socket\n'
            's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
            's.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)\n'
            's.bind((%r, 0))\n'
            'dest = (%r, 9997)\n'
            'payload = b"x" * 64\n'
            'for _ in range(%d):\n'
            '    s.sendto(payload, dest)\n'
            % (bind_ip, group, flood_count))
        self._run_guest_python_script(ctx['ssh_sender'], script, timeout_sec=120)

    def _send_broadcast_packets(self, ctx, count, min_packets):
        """Send IPv4 UDP broadcast on the SR-IOV L2 segment (no reply needed)."""
        broadcast_ip = ctx.get('broadcast_ip')
        if not broadcast_ip:
            raise unittest.SkipTest(
                'Broadcast VF metric test needs an IPv4 SR-IOV peer address')
        flood_count = (
            CONF.nfv_plugin_options.network_exporter_sriov_broadcast_flood_packets)
        bind_ip = ctx['sender']['port']['fixed_ips'][0]['ip_address']
        LOG.warning(
            'Sending broadcast UDP: %d datagrams to %s:9998 from %s',
            flood_count, broadcast_ip, bind_ip)
        self._flood_udp_dataplane(
            ctx['ssh_sender'], bind_ip, broadcast_ip, flood_count,
            broadcast=True)

    def _guest_sudo_prefix(self, ssh_client):
        """Return sudo prefix when passwordless sudo is available on the guest."""
        if self._guest_has_passwordless_sudo(ssh_client):
            return 'sudo -n'
        return ''

    def _set_hypervisor_vf_link_state(self, hypervisor_ip, vf_labels, enabled):
        """Enable or disable SR-IOV VF link state from the compute hypervisor."""
        state = 'enable' if enabled else 'disable'
        cmd = 'sudo ip link set dev %s vf %s state %s' % (
            vf_labels['device'], vf_labels['vf'], state)
        self._ssh_run_on_hypervisor(hypervisor_ip, cmd)

    def _restore_hypervisor_vf_link(self, hypervisor_ip, vf_labels):
        """Best-effort re-enable of VF link state after transmit drop test."""
        self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip,
            'sudo ip link set dev %s vf %s state enable' % (
                vf_labels['device'], vf_labels['vf']))

    def _induce_transmit_drops(self, ctx, count, min_packets):
        """Disable host VF link and flood TX from guest (iface stays up)."""
        sender = ctx['sender']
        hypervisor_ip = sender['hypervisor_ip']
        vf_labels = sender['vf_labels']
        mac_address = sender['port']['mac_address']
        bind_ip = sender['port']['fixed_ips'][0]['ip_address']
        peer_ip = ctx['peer_ip']
        ssh_sender = ctx['ssh_sender']
        flood_count = (
            CONF.nfv_plugin_options.network_exporter_sriov_tx_drop_flood_packets)

        self.addCleanup(
            self._restore_hypervisor_vf_link, hypervisor_ip, vf_labels)
        self._maybe_shrink_guest_tx_ring(ssh_sender, mac_address)
        self._set_hypervisor_vf_link_state(
            hypervisor_ip, vf_labels, False)
        time.sleep(2)

        sysfs_before = self._host_vf_sysfs_stat(
            hypervisor_ip, vf_labels, 'tx_dropped')
        LOG.warning(
            'Inducing transmit drops on %s VF %s: host link disabled, '
            'guest iface up, %d UDP datagrams %s -> %s (sysfs tx_dropped=%s)',
            hypervisor_ip, vf_labels, flood_count, bind_ip, peer_ip,
            sysfs_before)
        self._flood_udp_dataplane(
            ssh_sender, bind_ip, peer_ip, flood_count)
        self._restore_hypervisor_vf_link(hypervisor_ip, vf_labels)
        sysfs_after = self._host_vf_sysfs_stat(
            hypervisor_ip, vf_labels, 'tx_dropped')
        LOG.warning(
            'Transmit drop induce finished on %s VF %s: sysfs tx_dropped '
            'before=%s after=%s',
            hypervisor_ip, vf_labels, sysfs_before, sysfs_after)
        ctx['sysfs_drop_before'] = sysfs_before
        ctx['sysfs_drop_after'] = sysfs_after

    def _run_guest_python_script(self, ssh_client, script, timeout_sec=60):
        """Run a Python script on the guest via base64 pipe (avoids quoting)."""
        encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
        cmd = 'timeout %d sh -c \'echo %s | base64 -d | python3\'' % (
            timeout_sec, encoded)
        sudo = self._guest_sudo_prefix(ssh_client)
        if sudo:
            cmd = '%s %s' % (sudo, cmd)
        return ssh_client.exec_command(cmd)

    def _maybe_shrink_guest_rx_ring(self, ssh_client, mac_address):
        """Temporarily shrink RX ring on guest SR-IOV NIC to ease RX drops."""
        sudo = self._guest_sudo_prefix(ssh_client)
        if not sudo:
            return None
        iface = self._guest_dataplane_iface(ssh_client, mac_address)
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
        """Temporarily shrink TX ring on guest SR-IOV NIC to ease TX drops."""
        sudo = self._guest_sudo_prefix(ssh_client)
        if not sudo:
            return None
        iface = self._guest_dataplane_iface(ssh_client, mac_address)
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

    def _flood_udp_dataplane(self, ssh_client, bind_ip, dest_ip, packet_count,
                             broadcast=False):
        """Send a UDP flood bound to the SR-IOV guest IP."""
        bcast = 'True' if broadcast else 'False'
        script = (
            'import socket\n'
            's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
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
        self._run_guest_python_script(ssh_client, script, timeout_sec=120)

    def _induce_receive_drops(self, ctx, count, min_packets):
        """Shrink RX ring and flood peer IP to overflow the receiver VF."""
        if ':' in ctx['peer_ip']:
            raise unittest.SkipTest(
                'RX drop flood test currently supports IPv4 SR-IOV peers only')
        receiver = ctx['receiver']
        hypervisor_ip = receiver['hypervisor_ip']
        vf_labels = receiver['vf_labels']
        bind_ip = ctx['sender']['port']['fixed_ips'][0]['ip_address']
        flood_count = (
            CONF.nfv_plugin_options.network_exporter_sriov_rx_drop_flood_packets)
        self._maybe_shrink_guest_rx_ring(
            ctx['ssh_receiver'], receiver['port']['mac_address'])

        sysfs_before = self._host_vf_sysfs_stat(
            hypervisor_ip, vf_labels, 'rx_dropped')
        LOG.warning(
            'Inducing receive drops on %s VF %s: %d UDP datagrams %s -> %s '
            'with no listener (sysfs rx_dropped=%s)',
            hypervisor_ip, vf_labels, flood_count, bind_ip, ctx['peer_ip'],
            sysfs_before)
        self._flood_udp_dataplane(
            ctx['ssh_sender'], bind_ip, ctx['peer_ip'], flood_count)
        sysfs_after = self._host_vf_sysfs_stat(
            hypervisor_ip, vf_labels, 'rx_dropped')
        LOG.warning(
            'Receive drop induce finished on %s VF %s: sysfs rx_dropped '
            'before=%s after=%s',
            hypervisor_ip, vf_labels, sysfs_before, sysfs_after)
        ctx['sysfs_drop_before'] = sysfs_before
        ctx['sysfs_drop_after'] = sysfs_after

    # --- Presence: one Tempest result per net_vf metric family ---

    def test_net_vf_info_reported(self):
        """Verify net_vf_info on compute :9105 and metric-storage Prometheus."""
        self._assert_net_vf_metric_reported(metrics_base.NET_VF_INFO_METRIC)

    def test_net_vf_receive_packets_total_reported(self):
        """Verify net_vf_receive_packets_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_RECEIVE_PACKETS_METRIC)

    def test_net_vf_transmit_packets_total_reported(self):
        """Verify net_vf_transmit_packets_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_TRANSMIT_PACKETS_METRIC)

    def test_net_vf_receive_bytes_total_reported(self):
        """Verify net_vf_receive_bytes_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_RECEIVE_BYTES_METRIC)

    def test_net_vf_transmit_bytes_total_reported(self):
        """Verify net_vf_transmit_bytes_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_TRANSMIT_BYTES_METRIC)

    def test_net_vf_broadcast_packets_total_reported(self):
        """Verify net_vf_broadcast_packets_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_BROADCAST_PACKETS_METRIC)

    def test_net_vf_multicast_packets_total_reported(self):
        """Verify net_vf_multicast_packets_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_MULTICAST_PACKETS_METRIC)

    # --- Label integrity (net_vf_info only) ---

    def test_net_vf_info_labels_match_host(self):
        """Verify net_vf_info labels match host VF mapping for VM direct ports."""
        self._assert_net_vf_metric_reported(metrics_base.NET_VF_INFO_METRIC)
        servers, _ = self._boot_sriov_vms()
        sender, sender_port, receiver, receiver_port = self._select_peer_ports(
            servers)
        for server, port in ((sender, sender_port), (receiver, receiver_port)):
            hypervisor_ip = server["hypervisor_ip"]
            expected_labels = self._vf_labels_from_mac(
                hypervisor_ip, port["mac_address"])
            samples = self._prom_samples(
                hypervisor_ip, metrics_base.NET_VF_INFO_METRIC,
                required_labels={
                    "device": expected_labels["device"],
                    "vf": expected_labels["vf"],
                    "pci_address": expected_labels["pci_address"],
                })
            self.assertNotEmpty(
                samples,
                "No net_vf_info sample found on %s for labels %s "
                "(MAC=%s, server=%s)" % (
                    hypervisor_ip, expected_labels,
                    port["mac_address"], server["id"]))
            exported_numa_nodes = {
                sample["labels"].get("numa_node", "") for sample in samples}
            self.assertIn(
                expected_labels["numa_node"], exported_numa_nodes,
                "numa_node mismatch for host VF labels %s on %s. "
                "Exporter returned numa_node values %s" % (
                    expected_labels, hypervisor_ip,
                    sorted(exported_numa_nodes)))
            self._assert_net_vf_metrics_match_metric_storage(
                hypervisor_ip, metrics_base.NET_VF_INFO_METRIC, {
                    'device': expected_labels['device'],
                    'vf': expected_labels['vf'],
                    'pci_address': expected_labels['pci_address'],
                })

    def test_net_vf_guest_dataplane_mac_matches_port(self):
        """Verify each VM SR-IOV dataplane NIC MAC matches its Neutron port."""
        servers, key_pair = self._boot_sriov_vms()
        sender, sender_port, receiver, receiver_port = self._select_peer_ports(
            servers)
        for server, port in ((sender, sender_port), (receiver, receiver_port)):
            access_ip = server.get('fip') or server.get('fixed_ip')
            ssh_client = self.get_remote_client(
                access_ip, self.instance_user, key_pair['private_key'])
            self._assert_guest_port_mac(ssh_client, port, server)

    def test_net_vf_guest_dataplane_mac_not_zero(self):
        """Verify each VM SR-IOV dataplane NIC MAC is not all zeros."""
        servers, key_pair = self._boot_sriov_vms()
        sender, sender_port, receiver, receiver_port = self._select_peer_ports(
            servers)
        for server, port in ((sender, sender_port), (receiver, receiver_port)):
            access_ip = server.get('fip') or server.get('fixed_ip')
            ssh_client = self.get_remote_client(
                access_ip, self.instance_user, key_pair['private_key'])
            self._assert_guest_port_mac_not_zero(ssh_client, port, server)

    # --- Traffic: one Tempest result per counter metric ---

    def test_net_vf_transmit_packets_total_increases_with_traffic(self):
        """Verify net_vf_transmit_packets_total increases on sender VF."""
        self._test_vf_counter_increases_with_traffic(
            metrics_base.NET_VF_TRANSMIT_PACKETS_METRIC, 'sender', 'packets')

    def test_net_vf_receive_packets_total_increases_with_traffic(self):
        """Verify net_vf_receive_packets_total increases on receiver VF."""
        self._test_vf_counter_increases_with_traffic(
            metrics_base.NET_VF_RECEIVE_PACKETS_METRIC, 'receiver', 'packets')

    def test_net_vf_transmit_bytes_total_increases_with_traffic(self):
        """Verify net_vf_transmit_bytes_total increases on sender VF."""
        self._test_vf_counter_increases_with_traffic(
            metrics_base.NET_VF_TRANSMIT_BYTES_METRIC, 'sender', 'bytes')

    def test_net_vf_receive_bytes_total_increases_with_traffic(self):
        """Verify net_vf_receive_bytes_total increases on receiver VF."""
        self._test_vf_counter_increases_with_traffic(
            metrics_base.NET_VF_RECEIVE_BYTES_METRIC, 'receiver', 'bytes')

    def test_net_vf_multicast_packets_total_increases_with_traffic(self):
        """Verify net_vf_multicast_packets_total increases on receiver VF."""
        self._test_vf_counter_increases_with_traffic(
            metrics_base.NET_VF_MULTICAST_PACKETS_METRIC, 'receiver',
            'packets', traffic_generator=self._send_multicast_packets,
            traffic_packet_count=CONF.nfv_plugin_options.
            network_exporter_sriov_multicast_flood_packets)

    def test_net_vf_broadcast_packets_total_increases_with_traffic(self):
        """Verify net_vf_broadcast_packets_total increases on receiver VF."""
        self._test_vf_counter_increases_with_traffic(
            metrics_base.NET_VF_BROADCAST_PACKETS_METRIC, 'receiver',
            'packets', traffic_generator=self._send_broadcast_packets,
            traffic_packet_count=CONF.nfv_plugin_options.
            network_exporter_sriov_broadcast_flood_packets)

    # --- Drop counters (test_z_* runs last; do not use a TestCase subclass
    #     for ordering — unittest re-discovers inherited test_* methods) ---

    def test_z_net_vf_receive_dropped_total_reported(self):
        """Verify net_vf_receive_dropped_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_RECEIVE_DROPPED_METRIC)

    def test_z_net_vf_transmit_dropped_total_reported(self):
        """Verify net_vf_transmit_dropped_total on compute and metric-storage."""
        self._assert_net_vf_metric_reported(
            metrics_base.NET_VF_TRANSMIT_DROPPED_METRIC)

    def test_z_net_vf_transmit_dropped_total_increases_with_traffic(self):
        """Verify net_vf_transmit_dropped_total increases after TX link-down."""
        self._test_vf_drop_counter_increases(
            metrics_base.NET_VF_TRANSMIT_DROPPED_METRIC, 'sender',
            traffic_generator=self._induce_transmit_drops,
            sysfs_stat_name='tx_dropped')

    def test_z_net_vf_receive_dropped_total_increases_with_traffic(self):
        """Verify net_vf_receive_dropped_total increases after RX UDP flood."""
        self._test_vf_drop_counter_increases(
            metrics_base.NET_VF_RECEIVE_DROPPED_METRIC, 'receiver',
            traffic_generator=self._induce_receive_drops,
            sysfs_stat_name='rx_dropped')
