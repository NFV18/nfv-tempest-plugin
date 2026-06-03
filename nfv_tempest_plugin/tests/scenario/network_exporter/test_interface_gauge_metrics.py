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

OVS_INTERFACE_MTU_BYTES_METRIC = metrics_base.OVS_INTERFACE_MTU_BYTES_METRIC
OVS_INTERFACE_LINK_SPEED_BPS_METRIC = (
    metrics_base.OVS_INTERFACE_LINK_SPEED_BPS_METRIC)
OVS_INTERFACE_LINK_RESETS_METRIC = (
    metrics_base.OVS_INTERFACE_LINK_RESETS_METRIC)
# Alternate MTU applied on the disposable veth to verify live updates.
STATE_TEST_MTU_ALTERNATE = 1400
# :9105 scrape reports link_speed as OVS bps / 10^10 (e.g. 10G -> 1).
LINK_SPEED_PROM_SCALE_BPS = 10_000_000_000
# Link down/up cycles applied to raise link_resets by at least one.
STATE_TEST_LINK_FLAP_COUNT = 2
STATE_TEST_LINK_FLAP_SETTLE_SEC = 2


class TestInterfaceGaugeMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify interface MTU, link speed, and link reset metrics."""

    def _ovs_mtu_bytes(self, hypervisor_ip, interface):
        raw = self._ovs_field(hypervisor_ip, interface, 'mtu')
        if raw is None:
            return None
        return int(raw)

    def _ovs_link_speed_bps(self, hypervisor_ip, interface):
        raw = self._ovs_field(hypervisor_ip, interface, 'link_speed')
        if raw is None:
            return None
        return int(raw)

    def _ovs_link_resets(self, hypervisor_ip, interface):
        raw = self._ovs_field(hypervisor_ip, interface, 'link_resets')
        if raw is None:
            return None
        return int(raw)

    def _flap_interface_link(self, hypervisor_ip, interface, count=None):
        """Cycle kernel link down/up to increment OVS link_resets."""
        if count is None:
            count = STATE_TEST_LINK_FLAP_COUNT
        for _ in range(count):
            self._set_interface_link_state(hypervisor_ip, interface, 'down')
            time.sleep(STATE_TEST_LINK_FLAP_SETTLE_SEC)
            self._set_interface_link_state(hypervisor_ip, interface, 'up')
            time.sleep(STATE_TEST_LINK_FLAP_SETTLE_SEC)

    def _prom_matches_ovs_gauge(self, expected, prom_value, prom_scale=1):
        """True when :9105 value matches OVS (raw bps or scaled)."""
        if prom_value is None:
            return False
        if prom_scale == 1:
            return prom_value == expected
        return (prom_value == expected or
                prom_value * prom_scale == expected)

    def _metric_values_match_int(self, expected, reported, prom_value,
                                 prom_scale=1):
        if expected is None:
            return False
        if reported is None:
            return False
        return (reported == expected and
                self._prom_matches_ovs_gauge(expected, prom_value, prom_scale))

    def _set_interface_mtu(self, hypervisor_ip, interface, mtu):
        """Set MTU on veth legs and request the same MTU in OVS."""
        for dev in (self._veth_peer_name(interface), interface):
            self._ssh_run_on_hypervisor(
                hypervisor_ip, 'sudo ip link set dev %s mtu %d' % (dev, mtu))
        self._ssh_run_on_hypervisor(
            hypervisor_ip,
            'sudo ovs-vsctl set Interface %s mtu_request=%d' %
            (interface, mtu))

    def _wait_for_gauge_metric(
            self, hypervisor_ip, interface, metric_name, ovs_getter,
            expected_value, reapply=None, prom_scale=1):
        """Wait until a numeric interface gauge matches OVS across all sources."""
        labels = {'interface': interface}
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            if reapply is not None and attempt > 0 and attempt % 2 == 0:
                reapply()
            metric_stdout, _, rc = self._metric_show(metric_name)
            ovs_value = ovs_getter(hypervisor_ip, interface)
            reported = self._parse_compute_metric_show_value(
                metric_stdout, metric_name, hypervisor_ip,
                row_contains=interface)
            prom = self._prom_compute_metric_value(
                hypervisor_ip, metric_name, labels)
            last = {
                'ovs': ovs_value,
                'metric': reported,
                'prom': prom,
                'rc': rc,
            }
            if (ovs_value == expected_value and
                    self._metric_values_match_int(
                        expected_value, reported, prom, prom_scale)):
                LOG.warning(
                    '%s on %s for %s at %s (attempt %s)',
                    metric_name, hypervisor_ip, interface, expected_value,
                    attempt + 1)
                return
            LOG.warning(
                'Attempt %s/%s waiting for %s=%s on %s: %s',
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                metric_name, expected_value, interface, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for %s on %s:%s to reach %s with matching '
            'OVS, openstack metric show, and :9105 scrape. Last snapshot: %s. '
            'OVS details: %s' % (
                metric_name, hypervisor_ip, interface, expected_value, last,
                self._ovs_interface_diagnostic(
                    hypervisor_ip,
                    getattr(self, '_active_state_test_bridge',
                            self._state_test_bridge()),
                    interface)))

    def _wait_for_link_resets_metric(
            self, hypervisor_ip, interface, min_resets, reapply=None):
        """Wait until link_resets is >= min_resets and matches all sources."""
        labels = {'interface': interface}
        metric_name = OVS_INTERFACE_LINK_RESETS_METRIC
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            if reapply is not None and attempt > 0 and attempt % 2 == 0:
                reapply()
            metric_stdout, _, rc = self._metric_show(metric_name)
            ovs_value = self._ovs_link_resets(hypervisor_ip, interface)
            reported = self._parse_compute_metric_show_value(
                metric_stdout, metric_name, hypervisor_ip,
                row_contains=interface)
            prom = self._prom_compute_metric_value(
                hypervisor_ip, metric_name, labels)
            last = {
                'ovs': ovs_value, 'metric': reported, 'prom': prom, 'rc': rc,
                'min_resets': min_resets,
            }
            if (ovs_value is not None and ovs_value >= min_resets and
                    self._metric_values_match_int(
                        ovs_value, reported, prom)):
                LOG.warning(
                    '%s on %s for %s at %s (>=%s) (attempt %s)',
                    metric_name, hypervisor_ip, interface, ovs_value,
                    min_resets, attempt + 1)
                return ovs_value
            LOG.warning(
                'Attempt %s/%s waiting for %s>=%s on %s: %s',
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                metric_name, min_resets, interface, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for %s on %s:%s to reach >=%s with matching '
            'OVS, openstack metric show, and :9105 scrape. Last snapshot: %s. '
            'OVS details: %s' % (
                metric_name, hypervisor_ip, interface, min_resets, last,
                self._ovs_interface_diagnostic(
                    hypervisor_ip,
                    getattr(self, '_active_state_test_bridge',
                            self._state_test_bridge()),
                    interface)))

    def test_ovs_interface_mtu_bytes_updates_live(self):
        """Verify ovs_interface_mtu_bytes tracks OVS Interface mtu changes."""
        hypervisor_ip, interface = self._setup_state_test_port(
            OVS_INTERFACE_MTU_BYTES_METRIC)
        initial_mtu = self._ovs_mtu_bytes(hypervisor_ip, interface)
        self.assertIsNotNone(
            initial_mtu,
            'OVS did not report mtu for %s on %s' % (interface, hypervisor_ip))
        self._wait_for_gauge_metric(
            hypervisor_ip, interface, OVS_INTERFACE_MTU_BYTES_METRIC,
            self._ovs_mtu_bytes, initial_mtu)

        self._set_interface_mtu(
            hypervisor_ip, interface, STATE_TEST_MTU_ALTERNATE)
        self._wait_for_gauge_metric(
            hypervisor_ip, interface, OVS_INTERFACE_MTU_BYTES_METRIC,
            self._ovs_mtu_bytes, STATE_TEST_MTU_ALTERNATE,
            reapply=lambda: self._set_interface_mtu(
                hypervisor_ip, interface, STATE_TEST_MTU_ALTERNATE))

    def test_ovs_interface_link_speed_bps_matches_ovs(self):
        """Verify ovs_interface_link_speed_bps matches OVS link_speed."""
        hypervisor_ip, interface = self._setup_state_test_port(
            OVS_INTERFACE_LINK_SPEED_BPS_METRIC)
        link_speed = self._ovs_link_speed_bps(hypervisor_ip, interface)
        self.assertIsNotNone(
            link_speed,
            'OVS did not report link_speed for %s on %s (is the test port '
            'up?)' % (interface, hypervisor_ip))
        self.assertGreater(link_speed, 0)
        # openstack metric show uses OVS bps; :9105 uses bps / 10^10.
        self._wait_for_gauge_metric(
            hypervisor_ip, interface, OVS_INTERFACE_LINK_SPEED_BPS_METRIC,
            self._ovs_link_speed_bps, link_speed,
            reapply=lambda: self._ensure_port_up(hypervisor_ip, interface),
            prom_scale=LINK_SPEED_PROM_SCALE_BPS)

    def test_ovs_interface_link_resets_increments_on_flap(self):
        """Verify ovs_interface_link_resets increases when the link flaps."""
        hypervisor_ip, interface = self._setup_state_test_port(
            OVS_INTERFACE_LINK_RESETS_METRIC)
        baseline = self._ovs_link_resets(hypervisor_ip, interface)
        self.assertIsNotNone(
            baseline,
            'OVS did not report link_resets for %s on %s' % (
                interface, hypervisor_ip))
        self._wait_for_link_resets_metric(
            hypervisor_ip, interface, baseline)

        self._flap_interface_link(hypervisor_ip, interface)
        self._wait_for_link_resets_metric(
            hypervisor_ip, interface, baseline + 1,
            reapply=lambda: self._flap_interface_link(
                hypervisor_ip, interface, count=1))

        self._ensure_port_up(hypervisor_ip, interface)
        LOG.warning(
            'Interface %s link_resets went from %s to %s after link flap',
            interface, baseline,
            self._ovs_link_resets(hypervisor_ip, interface))
