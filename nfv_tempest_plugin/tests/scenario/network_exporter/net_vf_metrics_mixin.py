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

"""Reusable net_vf metric validation helpers for SR-IOV VF Tempest tests."""

import shlex
import time
import unittest

from nfv_tempest_plugin.tests.common import shell_utilities as shell_utils
from nfv_tempest_plugin.tests.scenario.network_exporter import metrics_base
from oslo_log import log as logging

LOG = logging.getLogger('{} [-] nfv_plugin_test'.format(__name__))


class NetVfMetricsMixin(object):
    """Validate net_vf_* metrics against compute :9105, metric-storage, and sysfs."""

    def _metrics_text_from_hypervisor(self, hypervisor_ip):
        return self._scrape_compute_metrics_text(hypervisor_ip)

    def _assert_net_vf_metric_reported(self, metric_name):
        """Assert net_vf* on compute :9105 and in metric-storage Prometheus."""
        stdout, stderr, returncode = self._metric_show(metric_name)
        stdout = stdout or ''
        hypervisor_ip = None
        if (returncode == 0 and metric_name in stdout and
                metrics_base.NETWORK_EXPORTER_INSTANCE_PORT in stdout):
            LOG.warning(
                "Metric '%s' reported via openstack metric show", metric_name)
            hypervisors = self._hypervisor_ips_from_metric_stdout(stdout)
            if hypervisors:
                hypervisor_ip = hypervisors[0]
        else:
            LOG.warning(
                "openstack metric show unavailable for '%s' (exit %s: %s); "
                "falling back to compute :9105 SSH scrape",
                metric_name, returncode, stderr)
            self._assert_metric_on_compute_scrape(metric_name)
        if not hypervisor_ip:
            hypervisor_ip = self._hypervisor_for_net_vf_metric(metric_name)
        self.assertIsNotNone(
            hypervisor_ip,
            "Could not find a hypervisor exporting %s on compute :9105" %
            metric_name)
        self._assert_net_vf_metric_present_in_metric_storage(
            hypervisor_ip, metric_name)
        return stdout

    def _prom_samples(self, hypervisor_ip, metric_name, required_labels=None,
                      metrics_output=None):
        if metrics_output is None:
            metrics_output = self._metrics_text_from_hypervisor(hypervisor_ip)
        return self._parse_prom_samples(
            metrics_output, metric_name, required_labels)

    def _net_vf_sample_key(self, labels):
        """Stable VF identity shared by compute scrape and metric-storage."""
        return (labels.get('device'), labels.get('vf'),
                labels.get('pci_address'))

    def _hypervisor_for_net_vf_metric(self, metric_name):
        """Return a hypervisor IP that exports metric_name on compute :9105."""
        for hypervisor_ip in self._get_hypervisor_ip_from_undercloud():
            if self._prom_samples(hypervisor_ip, metric_name):
                return hypervisor_ip
        return None

    def _vf_identity_labels(self, vf_labels):
        """Label subset used to match one VF across compute and metric-storage."""
        if not vf_labels:
            return None
        return {key: vf_labels[key]
                for key in ('device', 'vf', 'pci_address')
                if key in vf_labels}

    def _assert_net_vf_metric_present_in_metric_storage(
            self, hypervisor_ip, metric_name):
        """Verify metric series exist on compute :9105 and in metric-storage."""
        compute_samples = self._prom_samples(hypervisor_ip, metric_name)
        self.assertNotEmpty(
            compute_samples,
            "No %s samples on compute :9105 for %s" % (
                metric_name, hypervisor_ip))
        storage_samples, query_error = self._metric_storage_samples(
            metric_name, hypervisor_ip=hypervisor_ip)
        self.assertNotEmpty(
            storage_samples,
            "%s missing from metric-storage Prometheus for %s "
            "(compute had %d series). Query error: %s" % (
                metric_name, hypervisor_ip, len(compute_samples),
                query_error))
        compute_keys = {
            self._net_vf_sample_key(sample['labels'])
            for sample in compute_samples}
        storage_keys = {
            self._net_vf_sample_key(sample['labels'])
            for sample in storage_samples}
        overlap = compute_keys & storage_keys
        self.assertNotEmpty(
            overlap,
            "%s on %s: no VF series in common between compute :9105 and "
            "metric-storage (compute=%d storage=%d series). "
            "Sample compute keys: %s; storage keys: %s" % (
                metric_name, hypervisor_ip, len(compute_keys),
                len(storage_keys), sorted(compute_keys)[:3],
                sorted(storage_keys)[:3]))
        LOG.warning(
            "%s on %s present in compute :9105 and metric-storage "
            "(%d common VF series of compute=%d storage=%d)",
            metric_name, hypervisor_ip, len(overlap),
            len(compute_keys), len(storage_keys))

    def _assert_net_vf_metrics_match_metric_storage(
            self, hypervisor_ip, metric_name, vf_labels=None):
        """Compare net_vf* values on compute :9105 vs metric-storage for VF(s)."""
        identity_labels = self._vf_identity_labels(vf_labels)
        self.assertIsNotNone(
            identity_labels,
            '_assert_net_vf_metrics_match_metric_storage requires vf_labels '
            'to identify the VF under test')
        last_detail = ''
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            compute_output = self._metrics_text_from_hypervisor(hypervisor_ip)
            compute_samples = self._prom_samples(
                hypervisor_ip, metric_name, identity_labels,
                metrics_output=compute_output)
            storage_samples, query_error = self._metric_storage_samples(
                metric_name, hypervisor_ip=hypervisor_ip,
                required_labels=identity_labels)
            if not compute_samples:
                last_detail = (
                    'no compute samples for labels %s' % identity_labels)
            elif not storage_samples:
                last_detail = (
                    'no metric-storage samples for labels %s (%s)' % (
                        identity_labels, query_error))
            else:
                storage_by_key = {
                    self._net_vf_sample_key(sample['labels']): sample['value']
                    for sample in storage_samples}
                mismatches = []
                for sample in compute_samples:
                    key = self._net_vf_sample_key(sample['labels'])
                    compute_value = sample['value']
                    if key not in storage_by_key:
                        mismatches.append(
                            'VF %s on compute :9105 but missing in '
                            'metric-storage' % (key,))
                        continue
                    storage_value = storage_by_key[key]
                    if compute_value != storage_value:
                        mismatches.append(
                            'VF %s on %s: compute :9105=%s, '
                            'metric-storage=%s' % (
                                key, hypervisor_ip, compute_value,
                                storage_value))
                if not mismatches:
                    LOG.warning(
                        "%s on %s matches between compute :9105 and "
                        "metric-storage for labels %s",
                        metric_name, hypervisor_ip, identity_labels)
                    return
                last_detail = '\n'.join(mismatches)
            LOG.warning(
                "Attempt %s/%s waiting for %s metric-storage sync on %s: %s",
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                metric_name, hypervisor_ip, last_detail)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            "%s mismatch between compute and metric-storage on %s "
            "(labels=%s): %s" % (
                metric_name, hypervisor_ip, identity_labels, last_detail))

    def _vf_labels_from_mac(self, hypervisor_ip, mac_address):
        vf_ref = shell_utils.get_vf_from_mac(mac_address, hypervisor_ip)
        if not vf_ref or "_" not in vf_ref:
            raise unittest.SkipTest(
                "Could not map MAC %s to host VF on %s. "
                "Ensure direct SR-IOV ports are present." % (
                    mac_address, hypervisor_ip))
        device, vf = vf_ref.rsplit("_", 1)
        device = device.strip()
        vf = vf.strip()
        pci_cmd = (
            "basename \"$(readlink -f /sys/class/net/%s/device/virtfn%s)\" "
            "2>/dev/null" % (device, vf))
        pci_address = self._ssh_run_on_hypervisor(
            hypervisor_ip, pci_cmd).strip()
        if not pci_address:
            raise unittest.SkipTest(
                "Could not read VF PCI address for %s vf %s on %s" % (
                    device, vf, hypervisor_ip))
        numa_cmd = (
            "cat /sys/bus/pci/devices/%s/numa_node 2>/dev/null" % pci_address)
        numa_node = self._ssh_run_on_hypervisor(hypervisor_ip, numa_cmd).strip()
        return {
            "device": device,
            "vf": vf,
            "pci_address": pci_address,
            "numa_node": numa_node if numa_node else "-1",
        }

    def _counter_value(self, hypervisor_ip, metric_name, vf_labels):
        """Read one net_vf counter from compute :9105 for a single VF."""
        identity = self._vf_identity_labels(vf_labels)
        samples = self._prom_samples(
            hypervisor_ip, metric_name, required_labels=identity)
        if not samples:
            return None
        if len(samples) > 1:
            self.fail(
                '%s on %s matched multiple VF series for labels %s: %s' % (
                    metric_name, hypervisor_ip, identity,
                    [self._net_vf_sample_key(s['labels']) for s in samples]))
        return samples[0]['value']

    def _storage_counter_value(self, hypervisor_ip, metric_name, vf_labels):
        """Read one net_vf counter from metric-storage for a single VF."""
        identity = self._vf_identity_labels(vf_labels)
        samples, query_error = self._metric_storage_samples(
            metric_name, hypervisor_ip=hypervisor_ip,
            required_labels=identity)
        if not samples:
            LOG.warning(
                'No metric-storage sample for %s on %s labels %s: %s',
                metric_name, hypervisor_ip, identity, query_error)
            return None
        if len(samples) > 1:
            self.fail(
                '%s in metric-storage on %s matched multiple VF series for '
                'labels %s' % (metric_name, hypervisor_ip, identity))
        return samples[0]['value']

    def _vf_promql_filter(self, hypervisor_ip, vf_labels, metric_name):
        """Return a PromQL snippet for the VF under test (for failure logs)."""
        identity = self._vf_identity_labels(vf_labels)
        parts = ['instance="%s%s"' % (
            hypervisor_ip, metrics_base.NETWORK_EXPORTER_INSTANCE_PORT)]
        for key in ('device', 'vf', 'pci_address'):
            if key in identity:
                parts.append('%s="%s"' % (key, identity[key]))
        return '%s{%s}' % (metric_name, ','.join(parts))

    def _ssh_run_unchecked_on_hypervisor(self, hypervisor_ip, command):
        """Run on hypervisor without failing the test (cleanup helpers)."""
        try:
            return self._ssh_run_on_hypervisor(hypervisor_ip, command)
        except Exception as exc:
            LOG.warning(
                'Unchecked hypervisor command on %s failed: %s (%s)',
                hypervisor_ip, command, exc)
            return ''

    def _lookup_guest_dataplane_iface_by_mac(self, ssh_client, mac_address):
        """Return guest netdev for a MAC, or None if not found."""
        mac = mac_address.lower()
        cmd = (
            "ip -o link | grep -i '%s' | awk -F': ' '{print $2; exit}'" % mac)
        raw = ssh_client.exec_command(cmd).strip()
        iface = raw.split('@')[0].strip() if raw else ''
        return iface or None

    def _lookup_guest_dataplane_iface_by_ip(self, ssh_client, ip_address):
        """Return guest netdev carrying a fixed IP, or None if not found."""
        if ':' in ip_address:
            match = "inet6 %s/" % ip_address
        else:
            match = "inet %s/" % ip_address
        cmd = (
            "ip -o addr show | grep '%s' | awk '{print $2; exit}'" % match)
        raw = ssh_client.exec_command(cmd).strip()
        return raw.split('@')[0].strip() if raw else None

    def _guest_dataplane_iface(self, ssh_client, mac_address):
        """Return the guest netdev name for an SR-IOV port MAC address."""
        iface = self._lookup_guest_dataplane_iface_by_mac(
            ssh_client, mac_address)
        if not iface:
            raise unittest.SkipTest(
                'Could not resolve guest dataplane interface for MAC %s' % (
                    mac_address))
        return iface

    def _guest_dataplane_iface_for_port(self, ssh_client, port):
        """Locate SR-IOV dataplane netdev by port MAC, else by fixed IP."""
        iface = self._lookup_guest_dataplane_iface_by_mac(
            ssh_client, port['mac_address'])
        if iface:
            return iface
        fixed_ips = port.get('fixed_ips') or []
        if fixed_ips:
            iface = self._lookup_guest_dataplane_iface_by_ip(
                ssh_client, fixed_ips[0]['ip_address'])
            if iface:
                return iface
        raise unittest.SkipTest(
            'Could not resolve guest dataplane interface for port MAC %s '
            'and fixed IPs %s' % (
                port['mac_address'],
                [ip['ip_address'] for ip in fixed_ips]))

    @staticmethod
    def _is_zero_mac(mac_address):
        """Return True when a MAC is unset (all zeros)."""
        mac = (mac_address or '').lower().replace('-', ':')
        return mac in ('00:00:00:00:00:00',)

    def _read_guest_iface_mac(self, ssh_client, iface):
        """Return the MAC address from guest sysfs for one netdev."""
        guest_mac = ssh_client.exec_command(
            'cat /sys/class/net/%s/address' % iface).strip().lower()
        if not guest_mac:
            raise unittest.SkipTest(
                'Could not read MAC from guest interface %s' % iface)
        return guest_mac

    def _guest_dataplane_mac(self, ssh_client, mac_address):
        """Return the MAC address read from the guest SR-IOV netdev."""
        iface = self._guest_dataplane_iface(ssh_client, mac_address)
        guest_mac = self._read_guest_iface_mac(ssh_client, iface)
        return guest_mac, iface

    def _assert_guest_port_mac(self, ssh_client, port, server):
        """Verify the VM dataplane interface MAC matches the Neutron port."""
        expected_mac = port['mac_address'].lower()
        guest_mac, iface = self._guest_dataplane_mac(
            ssh_client, expected_mac)
        self.assertEqual(
            guest_mac, expected_mac,
            'Guest SR-IOV MAC mismatch on server %s interface %s: '
            'Neutron port=%s guest=%s' % (
                server['id'], iface, expected_mac, guest_mac))
        LOG.warning(
            'Guest SR-IOV MAC verified on server %s: interface=%s mac=%s',
            server['id'], iface, guest_mac)

    def _assert_guest_port_mac_not_zero(self, ssh_client, port, server):
        """Fail when the guest SR-IOV dataplane NIC MAC is all zeros."""
        iface = self._guest_dataplane_iface_for_port(ssh_client, port)
        guest_mac = self._read_guest_iface_mac(ssh_client, iface)
        self.assertFalse(
            self._is_zero_mac(guest_mac),
            'Guest SR-IOV interface %s on server %s has all-zero MAC %s '
            '(Neutron port MAC %s). The VF MAC was not programmed in the '
            'guest.' % (
                iface, server['id'], guest_mac, port['mac_address']))
        LOG.warning(
            'Guest SR-IOV MAC is non-zero on server %s: interface=%s mac=%s',
            server['id'], iface, guest_mac)

    @staticmethod
    def _parse_vf_sysfs_stats_blob(blob):
        """Parse mlx5-style VF stats file (``tx_dropped : 123`` per line)."""
        stats = {}
        for line in (blob or '').splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            try:
                stats[key] = int(value)
            except ValueError:
                continue
        return stats

    def _pf_netdevices_for_vf(self, hypervisor_ip, vf_labels):
        """Return PF netdev name(s) that host VF stats under .../sriov/<vf>/stats."""
        devices = []
        seen = set()

        def add(name):
            name = (name or '').strip()
            if name and name not in seen:
                seen.add(name)
                devices.append(name)

        add(vf_labels['device'])
        pci = vf_labels.get('pci_address')
        if not pci:
            return devices
        pf_net_cmd = (
            'bash -c %s' % shlex.quote(
                'pf=$(readlink -f /sys/bus/pci/devices/%s/physfn 2>/dev/null); '
                'if [ -n "$pf" ]; then ls "$pf/net" 2>/dev/null; fi' % pci))
        for line in self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip, pf_net_cmd).splitlines():
            add(line)

        pci_net_cmd = (
            'bash -c %s' % shlex.quote(
                'ls /sys/bus/pci/devices/%s/net 2>/dev/null' % pci))
        for line in self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip, pci_net_cmd).splitlines():
            add(line)
        return devices

    def _pf_netdev_for_vf_admin(self, hypervisor_ip, vf_labels):
        """Return PF netdev for ``ip link set dev <pf> vf <n> ...`` on the host."""
        pci = vf_labels.get('pci_address')
        if pci:
            pf_net_cmd = (
                'bash -c %s' % shlex.quote(
                    'pf=$(readlink -f /sys/bus/pci/devices/%s/physfn '
                    '2>/dev/null); '
                    'if [ -n "$pf" ]; then ls "$pf/net" 2>/dev/null | head -1; '
                    'fi' % pci))
            pf_netdev = self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip, pf_net_cmd).strip()
            if pf_netdev:
                return pf_netdev
        return vf_labels['device']

    def _vf_sysfs_stats_bases(self, hypervisor_ip, vf_labels):
        """Candidate sysfs paths for one VF stats file or directory."""
        vf = vf_labels['vf']
        bases = []
        seen = set()

        def add(path):
            if path not in seen:
                seen.add(path)
                bases.append(path)

        for pf_dev in self._pf_netdevices_for_vf(hypervisor_ip, vf_labels):
            add('/sys/class/net/%s/device/sriov/%s/stats' % (pf_dev, vf))
            add('/sys/class/net/%s/device/sriov/vf%s/stats' % (pf_dev, vf))

        ib_cmd = (
            'bash -c %s' % shlex.quote(
                'for ib in /sys/class/infiniband/*/device; do '
                'test -e "$ib/sriov/%s/stats" && echo "$ib/sriov/%s/stats"; '
                'done' % (vf, vf)))
        for line in self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip, ib_cmd).splitlines():
            add(line.strip())
        return bases

    def _vf_sysfs_stat_file_paths(self, hypervisor_ip, vf_labels, stat_name):
        """Per-stat sysfs files (Intel-style .../sriov/N/stats/rx_dropped)."""
        vf = vf_labels['vf']
        paths = []
        seen = set()
        for pf_dev in self._pf_netdevices_for_vf(hypervisor_ip, vf_labels):
            for path in (
                    '/sys/class/net/%s/device/sriov/%s/stats/%s' % (
                        pf_dev, vf, stat_name),
                    '/sys/class/net/%s/device/sriov/vf%s/stats/%s' % (
                        pf_dev, vf, stat_name)):
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths

    def _read_vf_stats_at_base(self, hypervisor_ip, stats_base):
        """Read VF stats from one sysfs file (mlx5 blob) or stats/ directory."""
        script = (
            'base=%s; '
            'if [ -f "$base" ]; then cat "$base"; '
            'elif [ -d "$base" ]; then '
            '  for f in "$base"/*; do '
            '    [ -f "$f" ] || continue; '
            '    n=$(basename "$f"); v=$(cat "$f" 2>/dev/null); '
            '    printf "%%s: %%s\\n" "$n" "$v"; '
            '  done; '
            'fi' % shlex.quote(stats_base))
        blob = self._ssh_run_unchecked_on_hypervisor(
            hypervisor_ip, 'bash -c %s' % shlex.quote(script)).strip()
        if not blob or 'No such file' in blob:
            return {}
        return self._parse_vf_sysfs_stats_blob(blob)

    def _host_vf_sysfs_stats_map(self, hypervisor_ip, vf_labels):
        """Return all VF stats from host sysfs for the PF/VF under test."""
        stats_bases = self._vf_sysfs_stats_bases(hypervisor_ip, vf_labels)
        for stats_base in stats_bases:
            stats = self._read_vf_stats_at_base(hypervisor_ip, stats_base)
            if stats:
                LOG.warning(
                    'Read VF sysfs stats from %s on %s for labels %s: %s',
                    stats_base, hypervisor_ip, vf_labels,
                    sorted(stats.keys()))
                return stats
        LOG.warning(
            'No VF sysfs stats found on %s for labels %s (tried: %s)',
            hypervisor_ip, vf_labels, stats_bases)
        return {}

    def _host_vf_sysfs_stat(self, hypervisor_ip, vf_labels, stat_name):
        """Read one VF counter from host sysfs (same source as net_vf exporter)."""
        for path in self._vf_sysfs_stat_file_paths(
                hypervisor_ip, vf_labels, stat_name):
            raw = self._ssh_run_unchecked_on_hypervisor(
                hypervisor_ip,
                'cat %s 2>/dev/null' % shlex.quote(path)).strip()
            if raw.isdigit():
                return int(raw)
        return self._host_vf_sysfs_stats_map(hypervisor_ip, vf_labels).get(
            stat_name)

    def _host_vf_sysfs_stats_available(self, hypervisor_ip, vf_labels):
        """Return sorted VF stat names exposed under host sysfs, or []."""
        return sorted(self._host_vf_sysfs_stats_map(hypervisor_ip, vf_labels))

    def _vf_prom_storage_values(self, hypervisor_ip, vf_labels, metric_name):
        """Read one net_vf counter from compute :9105 and metric-storage."""
        return (
            self._counter_value(hypervisor_ip, metric_name, vf_labels),
            self._storage_counter_value(hypervisor_ip, metric_name, vf_labels))

    def _wait_for_vf_prom_storage_aligned(
            self, hypervisor_ip, vf_labels, metric_name):
        """Wait until :9105 and metric-storage match before taking a baseline."""
        identity = self._vf_identity_labels(vf_labels)
        promql = self._vf_promql_filter(hypervisor_ip, vf_labels, metric_name)
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            prom, storage = self._vf_prom_storage_values(
                hypervisor_ip, vf_labels, metric_name)
            last = {'prom': prom, 'storage': storage}
            if prom is not None and storage is not None and prom == storage:
                LOG.warning(
                    ':9105 and metric-storage aligned for %s on %s: %s. %s',
                    metric_name, identity, prom, promql)
                return prom, storage
            LOG.warning(
                'Attempt %s/%s waiting for :9105==metric-storage baseline '
                'on %s for %s: %s',
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                identity, metric_name, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            '%s on %s did not align across compute :9105 and metric-storage '
            'before baseline for labels %s. Last %s. Per-VF PromQL: %s' % (
                metric_name, hypervisor_ip, identity, last, promql))

    def _wait_for_vf_prom_and_storage_increase(
            self, hypervisor_ip, vf_labels, metric_name, baseline_prom,
            counter_kind, min_delta=None, baseline_storage=None):
        """Wait for net_vf counter growth on :9105 and metric-storage."""
        if min_delta is None:
            min_delta = (self._min_expected_packets()
                         if counter_kind == 'packets'
                         else self._min_expected_bytes())
        if baseline_storage is None:
            baseline_storage = self._storage_counter_value(
                hypervisor_ip, metric_name, vf_labels)
        identity = self._vf_identity_labels(vf_labels)
        promql = self._vf_promql_filter(hypervisor_ip, vf_labels, metric_name)
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            prom = self._counter_value(
                hypervisor_ip, metric_name, vf_labels)
            storage = self._storage_counter_value(
                hypervisor_ip, metric_name, vf_labels)
            prom_delta = (
                None if prom is None or baseline_prom is None
                else prom - baseline_prom)
            storage_delta = (
                None if storage is None or baseline_storage is None
                else storage - baseline_storage)
            last = {
                'baseline_prom': baseline_prom,
                'baseline_storage': baseline_storage,
                'prom': prom,
                'storage': storage,
                'prom_delta': prom_delta,
                'storage_delta': storage_delta,
                'min_delta': min_delta,
            }
            if (prom is not None and storage is not None and
                    prom_delta is not None and storage_delta is not None and
                    prom_delta >= min_delta and
                    storage_delta >= min_delta and
                    prom == storage):
                LOG.warning(
                    '%s on %s increased on compute and metric-storage for %s: '
                    'value=%s (baseline prom=%s storage=%s, delta=%s). '
                    'Dashboard filter: %s',
                    metric_name, hypervisor_ip, identity, prom,
                    baseline_prom, baseline_storage, prom_delta, promql)
                return prom_delta
            LOG.warning(
                'Attempt %s/%s waiting for %s on %s (need prom/storage '
                'delta>=%s and prom==storage): %s',
                attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                metric_name, identity, min_delta, last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            '%s on %s did not increase on both compute :9105 and '
            'metric-storage for labels %s. Required delta>=%s and '
            'prom==storage. Last %s. Per-VF PromQL: %s' % (
                metric_name, hypervisor_ip, identity, min_delta, last, promql))

    def _wait_for_vf_counter_aligned_with_sysfs(
            self, hypervisor_ip, vf_labels, metric_name, sysfs_stat_name,
            baseline_prom, baseline_sysfs, min_prom_delta, min_sysfs_delta):
        """Wait until :9105, metric-storage, and sysfs all match for one VF."""
        identity = self._vf_identity_labels(vf_labels)
        promql = self._vf_promql_filter(hypervisor_ip, vf_labels, metric_name)
        last = {}
        for attempt in range(metrics_base.METRIC_RETRY_ATTEMPTS):
            sysfs_now = self._host_vf_sysfs_stat(
                hypervisor_ip, vf_labels, sysfs_stat_name)
            prom = self._counter_value(
                hypervisor_ip, metric_name, vf_labels)
            storage = self._storage_counter_value(
                hypervisor_ip, metric_name, vf_labels)
            sysfs_delta = (
                None if sysfs_now is None or baseline_sysfs is None
                else sysfs_now - baseline_sysfs)
            prom_delta = (
                None if prom is None or baseline_prom is None
                else prom - baseline_prom)
            last = {
                'baseline_prom': baseline_prom,
                'baseline_sysfs': baseline_sysfs,
                'sysfs_now': sysfs_now,
                'sysfs_delta': sysfs_delta,
                'prom': prom,
                'prom_delta': prom_delta,
                'storage': storage,
                'min_prom_delta': min_prom_delta,
                'min_sysfs_delta': min_sysfs_delta,
            }
            if None in (sysfs_now, prom, storage, sysfs_delta, prom_delta):
                LOG.warning(
                    'Attempt %s/%s waiting for %s alignment on %s: %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    metric_name, identity, last)
            elif (sysfs_delta >= min_sysfs_delta and
                  prom_delta >= min_prom_delta and
                  prom == storage == sysfs_now):
                LOG.warning(
                    '%s aligned on %s for %s: sysfs/prom/storage=%s '
                    '(baseline prom=%s sysfs=%s, deltas prom=%s sysfs=%s). '
                    'Dashboard filter: %s',
                    metric_name, hypervisor_ip, identity, prom,
                    baseline_prom, baseline_sysfs, prom_delta, sysfs_delta,
                    promql)
                return prom, sysfs_now
            else:
                LOG.warning(
                    'Attempt %s/%s %s not aligned on %s (need sysfs_delta>=%s '
                    'prom_delta>=%s and prom==storage==sysfs): %s',
                    attempt + 1, metrics_base.METRIC_RETRY_ATTEMPTS,
                    metric_name, identity, min_sysfs_delta, min_prom_delta,
                    last)
            if attempt < metrics_base.METRIC_RETRY_ATTEMPTS - 1:
                time.sleep(metrics_base.METRIC_RETRY_INTERVAL)
        self.fail(
            '%s on %s did not align across sysfs, compute :9105, and '
            'metric-storage for labels %s. Required sysfs_delta>=%s '
            'prom_delta>=%s and prom==storage==sysfs. Last %s. '
            'Per-VF PromQL (not sum across all VFs): %s' % (
                metric_name, hypervisor_ip, identity, min_sysfs_delta,
                min_prom_delta, last, promql))

    def _test_vf_drop_counter_increases(
            self, metric_name, endpoint_role, traffic_generator,
            sysfs_stat_name):
        """Validate net_vf_*_dropped_total increases after induced drops."""
        self._assert_net_vf_metric_reported(metric_name)
        ctx = self._build_traffic_context()
        endpoint = ctx[endpoint_role]
        hypervisor_ip = endpoint['hypervisor_ip']
        vf_labels = endpoint['vf_labels']
        baseline_sysfs = self._host_vf_sysfs_stat(
            hypervisor_ip, vf_labels, sysfs_stat_name)
        baseline_prom, baseline_storage = (
            self._wait_for_vf_prom_storage_aligned(
                hypervisor_ip, vf_labels, metric_name))
        self.assertIsNotNone(
            baseline_prom,
            '%s missing baseline on %s for labels %s' % (
                metric_name, hypervisor_ip, vf_labels))

        packet_count = self._ping_count()
        min_packets = self._min_expected_packets()
        traffic_generator(ctx, packet_count, min_packets)

        sysfs_before = ctx.get('sysfs_drop_before')
        sysfs_after = ctx.get('sysfs_drop_after')
        promql = self._vf_promql_filter(hypervisor_ip, vf_labels, metric_name)

        if (sysfs_before is not None and sysfs_after is not None and
                sysfs_after > sysfs_before):
            sysfs_delta = sysfs_after - sysfs_before
            baseline_sysfs_val = (
                baseline_sysfs if baseline_sysfs is not None else sysfs_before)
            final_prom, final_sysfs = (
                self._wait_for_vf_counter_aligned_with_sysfs(
                    hypervisor_ip, vf_labels, metric_name, sysfs_stat_name,
                    baseline_prom, baseline_sysfs_val, sysfs_delta,
                    sysfs_delta))
            LOG.warning(
                '%s validated with sysfs on %s for %s: prom/sysfs=%s '
                '(baseline prom=%s sysfs=%s, induced %s->%s). %s',
                metric_name, endpoint_role, hypervisor_ip, final_prom,
                baseline_prom, baseline_sysfs_val, sysfs_before, final_sysfs,
                promql)
            return

        if (sysfs_before is not None and sysfs_after is not None and
                sysfs_after <= sysfs_before):
            raise unittest.SkipTest(
                'Host VF sysfs %s on %s unchanged after drop induce '
                '(before=%s after=%s). The NIC/driver may not increment VF '
                'drop stats for this test method.' % (
                    sysfs_stat_name, hypervisor_ip, sysfs_before,
                    sysfs_after))

        LOG.warning(
            'Host VF sysfs %s unavailable around drop induce on %s for '
            'labels %s (baseline=%s before=%r after=%r, available=%s); '
            'validating %s via Prometheus delta and metric-storage. %s',
            sysfs_stat_name, hypervisor_ip, vf_labels, baseline_sysfs,
            sysfs_before, sysfs_after,
            self._host_vf_sysfs_stats_available(hypervisor_ip, vf_labels) or
            'none', metric_name, promql)
        self._wait_for_vf_prom_and_storage_increase(
            hypervisor_ip, vf_labels, metric_name, baseline_prom,
            'drops', min_delta=1, baseline_storage=baseline_storage)

    def _test_vf_counter_increases_with_traffic(
            self, metric_name, endpoint_role, counter_kind,
            traffic_generator=None, traffic_packet_count=None):
        """Validate one net_vf counter increases under VM dataplane traffic."""
        self._assert_net_vf_metric_reported(metric_name)
        ctx = self._build_traffic_context()
        endpoint = ctx[endpoint_role]
        hypervisor_ip = endpoint['hypervisor_ip']
        vf_labels = endpoint['vf_labels']
        sysfs_stat_name = metrics_base.NET_VF_METRIC_TO_SYSFS_STAT.get(
            metric_name)
        baseline_sysfs = None
        use_sysfs = False
        if sysfs_stat_name:
            baseline_sysfs = self._host_vf_sysfs_stat(
                hypervisor_ip, vf_labels, sysfs_stat_name)
            if baseline_sysfs is not None:
                use_sysfs = True
            else:
                available = self._host_vf_sysfs_stats_available(
                    hypervisor_ip, vf_labels)
                LOG.warning(
                    'Host VF sysfs %s unavailable on %s for labels %s '
                    '(available: %s); validating %s via Prometheus delta '
                    'and metric-storage only. Per-VF query: %s',
                    sysfs_stat_name, hypervisor_ip, vf_labels,
                    available or 'none', metric_name,
                    self._vf_promql_filter(hypervisor_ip, vf_labels,
                                           metric_name))
        baseline_prom, baseline_storage = (
            self._wait_for_vf_prom_storage_aligned(
                hypervisor_ip, vf_labels, metric_name))
        self.assertIsNotNone(
            baseline_prom,
            '%s missing baseline on %s for labels %s' % (
                metric_name, hypervisor_ip, vf_labels))

        packet_count = traffic_packet_count or self._ping_count()
        min_packets = self._min_expected_packets_for_count(packet_count)
        if counter_kind == 'packets':
            min_delta = min_packets
        else:
            min_delta = self._min_expected_bytes_for_count(packet_count)
        if traffic_generator is None:
            self._send_ping_packets(
                ctx['ssh_sender'], ctx['peer_ip'], packet_count, min_packets)
        else:
            traffic_generator(ctx, packet_count, min_packets)

        if use_sysfs:
            self._wait_for_vf_counter_aligned_with_sysfs(
                hypervisor_ip, vf_labels, metric_name, sysfs_stat_name,
                baseline_prom, baseline_sysfs, min_delta, min_delta)
            LOG.warning(
                '%s increased with sysfs alignment after SR-IOV traffic '
                '(%s %s)', metric_name, endpoint_role, counter_kind)
        else:
            self._wait_for_vf_prom_and_storage_increase(
                hypervisor_ip, vf_labels, metric_name, baseline_prom,
                counter_kind, min_delta=min_delta,
                baseline_storage=baseline_storage)
            LOG.warning(
                '%s increased on Prometheus paths after SR-IOV traffic '
                '(%s %s)', metric_name, endpoint_role, counter_kind)
