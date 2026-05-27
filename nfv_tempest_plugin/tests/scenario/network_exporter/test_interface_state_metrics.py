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

import paramiko
from tempest import config

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

OVS_INTERFACE_ADMIN_STATE_METRIC = metrics_base.OVS_INTERFACE_ADMIN_STATE_METRIC
OVS_INTERFACE_LINK_STATE_METRIC = metrics_base.OVS_INTERFACE_LINK_STATE_METRIC
OVS_STATE_UP = metrics_base.OVS_STATE_UP
OVS_STATE_DOWN = metrics_base.OVS_STATE_DOWN
# Linux IFNAMSIZ (16 bytes including NUL)
LINUX_MAX_IFNAME_LEN = 15
LEGACY_STATE_TEST_INTERFACES = (
    'tempest-ovs-state-test',
    'tempest-ovs-state-test-host',
)


class TestInterfaceStateMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_interface_admin_state and ovs_interface_link_state separately."""

    def _ssh_run_unchecked_on_hypervisor(self, hypervisor_ip, command):
        """SSH without enforcing exit status (cleanup helpers)."""
        return super(TestInterfaceStateMetrics, self)._ssh_run_on_hypervisor(
            hypervisor_ip, command)

    def _ssh_run_on_hypervisor(self, hypervisor_ip, command):
        """SSH on hypervisor; fail when the remote command exits non-zero."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_opts = {
            'allow_agent': False,
            'timeout': metrics_base.SSH_CONNECT_TIMEOUT,
            'banner_timeout': metrics_base.SSH_CONNECT_TIMEOUT,
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
        if rc != 0:
            self.fail(
                'SSH command on %s exited %s: %r\nstderr: %s\nstdout: %s' % (
                    hypervisor_ip, rc, command, err, out))
        return out

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
            metrics_base.NETWORK_EXPORTER_INSTANCE_PORT)
        hypervisor_ip = hypervisors[0]
        interface = self._state_test_interface()
        self._assert_valid_ifnames(interface)
        bridge = self._create_test_interface(hypervisor_ip, interface)
        self._active_state_test_bridge = bridge
        self.addCleanup(
            self._delete_test_interface, hypervisor_ip, bridge, interface)
        self._ensure_port_up(hypervisor_ip, interface)
        return hypervisor_ip, interface

    def _wait_for_admin_metric(self, hypervisor_ip, interface,
                               expected_admin_ovs):
        """Wait until ovs_interface_admin_state matches OVS admin_state."""
        labels = {'interface': interface}
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            if attempt > 0 and attempt % 2 == 0:
                self._set_ovs_admin_only(
                    hypervisor_ip, interface, expected_admin_ovs)
            metric_stdout, _, rc = self._metric_show(
                OVS_INTERFACE_ADMIN_STATE_METRIC)
            ovs_admin, _ = self._ovs_interface_states(hypervisor_ip, interface)
            reported = self._parse_compute_metric_show_value(
                metric_stdout, OVS_INTERFACE_ADMIN_STATE_METRIC,
                hypervisor_ip, row_contains=interface)
            prom = self._prom_compute_metric_value(
                hypervisor_ip, OVS_INTERFACE_ADMIN_STATE_METRIC, labels)
            last = {
                'ovs_admin': ovs_admin,
                'metric_admin': reported,
                'prom_admin': prom,
                'rc': rc,
            }
            if (ovs_admin == expected_admin_ovs and
                    self._metric_values_match_ovs(
                        ovs_admin, reported, prom)):
                LOG.warning(
                    '%s on %s for %s at admin=%s (attempt %s)',
                    OVS_INTERFACE_ADMIN_STATE_METRIC, hypervisor_ip, interface,
                    expected_admin_ovs, attempt + 1)
                return
            LOG.warning(
                'Attempt %s/%s waiting for %s admin=%s on %s: %s',
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                OVS_INTERFACE_ADMIN_STATE_METRIC, expected_admin_ovs,
                interface, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for %s on %s:%s to reach admin=%s with '
            'matching OVS, openstack metric show, and :9105 scrape. '
            'Last snapshot: %s. OVS details: %s' % (
                OVS_INTERFACE_ADMIN_STATE_METRIC, hypervisor_ip, interface,
                expected_admin_ovs, last,
                self._ovs_interface_diagnostic(
                    hypervisor_ip,
                    getattr(self, '_active_state_test_bridge',
                            self._state_test_bridge()),
                    interface)))

    def _wait_for_link_metric(self, hypervisor_ip, interface,
                              expected_link_ovs):
        """Wait until ovs_interface_link_state matches OVS link_state."""
        labels = {'interface': interface}
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            if attempt > 0 and attempt % 2 == 0:
                self._set_interface_link_state(
                    hypervisor_ip, interface, expected_link_ovs)
            metric_stdout, _, rc = self._metric_show(
                OVS_INTERFACE_LINK_STATE_METRIC)
            _, ovs_link = self._ovs_interface_states(hypervisor_ip, interface)
            reported = self._parse_compute_metric_show_value(
                metric_stdout, OVS_INTERFACE_LINK_STATE_METRIC,
                hypervisor_ip, row_contains=interface)
            prom = self._prom_compute_metric_value(
                hypervisor_ip, OVS_INTERFACE_LINK_STATE_METRIC, labels)
            last = {
                'ovs_link': ovs_link,
                'metric_link': reported,
                'prom_link': prom,
                'rc': rc,
            }
            if (ovs_link == expected_link_ovs and
                    self._metric_values_match_ovs(
                        ovs_link, reported, prom)):
                LOG.warning(
                    '%s on %s for %s at link=%s (attempt %s)',
                    OVS_INTERFACE_LINK_STATE_METRIC, hypervisor_ip, interface,
                    expected_link_ovs, attempt + 1)
                return
            LOG.warning(
                'Attempt %s/%s waiting for %s link=%s on %s: %s',
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                OVS_INTERFACE_LINK_STATE_METRIC, expected_link_ovs,
                interface, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for %s on %s:%s to reach link=%s with '
            'matching OVS, openstack metric show, and :9105 scrape. '
            'Last snapshot: %s. OVS details: %s' % (
                OVS_INTERFACE_LINK_STATE_METRIC, hypervisor_ip, interface,
                expected_link_ovs, last,
                self._ovs_interface_diagnostic(
                    hypervisor_ip,
                    getattr(self, '_active_state_test_bridge',
                            self._state_test_bridge()),
                    interface)))

    def test_ovs_interface_admin_state_updates_live(self):
        """Verify ovs_interface_admin_state tracks OVS admin_state changes."""
        hypervisor_ip, interface = self._setup_state_test_port(
            OVS_INTERFACE_ADMIN_STATE_METRIC)
        self._wait_for_admin_metric(hypervisor_ip, interface, 'up')
        self._set_ovs_admin_only(hypervisor_ip, interface, 'down')
        self._wait_for_admin_metric(hypervisor_ip, interface, 'down')
        self._set_ovs_admin_only(hypervisor_ip, interface, 'up')
        self._wait_for_admin_metric(hypervisor_ip, interface, 'up')

    def test_ovs_interface_link_state_updates_live(self):
        """Verify ovs_interface_link_state tracks OVS link_state changes."""
        hypervisor_ip, interface = self._setup_state_test_port(
            OVS_INTERFACE_LINK_STATE_METRIC)
        self._wait_for_link_metric(hypervisor_ip, interface, 'up')
        self._set_interface_link_state(hypervisor_ip, interface, 'down')
        self._wait_for_link_metric(hypervisor_ip, interface, 'down')
        self._set_interface_link_state(hypervisor_ip, interface, 'up')
        self._wait_for_link_metric(hypervisor_ip, interface, 'up')
