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

OVNC_ENCAP_IP_METRIC = metrics_base.OVNC_ENCAP_IP_METRIC
OVNC_ENCAP_TYPE_METRIC = metrics_base.OVNC_ENCAP_TYPE_METRIC
OVNC_SB_CONNECTION_METHOD_METRIC = (
    metrics_base.OVNC_SB_CONNECTION_METHOD_METRIC)
OVNC_MONITOR_ALL_METRIC = metrics_base.OVNC_MONITOR_ALL_METRIC
OVNC_BRIDGE_MAPPINGS_METRIC = metrics_base.OVNC_BRIDGE_MAPPINGS_METRIC


class TestOvncConfigMetrics(metrics_base.NetworkExporterMetricsBase):
    """Verify ovnc_* config gauges against ovs-vsctl external_ids (Suite 7.1)."""

    def test_ovnc_encap_ip_reported(self):
        """Verify ovnc_encap_ip on OVN :1981 and metric-storage."""
        self._assert_ovnc_config_metric_reported(OVNC_ENCAP_IP_METRIC)

    def test_ovnc_encap_type_reported(self):
        """Verify ovnc_encap_type on OVN :1981 and metric-storage."""
        self._assert_ovnc_config_metric_reported(OVNC_ENCAP_TYPE_METRIC)

    def test_ovnc_sb_connection_method_reported(self):
        """Verify ovnc_sb_connection_method on OVN :1981 and metric-storage."""
        self._assert_ovnc_config_metric_reported(
            OVNC_SB_CONNECTION_METHOD_METRIC)

    def test_ovnc_monitor_all_reported(self):
        """Verify ovnc_monitor_all on OVN :1981 and metric-storage."""
        self._assert_ovnc_config_metric_reported(OVNC_MONITOR_ALL_METRIC)

    def test_ovnc_bridge_mappings_reported(self):
        """Verify ovnc_bridge_mappings on OVN :1981 and metric-storage."""
        self._assert_ovnc_config_metric_reported(OVNC_BRIDGE_MAPPINGS_METRIC)
