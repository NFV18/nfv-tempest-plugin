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

from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))

OVS_MEMORY_HANDLERS_TOTAL_METRIC = (
    metrics_base.OVS_MEMORY_HANDLERS_TOTAL_METRIC)
OVS_MEMORY_PORTS_TOTAL_METRIC = metrics_base.OVS_MEMORY_PORTS_TOTAL_METRIC
OVS_MEMORY_REVALIDATORS_TOTAL_METRIC = (
    metrics_base.OVS_MEMORY_REVALIDATORS_TOTAL_METRIC)
OVS_MEMORY_RULES_TOTAL_METRIC = metrics_base.OVS_MEMORY_RULES_TOTAL_METRIC
OVS_MEMORY_KEYS_TOTAL_METRIC = metrics_base.OVS_MEMORY_KEYS_TOTAL_METRIC


class TestOvsMemoryMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovs_memory_* gauges against ovs-appctl memory/show (Suite 6.1)."""

    def test_ovs_memory_handlers_total_reported(self):
        """Verify ovs_memory_handlers_total on compute and metric-storage."""
        self._assert_memory_metric_reported(OVS_MEMORY_HANDLERS_TOTAL_METRIC)

    def test_ovs_memory_ports_total_reported(self):
        """Verify ovs_memory_ports_total on compute and metric-storage."""
        self._assert_memory_metric_reported(OVS_MEMORY_PORTS_TOTAL_METRIC)

    def test_ovs_memory_revalidators_total_reported(self):
        """Verify ovs_memory_revalidators_total on compute and metric-storage."""
        self._assert_memory_metric_reported(
            OVS_MEMORY_REVALIDATORS_TOTAL_METRIC)

    def test_ovs_memory_rules_total_reported(self):
        """Verify ovs_memory_rules_total on compute and metric-storage."""
        self._assert_memory_metric_reported(OVS_MEMORY_RULES_TOTAL_METRIC)

    def test_ovs_memory_keys_total_reported(self):
        """Verify ovs_memory_keys_total on compute and metric-storage."""
        self._assert_memory_metric_reported(OVS_MEMORY_KEYS_TOTAL_METRIC)
