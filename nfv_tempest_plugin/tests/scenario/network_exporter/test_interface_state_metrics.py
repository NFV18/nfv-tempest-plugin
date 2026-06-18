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

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

OVS_INTERFACE_ADMIN_STATE_METRIC = metrics_base.OVS_INTERFACE_ADMIN_STATE_METRIC
OVS_INTERFACE_LINK_STATE_METRIC = metrics_base.OVS_INTERFACE_LINK_STATE_METRIC


class TestInterfaceStateMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_interface_admin_state and ovs_interface_link_state separately."""

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
