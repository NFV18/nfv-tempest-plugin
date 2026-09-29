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
import uuid

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

OVNC_TXN_SUCCESS_METRIC = metrics_base.OVNC_TXN_SUCCESS_METRIC
OVNC_TXN_UNCOMMITTED_METRIC = metrics_base.OVNC_TXN_UNCOMMITTED_METRIC
OVNC_TXN_ABORTED_METRIC = metrics_base.OVNC_TXN_ABORTED_METRIC
OVNC_TXN_ERROR_METRIC = metrics_base.OVNC_TXN_ERROR_METRIC


class TestOvncTxnMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovnc_txn_* counters against ovn-controller coverage/show (7.2)."""

    def _pick_ovn_hypervisor(self):
        hypervisors = self._get_ssh_hypervisors('')
        self.assertNotEmpty(
            hypervisors,
            'No compute hypervisors available for OVN txn metrics')
        ovn_hypervisors = self._hypervisors_with_ovn_controller(hypervisors)
        self.assertNotEmpty(
            ovn_hypervisors,
            'No OVN controller on hypervisors %s' % hypervisors)
        return ovn_hypervisors[0]

    def test_ovnc_txn_success_reported(self):
        """Verify ovnc_txn_success on OVN :1981 and metric-storage."""
        self._assert_ovnc_txn_metric_reported(OVNC_TXN_SUCCESS_METRIC)

    def test_ovnc_txn_uncommitted_reported(self):
        """Verify ovnc_txn_uncommitted on OVN :1981 and metric-storage."""
        self._assert_ovnc_txn_metric_reported(OVNC_TXN_UNCOMMITTED_METRIC)

    def test_ovnc_txn_aborted_reported(self):
        """Verify ovnc_txn_aborted on OVN :1981 and metric-storage."""
        self._assert_ovnc_txn_metric_reported(OVNC_TXN_ABORTED_METRIC)

    def test_ovnc_txn_error_reported(self):
        """Verify ovnc_txn_error on OVN :1981 and metric-storage."""
        self._assert_ovnc_txn_metric_reported(OVNC_TXN_ERROR_METRIC)

    def test_ovnc_txn_success_increases_with_network_create_delete(self):
        """Create/delete a Neutron network and verify txn_success increases."""
        hypervisor_ip = self._pick_ovn_hypervisor()
        baseline_show, baseline_live = self._ovnc_txn_success_totals(
            hypervisor_ip)
        LOG.warning(
            'OVN txn_success baseline on %s: coverage/show=%s live=%s',
            hypervisor_ip, baseline_show, baseline_live)

        network_name = 'tempest-ovnc-txn-%s' % uuid.uuid4().hex[:8]
        network = self.os_admin.networks_client.create_network(
            body={'network': {'name': network_name}})['network']
        self.addCleanup(
            self.os_admin.networks_client.delete_network, network['id'])

        last = {}
        last_exc = None
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            try:
                show_total, live_total = self._ovnc_txn_success_totals(
                    hypervisor_ip)
                last = {
                    'show_total': show_total,
                    'live_total': live_total,
                    'baseline_show': baseline_show,
                    'baseline_live': baseline_live,
                }
                self.assertGreaterEqual(
                    show_total, baseline_show + 1,
                    'coverage/show txn_success did not increase on %s' % (
                        hypervisor_ip))
                self.assertGreaterEqual(
                    live_total, baseline_live + 1,
                    'live :1981 ovnc_txn_success did not increase on %s' % (
                        hypervisor_ip))
                LOG.warning(
                    'OVN txn_success increased on %s (attempt %s): '
                    'show +%s live +%s',
                    hypervisor_ip, attempt + 1,
                    show_total - baseline_show,
                    live_total - baseline_live)
                return
            except Exception as exc:
                last_exc = exc
                LOG.warning(
                    'Attempt %s/%s waiting for ovnc_txn_success increase: %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    exc)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            'Timed out waiting for ovnc_txn_success increase after network '
            'create on %s. Last %s; last error: %s' % (
                hypervisor_ip, last, last_exc))
