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

import re
import time
import unittest

import paramiko
from kubernetes.client.rest import ApiException
from tempest import config

from nfv_tempest_plugin.tests.common import k8s
from nfv_tempest_plugin.tests.scenario import base_test
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

METRIC_RETRY_ATTEMPTS = 6
METRIC_RETRY_INTERVAL = 30
OVS_BUILD_INFO_METRIC = 'ovs_build_info'
OVS_DPDK_INITIALIZED_METRIC = 'ovs_dpdk_initialized'
OVS_BRIDGE_PORT_COUNT_METRIC = 'ovs_bridge_port_count'
OVS_BRIDGE_FLOW_COUNT_METRIC = 'ovs_bridge_flow_count'
OVN_NORTHD_STATUS_METRIC = 'ovn_northd_status'
OVS_INTERFACE_ADMIN_STATE_METRIC = 'ovs_interface_admin_state'
OVS_INTERFACE_LINK_STATE_METRIC = 'ovs_interface_link_state'
OVS_INTERFACE_MTU_BYTES_METRIC = 'ovs_interface_mtu_bytes'
OVS_INTERFACE_LINK_SPEED_BPS_METRIC = 'ovs_interface_link_speed_bps'
OVS_INTERFACE_LINK_RESETS_METRIC = 'ovs_interface_link_resets'
# OVN/K8s service metrics (northd, controller, etc.), not compute :9105
OVN_K8S_METRICS_PORT = ':1981'
# openstack-network-exporter: 0=standby, 1=active, 2=paused
OVN_NORTHD_STATUS_VALUES = (0, 1, 2)
OVN_NORTHD_STATUS_ACTIVE = 1
# openstack-network-exporter: admin/link up=1, down=0; link unknown=-1
OVS_STATE_UP = 1
OVS_STATE_DOWN = 0
OPENSTACK_NAMESPACE = 'openstack'
OPENSTACK_CLIENT_POD = 'openstackclient'
OPENSTACK_CLIENT_CONTAINER = 'openstackclient'
NETWORK_EXPORTER_INSTANCE_PORT = ':9105'
FLOW_COUNT_RE = re.compile(r'flow_count=(\d+)', re.IGNORECASE)
METRIC_ROW_VALUE_RE = re.compile(r'(\d+)\s*\|?\s*$')
COMPUTE_METRICS_HOST_RE = re.compile(
    r'(\d+\.\d+\.\d+\.\d+)' + re.escape(NETWORK_EXPORTER_INSTANCE_PORT))
SSH_CONNECT_TIMEOUT = 30
# Linux IFNAMSIZ (16 bytes including NUL)
LINUX_MAX_IFNAME_LEN = 15
LEGACY_STATE_TEST_INTERFACES = (
    'tempest-ovs-state-test',
    'tempest-ovs-state-test-host',
)


class NetworkExporterMetricsBase(base_test.BaseTest):
    """Shared helpers for openstack-network-exporter Tempest tests."""

    def __init__(self, *args, **kwargs):
        super(NetworkExporterMetricsBase, self).__init__(*args, **kwargs)
        self.k8s_client = k8s.openshift_client()
        self._hypervisor_id_cache = {}

    def _metric_show(self, metric_name):
        """Run openstack metric show in the openstackclient pod."""
        cmd = 'openstack metric show %s --disable-rbac' % metric_name
        LOG.info("Executing in pod %s/%s: %s",
                 OPENSTACK_NAMESPACE, OPENSTACK_CLIENT_POD, cmd)
        try:
            stdout = self.k8s_client.execute_command_in_pod(
                OPENSTACK_CLIENT_POD, OPENSTACK_NAMESPACE,
                OPENSTACK_CLIENT_CONTAINER, cmd)
            return stdout or '', '', 0
        except ApiException as exc:
            msg = 'kubernetes API %s: %s' % (exc.status, exc.body or exc.reason)
            LOG.warning("Pod exec API error: %s", msg)
            return '', msg, 1
        except Exception as exc:
            return '', str(exc), 1

    def _assert_metric_reported(self, metric_name, output_markers=None):
        """Wait until openstack metric show succeeds for metric_name."""
        if output_markers is None:
            output_markers = [metric_name]
        stdout = stderr = ''
        returncode = 1
        for attempt in range(METRIC_RETRY_ATTEMPTS):
            stdout, stderr, returncode = self._metric_show(metric_name)
            stdout = stdout or ''
            if returncode == 0 and metric_name in stdout:
                missing = [m for m in output_markers if m not in stdout]
                if not missing:
                    self.assertTrue(stdout.strip(),
                                    'openstack metric show returned empty '
                                    'output for %s' % metric_name)
                    LOG.info("Metric '%s' is reported (%s bytes)",
                                metric_name, len(stdout))
                    return stdout
            LOG.warning("Attempt %s/%s for metric '%s' failed: exit %s, "
                        "stderr: %s", attempt + 1, METRIC_RETRY_ATTEMPTS,
                        metric_name, returncode, stderr)
            if attempt < METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(METRIC_RETRY_INTERVAL)
        stdout = stdout or ''
        msg = ("Metric '%s' not found or openstack command failed "
               "(exit %s). stderr: %s stdout: %s" %
               (metric_name, returncode, stderr, stdout))
        self.assertEqual(0, returncode, msg)
        self.assertIn(metric_name, stdout,
                      "Metric '%s' not present in command output. stdout: %s"
                      % (metric_name, stdout))
        missing = [m for m in output_markers if m not in stdout]
        self.assertFalse(
            missing,
            "Metric '%s' output missing required markers %s. stdout: %s" %
            (metric_name, missing, stdout))
        return stdout

    def _split_metric_table_row(self, line):
        """Split an openstack metric show table row into columns."""
        if '|' not in line:
            return None
        parts = [part.strip() for part in line.split('|')]
        if parts and not parts[0]:
            parts = parts[1:]
        if parts and not parts[-1]:
            parts = parts[:-1]
        if len(parts) < 3:
            return None
        return parts

    def _parse_metric_row_value(self, parts):
        """Return the numeric value column from a trimmed metric table row."""
        for cell in reversed(parts):
            try:
                return int(float(cell))
            except ValueError:
                continue
        return None

    def _hypervisor_identifiers(self, hypervisor_ip):
        """Strings that identify a hypervisor in openstack metric show rows."""
        if hypervisor_ip in self._hypervisor_id_cache:
            return self._hypervisor_id_cache[hypervisor_ip]
        identifiers = {hypervisor_ip}
        for hyp in self.os_admin.hypervisor_client.list_hypervisors(
                detail=True)['hypervisors']:
            if hyp.get('host_ip', '').strip() != hypervisor_ip.strip():
                continue
            hostname = hyp['hypervisor_hostname']
            identifiers.add(hostname)
            identifiers.add(hostname.split('.')[0])
        self._hypervisor_id_cache[hypervisor_ip] = identifiers
        return identifiers

    def _exporter_instance_cell(self, parts):
        """Return the instance column (contains ':9105') from a table row."""
        for part in parts:
            if NETWORK_EXPORTER_INSTANCE_PORT in part:
                return part
        return None

    def _row_is_compute_network_exporter(self, parts):
        """True if row is from openstack-network-exporter on a compute node."""
        instance = self._exporter_instance_cell(parts)
        return (instance is not None and
                'ovn-controller-metrics' not in instance)

    def _row_matches_hypervisor(self, parts, hypervisor_ip):
        """Match metric rows to a hypervisor by IP, hostname, or FQDN."""
        if not self._row_is_compute_network_exporter(parts):
            return False
        row_text = ' '.join(parts)
        return any(identifier in row_text
                   for identifier in self._hypervisor_identifiers(hypervisor_ip))

    def _line_matches_hypervisor(self, line, hypervisor_ip):
        return any(identifier in line
                   for identifier in self._hypervisor_identifiers(hypervisor_ip))

    def _is_ovn_k8s_metrics_row(self, line):
        """True for OVN metrics scraped via openstack.svc:1981 (not compute)."""
        return (OVN_K8S_METRICS_PORT in line and 'openstack.svc' in line and
                NETWORK_EXPORTER_INSTANCE_PORT not in line)

    def _parse_ovn_k8s_metric_values(self, metric_stdout):
        """Parse numeric values from OVN K8s metrics table rows."""
        values = []
        for line in metric_stdout.splitlines():
            if not self._is_ovn_k8s_metrics_row(line):
                continue
            parts = self._split_metric_table_row(line)
            if parts and parts[0] in ('instance', 'bridge'):
                continue
            if parts:
                value = self._parse_metric_row_value(parts)
                if value is not None:
                    values.append(value)
                    continue
            match = METRIC_ROW_VALUE_RE.search(line.strip())
            if match:
                values.append(int(match.group(1)))
        return values

    def _exporter_instance_samples(self, metric_stdout):
        """Distinct :9105 instance labels (for assertion messages)."""
        samples = set()
        for line in metric_stdout.splitlines():
            parts = self._split_metric_table_row(line)
            if not parts:
                continue
            instance = self._exporter_instance_cell(parts)
            if instance and 'ovn-controller-metrics' not in instance:
                samples.add(instance)
        return sorted(samples)

    def _parse_compute_metric_show_values(
            self, metric_stdout, hypervisor_ip=None, first_column=None,
            row_contains=None):
        """Parse :9105 metric values from openstack metric show output."""
        if not metric_stdout:
            return []
        values = []
        for line in metric_stdout.splitlines():
            if row_contains and row_contains not in line:
                continue
            parts = self._split_metric_table_row(line)
            if parts:
                if first_column and parts[0] != first_column:
                    continue
                if hypervisor_ip and not self._row_matches_hypervisor(
                        parts, hypervisor_ip):
                    continue
                value = self._parse_metric_row_value(parts)
                if value is not None:
                    values.append(value)
                    continue
            if (not parts and row_contains and hypervisor_ip and
                    NETWORK_EXPORTER_INSTANCE_PORT in line and
                    'ovn-controller-metrics' not in line and
                    self._line_matches_hypervisor(line, hypervisor_ip)):
                match = METRIC_ROW_VALUE_RE.search(line.strip())
                if match:
                    values.append(int(match.group(1)))
        return values

    def _parse_compute_metric_show_value(
            self, metric_stdout, metric_name, hypervisor_ip, row_contains=None,
            first_column=None):
        """Return the first matching value for one compute metric row."""
        if not metric_stdout:
            return None
        for line in metric_stdout.splitlines():
            if metric_name not in line:
                continue
            if row_contains and row_contains not in line:
                continue
            if (NETWORK_EXPORTER_INSTANCE_PORT not in line or
                    'ovn-controller-metrics' in line):
                continue
            if hypervisor_ip and not self._line_matches_hypervisor(
                    line, hypervisor_ip):
                continue
            parts = self._split_metric_table_row(line)
            if parts:
                if first_column and parts[0] != first_column:
                    continue
                value = self._parse_metric_row_value(parts)
                if value is not None:
                    return value
            match = METRIC_ROW_VALUE_RE.search(line.strip())
            if match:
                return int(match.group(1))
        return None

    def _parse_metric_values_for_bridge(self, metric_stdout, bridge,
                                        hypervisor_ip=None):
        """Parse bridge metric values from openstack metric show."""
        values = self._parse_compute_metric_show_values(
            metric_stdout, hypervisor_ip=hypervisor_ip, first_column=bridge)
        if not values and hypervisor_ip:
            values = self._parse_metric_values_for_bridge_fallback(
                metric_stdout, bridge, hypervisor_ip)
        return values

    def _parse_metric_values_for_bridge_fallback(self, metric_stdout, bridge,
                                                 hypervisor_ip):
        """Fallback parser when pipe-split rows do not match."""
        values = []
        for line in metric_stdout.splitlines():
            if (bridge not in line or NETWORK_EXPORTER_INSTANCE_PORT not in line
                    or 'ovn-controller-metrics' in line):
                continue
            if not self._line_matches_hypervisor(line, hypervisor_ip):
                continue
            match = METRIC_ROW_VALUE_RE.search(line.strip())
            if match:
                values.append(int(match.group(1)))
        return values

    def _bridges_reported_for_hypervisor(self, metric_stdout, hypervisor_ip):
        """Bridge names with :9105 metric rows for hypervisor_ip."""
        bridges = set()
        for line in metric_stdout.splitlines():
            parts = self._split_metric_table_row(line)
            if not parts:
                continue
            if self._row_matches_hypervisor(parts, hypervisor_ip):
                bridges.add(parts[0])
        if not bridges:
            for line in metric_stdout.splitlines():
                if (NETWORK_EXPORTER_INSTANCE_PORT not in line
                        or 'ovn-controller-metrics' in line):
                    continue
                if not self._line_matches_hypervisor(line, hypervisor_ip):
                    continue
                parts = self._split_metric_table_row(line)
                if parts:
                    bridges.add(parts[0])
        return sorted(bridges)

    def _hypervisor_ips_from_metric_stdout(self, metric_stdout):
        """Compute hypervisor IPs that expose openstack-network-exporter :9105."""
        return sorted({
            match.group(1) for line in metric_stdout.splitlines()
            for match in [COMPUTE_METRICS_HOST_RE.search(line)] if match})

    def _get_ssh_hypervisors(self, metric_stdout):
        """Hypervisors to SSH: prefer :9105 targets from metric show."""
        hypervisors = self._hypervisor_ips_from_metric_stdout(metric_stdout)
        if hypervisors:
            return hypervisors
        LOG.warning(
            'No %s instances in metric output; falling back to Nova '
            'hypervisor list', NETWORK_EXPORTER_INSTANCE_PORT)
        return self._get_hypervisor_ip_from_undercloud()

    def _ssh_run_on_hypervisor(self, hypervisor_ip, command, check_rc=True):
        """Run command on hypervisor over SSH with connect timeout."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_opts = {
            'allow_agent': False,
            'timeout': SSH_CONNECT_TIMEOUT,
            'banner_timeout': SSH_CONNECT_TIMEOUT,
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
        if check_rc and rc != 0:
            self.fail(
                'SSH command on %s exited %s: %r\nstderr: %s\nstdout: %s' % (
                    hypervisor_ip, rc, command, err, out))
        return out

    def _ssh_run_unchecked_on_hypervisor(self, hypervisor_ip, command):
        """SSH without enforcing exit status (cleanup helpers)."""
        return self._ssh_run_on_hypervisor(
            hypervisor_ip, command, check_rc=False)

    def _list_ovs_bridges_on_hypervisor(self, hypervisor_ip):
        """Return OVS bridge names on a compute hypervisor (ovs-vsctl list-br)."""
        cmd = 'sudo ovs-vsctl list-br 2>/dev/null'
        out = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
        return sorted({line.strip() for line in out.splitlines() if line.strip()})

    def _prom_compute_metric_value(self, hypervisor_ip, metric_name, labels):
        """Read one gauge from the local :9105 scrape matching Prometheus labels."""
        grep = "grep '^%s{'" % metric_name
        for key in sorted(labels):
            grep += " | grep '%s=\"%s\"'" % (key, labels[key])
        cmd = (
            "curl -sk https://127.0.0.1:9105/metrics 2>/dev/null | %s" % grep)
        out = self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
        pattern = re.compile(
            r'%s\{[^}]*\}\s+(-?\d+(?:\.\d+)?)' % re.escape(metric_name))
        for line in out.splitlines():
            match = pattern.search(line)
            if match:
                return int(float(match.group(1)))
        cmd_http = (
            "curl -s http://127.0.0.1:9105/metrics 2>/dev/null | %s" % grep)
        out = self._ssh_run_on_hypervisor(hypervisor_ip, cmd_http)
        for line in out.splitlines():
            match = pattern.search(line)
            if match:
                return int(float(match.group(1)))
        return None

    def _parse_prom_metric_text(self, metrics_output, metric_name, labels):
        """Parse a gauge from Prometheus exposition text."""
        for line in metrics_output.splitlines():
            if not line.startswith(metric_name):
                continue
            if any('%s="%s"' % (key, labels[key]) not in line
                   for key in labels):
                continue
            parts = line.rsplit(None, 1)
            if len(parts) == 2:
                try:
                    return int(float(parts[1]))
                except ValueError:
                    continue
        return None

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
            NETWORK_EXPORTER_INSTANCE_PORT)
        hypervisor_ip = hypervisors[0]
        interface = self._state_test_interface()
        self._assert_valid_ifnames(interface)
        bridge = self._create_test_interface(hypervisor_ip, interface)
        self._active_state_test_bridge = bridge
        self.addCleanup(
            self._delete_test_interface, hypervisor_ip, bridge, interface)
        self._ensure_port_up(hypervisor_ip, interface)
        return hypervisor_ip, interface
