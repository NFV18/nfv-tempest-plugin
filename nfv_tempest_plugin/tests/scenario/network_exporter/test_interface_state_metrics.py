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

from tempest import config

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

CONF = config.CONF
LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

OVS_INTERFACE_ADMIN_STATE_METRIC = metrics_base.OVS_INTERFACE_ADMIN_STATE_METRIC
OVS_INTERFACE_LINK_STATE_METRIC = metrics_base.OVS_INTERFACE_LINK_STATE_METRIC
OVS_STATE_UP = metrics_base.OVS_STATE_UP
OVS_STATE_DOWN = metrics_base.OVS_STATE_DOWN


class TestInterfaceStateMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify interface state metrics update when admin state changes."""

    def _state_test_bridge(self):
        return CONF.nfv_plugin_options.network_exporter_state_test_bridge

    def _state_test_interface(self):
        return CONF.nfv_plugin_options.network_exporter_state_test_interface

    def _ovs_state_to_metric(self, ovs_value):
        """Map OVS admin_state/link_state strings to exporter gauge values."""
        if ovs_value == 'up':
            return OVS_STATE_UP
        if ovs_value == 'down':
            return OVS_STATE_DOWN
        return -1

    def _ovs_interface_states(self, hypervisor_ip, interface):
        """Return (admin_state, link_state) strings from OVSDB."""
        admin = self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl get Interface %s admin_state 2>/dev/null' %
            interface).strip().strip('"')
        link = self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl get Interface %s link_state 2>/dev/null' %
            interface).strip().strip('"')
        return admin, link

    def _set_interface_admin_state(self, hypervisor_ip, interface, state):
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s admin_state=%s' %
            (interface, state))

    def _create_test_interface(self, hypervisor_ip, bridge, interface):
        """Add a disposable internal port for state toggling."""
        cmd = (
            'sudo ovs-vsctl --may-exist del-port %(bridge)s %(iface)s; '
            'sudo ovs-vsctl add-port %(bridge)s %(iface)s -- '
            'set Interface %(iface)s type=internal' % {
                'bridge': bridge, 'iface': interface})
        self._ssh_run_on_hypervisor(hypervisor_ip, cmd)
        admin, link = self._ovs_interface_states(hypervisor_ip, interface)
        LOG.warning('Created test interface %s on %s bridge %s (admin=%s link=%s)',
                    interface, hypervisor_ip, bridge, admin, link)

    def _delete_test_interface(self, hypervisor_ip, bridge, interface):
        try:
            self._ssh_run_on_hypervisor(
                hypervisor_ip,
                'sudo ovs-vsctl --if-exists del-port %s %s' %
                (bridge, interface))
            LOG.warning('Removed test interface %s from bridge %s on %s',
                        interface, bridge, hypervisor_ip)
        except Exception as exc:
            LOG.warning('Could not remove test interface %s on %s: %s',
                        interface, hypervisor_ip, exc)

    def _wait_for_reported_interface_states(
            self, hypervisor_ip, interface, expected_admin, expected_link):
        """Wait until both metrics and OVSDB match expected states."""
        labels = {'interface': interface}
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            admin_stdout, _, rc_admin = self._metric_show(
                OVS_INTERFACE_ADMIN_STATE_METRIC)
            link_stdout, _, rc_link = self._metric_show(
                OVS_INTERFACE_LINK_STATE_METRIC)
            ovs_admin, ovs_link = self._ovs_interface_states(
                hypervisor_ip, interface)
            reported_admin = self._parse_compute_metric_show_value(
                admin_stdout, OVS_INTERFACE_ADMIN_STATE_METRIC,
                hypervisor_ip, row_contains=interface)
            reported_link = self._parse_compute_metric_show_value(
                link_stdout, OVS_INTERFACE_LINK_STATE_METRIC,
                hypervisor_ip, row_contains=interface)
            prom_admin = self._prom_compute_metric_value(
                hypervisor_ip, OVS_INTERFACE_ADMIN_STATE_METRIC, labels)
            prom_link = self._prom_compute_metric_value(
                hypervisor_ip, OVS_INTERFACE_LINK_STATE_METRIC, labels)
            last = {
                'ovs_admin': ovs_admin, 'ovs_link': ovs_link,
                'metric_admin': reported_admin, 'metric_link': reported_link,
                'prom_admin': prom_admin, 'prom_link': prom_link,
                'rc_admin': rc_admin, 'rc_link': rc_link,
            }
            if (self._ovs_state_to_metric(ovs_admin) == expected_admin and
                    self._ovs_state_to_metric(ovs_link) == expected_link and
                    reported_admin == expected_admin and
                    reported_link == expected_link and
                    prom_admin == expected_admin and
                    prom_link == expected_link):
                LOG.warning(
                    'Interface %s on %s reached admin=%s link=%s (attempt %s)',
                    interface, hypervisor_ip, expected_admin, expected_link,
                    attempt + 1)
                return
            LOG.warning(
                'Attempt %s/%s waiting for %s on %s: expected admin/link %s/%s, '
                'last snapshot %s', attempt + 1,
                metrics_base.METRIC_RETRY_ATTEMPTS, interface, hypervisor_ip,
                expected_admin, expected_link, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for interface %s on %s to report admin=%s '
            'link=%s in OVS, openstack metric show, and :9105 scrape. '
            'Last snapshot: %s' % (
                interface, hypervisor_ip, expected_admin, expected_link, last))

    def test_interface_admin_and_link_state_update_live(self):
        """Toggle admin state on a dummy port and verify metrics follow."""
        bridge = self._state_test_bridge()
        interface = self._state_test_interface()

        admin_stdout = self._assert_metric_reported(
            OVS_INTERFACE_ADMIN_STATE_METRIC)
        hypervisors = self._get_ssh_hypervisors(admin_stdout)
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors with %s metrics found' %
            metrics_base.NETWORK_EXPORTER_INSTANCE_PORT)
        hypervisor_ip = hypervisors[0]

        ovs_bridges = self._list_ovs_bridges_on_hypervisor(hypervisor_ip)
        self.assertIn(
            bridge, ovs_bridges,
            'State test bridge %s not found on %s (OVS has %s)' % (
                bridge, hypervisor_ip, ovs_bridges))

        self.addCleanup(
            self._delete_test_interface, hypervisor_ip, bridge, interface)
        self._create_test_interface(hypervisor_ip, bridge, interface)

        self._wait_for_reported_interface_states(
            hypervisor_ip, interface, OVS_STATE_UP, OVS_STATE_UP)

        self._set_interface_admin_state(hypervisor_ip, interface, 'down')
        self._wait_for_reported_interface_states(
            hypervisor_ip, interface, OVS_STATE_DOWN, OVS_STATE_DOWN)

        self._set_interface_admin_state(hypervisor_ip, interface, 'up')
        self._wait_for_reported_interface_states(
            hypervisor_ip, interface, OVS_STATE_UP, OVS_STATE_UP)
