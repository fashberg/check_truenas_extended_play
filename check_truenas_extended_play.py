#!/usr/bin/env python3

# The MIT License (MIT)
# Copyright (c) 2015 Goran Tornqvist
# Extended by Stewart Loving-Gibbard 2020, 2021, 2022, 2023
# Additional help from Folke Ashberg 2021, 2026
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import sys

MIN_PYTHON = (3, 7)
if sys.version_info < MIN_PYTHON:
    sys.exit("Python %s.%s or later is required.\n" % MIN_PYTHON)

import argparse
import asyncio
import json
import logging
import ssl
from dataclasses import dataclass

import websockets


@dataclass
class ZpoolCapacity:
    ZpoolName: str
    ZpoolAvailableBytes: int
    TotalUsedBytesForAllDatasets: int


class Startup(object):

    def __init__(self, hostname, user, secret, use_ssl, verify_cert, ignore_dismissed_alerts,
                 debug_logging, zpool_name, zpool_warn, zpool_critical, show_zpool_perfdata,
                 cpu_warn, cpu_critical, mem_warn, mem_critical, net_warn, net_critical):
        self._hostname = hostname
        self._user = user
        self._secret = secret
        self._use_ssl = use_ssl
        self._verify_cert = verify_cert
        self._ignore_dismissed_alerts = ignore_dismissed_alerts
        self._debug_logging = debug_logging
        self._zpool_name = zpool_name
        self._wfree = zpool_warn
        self._cfree = zpool_critical
        self._show_zpool_perfdata = show_zpool_perfdata
        self._cpu_warn = cpu_warn
        self._cpu_critical = cpu_critical
        self._mem_warn = mem_warn
        self._mem_critical = mem_critical
        self._net_warn = net_warn
        self._net_critical = net_critical

        scheme = 'wss' if use_ssl else 'ws'
        self._ws_url = '%s://%s/api/current' % (scheme, hostname)

        self._loop = asyncio.new_event_loop()
        self._ws = None
        self._call_id = 0

        self.setup_logging()
        self.log_startup_information()

    def log_startup_information(self):
        logging.debug('hostname: %s', self._hostname)
        logging.debug('ws_url: %s', self._ws_url)
        logging.debug('verify_cert: %s', self._verify_cert)
        logging.debug('zpool_name: %s', self._zpool_name)
        logging.debug('wfree: %d  cfree: %d', self._wfree, self._cfree)

    # -------------------------------------------------------------------------
    # WebSocket transport
    # -------------------------------------------------------------------------

    def connect(self):
        self._loop.run_until_complete(self._async_connect())

    def disconnect(self):
        self._loop.run_until_complete(self._async_disconnect())

    async def _async_connect(self):
        ssl_ctx = None
        if self._use_ssl:
            ssl_ctx = ssl.create_default_context()
            if not self._verify_cert:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            self._ws = await websockets.connect(self._ws_url, ssl=ssl_ctx)
        except Exception:
            print('UNKNOWN - Could not connect to TrueNAS WebSocket: ' + str(sys.exc_info()))
            sys.exit(3)

        # Authenticate
        if self._user:
            auth_method = 'auth.login'
            auth_params = [self._user, self._secret]
        else:
            auth_method = 'auth.login_with_api_key'
            auth_params = [self._secret]

        resp = await self._async_send_recv(auth_method, auth_params)
        if resp.get('error') or not resp.get('result'):
            print('UNKNOWN - Authentication failed: ' + str(resp.get('error', 'no result')))
            sys.exit(3)
        logging.debug('Authenticated via %s', auth_method)

    async def _async_disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _async_send_recv(self, method, params):
        self._call_id += 1
        cid = str(self._call_id)
        msg = {'jsonrpc': '2.0', 'id': cid, 'method': method, 'params': params}
        logging.debug('WS call: %s %s', method, params)
        await self._ws.send(json.dumps(msg))
        while True:
            raw = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=30))
            if raw.get('id') == cid:
                logging.debug('WS response: %s', str(raw)[:200])
                return raw

    def call(self, method, params=None):
        try:
            resp = self._loop.run_until_complete(
                self._async_send_recv(method, params if params is not None else [])
            )
        except Exception:
            print('UNKNOWN - request failed - Error when contacting TrueNAS server: ' + str(sys.exc_info()))
            sys.exit(3)

        if 'error' in resp:
            err = resp['error']
            data = err.get('data', {}) or {}
            reason = data.get('reason') or err.get('message', str(err))
            print('UNKNOWN - API error calling %s: %s' % (method, reason))
            sys.exit(3)

        return resp.get('result')

    def reporting_call(self, graph_name, identifier=None):
        graph = {'name': graph_name}
        if identifier:
            graph['identifier'] = identifier
        return self.call('reporting.netdata_get_data', [[graph], {'unit': 'HOUR', 'page': 1}])

    # -------------------------------------------------------------------------
    # Checks
    # -------------------------------------------------------------------------

    def check_repl(self):
        repls = self.call('replication.query')
        errors = 0
        msg = ''
        replications_examined = ''

        try:
            for repl in repls:
                repl_name = repl['name']
                repl_state_obj = repl['state']
                repl_state_code = repl_state_obj['state']
                replications_examined += ' ' + repl_name + ': ' + repl_state_code
                if repl_state_code != 'FINISHED' and repl_state_code != 'RUNNING':
                    errors += 1
                    msg += repl_name + ': ' + repl_state_code
        except:
            print('UNKNOWN - check_repl() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if errors > 0:
            print('WARNING - There are ' + str(errors) + ' replication errors [' + msg.strip() +
                  ']. Go to Storage > Replication Tasks > View Replication Tasks in TrueNAS for more details.')
            sys.exit(1)
        else:
            print('OK - No replication errors. Replications examined: ' + replications_examined)
            sys.exit(0)

    def check_update(self):
        # TrueNAS SCALE 25.x API: update.status replaces update.check_available
        # Returns {code: 'NORMAL'|'ERROR', status: {current_version, new_version}, error}
        result = self.call('update.status')

        try:
            logging.debug('Update check result: %s', result)
            code = result['code']

            if code == 'ERROR':
                error_info = result.get('error') or {}
                reason = error_info.get('reason', 'unknown error')
                print('UNKNOWN - check_update() - TrueNAS update status error: ' + reason)
                sys.exit(3)

            status = result.get('status') or {}
            new_version = status.get('new_version')
            current_version = status.get('current_version', {})
            current_train = current_version.get('train', 'unknown')

        except:
            print('UNKNOWN - check_update() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if new_version:
            version_str = new_version.get('version', 'unknown')
            print('WARNING - Update available: ' + version_str + ' (train: ' + current_train +
                  '). Go to TrueNAS Dashboard -> System -> Update to check for newer version.')
            sys.exit(1)
        else:
            print('OK - No update available (train: ' + current_train + ')')
            sys.exit(0)

    def check_alerts(self):
        alerts = self.call('alert.list')
        warn = 0
        crit = 0
        critical_messages = ''
        warning_messages = ''

        try:
            for alert in alerts:
                if self._ignore_dismissed_alerts and alert['dismissed']:
                    continue
                if alert['level'] == 'CRITICAL':
                    crit += 1
                    critical_messages += '- (C) ' + alert['formatted'].replace('\n', '. ') + ' '
                elif alert['level'] == 'WARNING':
                    warn += 1
                    warning_messages += '- (W) ' + alert['formatted'].replace('\n', '. ') + ' '
        except:
            print('UNKNOWN - check_alerts() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if crit > 0:
            print('CRITICAL ' + critical_messages + warning_messages)
            sys.exit(2)
        elif warn > 0:
            print('WARNING ' + warning_messages)
            sys.exit(1)
        else:
            print('OK - No problem alerts')
            sys.exit(0)

    def check_zpool(self):
        pool_results = self.call('pool.query')
        warn = 0
        crit = 0
        critical_messages = ''
        warning_messages = ''
        zpools_examined = ''
        actual_zpool_count = 0
        all_pool_names = ''
        looking_for_all_pools = self._zpool_name.lower() == 'all'

        try:
            for pool in pool_results:
                actual_zpool_count += 1
                pool_name = pool['name']
                pool_status = pool['status']
                all_pool_names += pool_name + ' '

                if looking_for_all_pools or self._zpool_name == pool_name:
                    zpools_examined += ' ' + pool_name
                    if pool_status != 'ONLINE':
                        crit += 1
                        critical_messages += '- (C) ZPool ' + pool_name + 'is ' + pool_status
        except:
            print('UNKNOWN - check_zpool() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if zpools_examined == '' and actual_zpool_count == 0 and looking_for_all_pools:
            zpools_examined = '(None - No Zpools found)'
        if zpools_examined == '' and actual_zpool_count > 0 and not looking_for_all_pools and crit == 0:
            crit += 1
            critical_messages = '- No Zpools found matching {} out of {} pools ({})'.format(
                self._zpool_name, actual_zpool_count, all_pool_names)

        if crit > 0:
            print('CRITICAL ' + critical_messages + warning_messages)
            sys.exit(2)
        elif warn > 0:
            print('WARNING ' + warning_messages)
            sys.exit(1)
        else:
            print('OK - No problem Zpools. Zpools examined: ' + zpools_examined)
            sys.exit(0)

    def check_zpool_capacity(self):
        BYTES_IN_MEGABYTE = 1024 * 1024

        warnZpoolCapacityPercent = self._wfree
        critZpoolCapacityPercent = self._cfree

        # flat=False returns only root-level datasets (one per pool)
        dataset_results = self.call('pool.dataset.query', [[], {'extra': {'flat': False}}])

        warn = 0
        crit = 0
        critical_messages = ''
        warning_messages = ''
        zpools_examined_with_no_issues = ''
        root_level_datasets_examined = ''
        root_level_dataset_count = 0
        all_root_level_dataset_names = ''
        perfdata = ''
        if self._show_zpool_perfdata:
            perfdata = ';|'

        looking_for_all_pools = self._zpool_name.lower() == 'all'
        zpoolNameToCapacityDict = {}

        try:
            for dataset in dataset_results:
                root_level_dataset_count += 1
                dataset_name = dataset['name']
                dataset_pool_name = dataset['pool']
                all_root_level_dataset_names += dataset_name + ' '

                if looking_for_all_pools or self._zpool_name == dataset_pool_name:
                    root_level_datasets_examined += ' ' + dataset_name
                    dataset_used_bytes = dataset['used']['parsed']
                    dataset_available_bytes = dataset['available']['parsed']

                    if dataset_pool_name not in zpoolNameToCapacityDict:
                        zpoolNameToCapacityDict[dataset_pool_name] = ZpoolCapacity(
                            dataset_pool_name, dataset_available_bytes, dataset_used_bytes)
                    else:
                        zpoolNameToCapacityDict[dataset_pool_name].TotalUsedBytesForAllDatasets += dataset_used_bytes

            for currentZpoolCapacity in zpoolNameToCapacityDict.values():
                zpoolTotalBytes = currentZpoolCapacity.ZpoolAvailableBytes + currentZpoolCapacity.TotalUsedBytesForAllDatasets
                usedPercentage = (currentZpoolCapacity.TotalUsedBytesForAllDatasets / zpoolTotalBytes) * 100
                usagePercentDisplayString = f'{usedPercentage:3.1f}'

                if usedPercentage >= critZpoolCapacityPercent:
                    crit += 1
                    critical_messages += ' - Pool ' + currentZpoolCapacity.ZpoolName + ' usage ' + usagePercentDisplayString + '% exceeds critical value of ' + str(critZpoolCapacityPercent) + '%'
                elif usedPercentage >= warnZpoolCapacityPercent:
                    warn += 1
                    warning_messages += ' - Pool ' + currentZpoolCapacity.ZpoolName + ' usage ' + usagePercentDisplayString + '% exceeds warning value of ' + str(warnZpoolCapacityPercent) + '%'
                else:
                    if len(zpools_examined_with_no_issues) > 0:
                        zpools_examined_with_no_issues += ' - '
                    zpools_examined_with_no_issues += currentZpoolCapacity.ZpoolName + ' (' + usagePercentDisplayString + '% used)'

                if self._show_zpool_perfdata:
                    usedMegaBytes = currentZpoolCapacity.TotalUsedBytesForAllDatasets / BYTES_IN_MEGABYTE
                    warningMegabytes = zpoolTotalBytes * (warnZpoolCapacityPercent / 100) / BYTES_IN_MEGABYTE
                    criticalMegabytes = zpoolTotalBytes * (critZpoolCapacityPercent / 100) / BYTES_IN_MEGABYTE
                    totalMegabytes = zpoolTotalBytes / BYTES_IN_MEGABYTE
                    perfdata += ' %s=%.2fMB;%.2f;%.2f;0;%.2f' % (
                        currentZpoolCapacity.ZpoolName, usedMegaBytes,
                        warningMegabytes, criticalMegabytes, totalMegabytes)

        except:
            print('UNKNOWN - check_zpool_capacity() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if root_level_datasets_examined == '' and root_level_dataset_count == 0 and looking_for_all_pools:
            root_level_datasets_examined = '(No Datasets found)'
        if root_level_datasets_examined == '' and root_level_dataset_count > 0 and not looking_for_all_pools and crit == 0:
            crit += 1
            critical_messages = '- No datasets found matching ZPool {} out of {} root level datasets ({})'.format(
                self._zpool_name, root_level_dataset_count, all_root_level_dataset_names)

        error_or_warning_dividing_dash = ' - ' if zpools_examined_with_no_issues else ''

        if crit > 0:
            print('CRITICAL' + critical_messages + warning_messages + error_or_warning_dividing_dash + zpools_examined_with_no_issues + perfdata)
            sys.exit(2)
        elif warn > 0:
            print('WARNING' + warning_messages + error_or_warning_dividing_dash + zpools_examined_with_no_issues + perfdata)
            sys.exit(1)
        else:
            print('OK - No Zpool capacity issues. ZPools examined: ' + zpools_examined_with_no_issues +
                  ' - Root level datasets examined:' + root_level_datasets_examined + perfdata)
            sys.exit(0)

    def check_datasets(self):
        BYTES_IN_MEGABYTE = 1024 * 1024
        warnPercent = self._wfree
        critPercent = self._cfree

        dataset_results = self.call('pool.dataset.query')

        crit = 0
        warn = 0
        critical_messages = ''
        warning_messages = ''
        ok_datasets = ''
        perfdata = ''
        seen = set()

        try:
            def check_ds(ds):
                nonlocal crit, warn, critical_messages, warning_messages, ok_datasets, perfdata

                name = ds['name']
                if name in seen:
                    return
                seen.add(name)

                locked = ds.get('locked', False)
                if locked:
                    crit += 1
                    critical_messages += '- (C) ' + name + ': LOCKED '
                    for child in ds.get('children', []):
                        check_ds(child)
                    return

                used_bytes = (ds.get('used') or {}).get('parsed', 0) or 0
                avail_bytes = (ds.get('available') or {}).get('parsed', 0) or 0
                quota_bytes = (ds.get('quota') or {}).get('parsed', None)
                is_root = (ds['pool'] == ds['name'])

                if quota_bytes:
                    total_bytes = quota_bytes
                elif is_root:
                    total_bytes = used_bytes + avail_bytes
                else:
                    total_bytes = None

                if total_bytes and total_bytes > 0:
                    used_pct = (used_bytes / total_bytes) * 100
                    used_pct_str = f'{used_pct:3.1f}'
                    capacity_source = 'quota' if quota_bytes else 'pool'

                    if used_pct >= critPercent:
                        crit += 1
                        critical_messages += '- (C) ' + name + ' ' + used_pct_str + '% used (' + capacity_source + ') '
                    elif used_pct >= warnPercent:
                        warn += 1
                        warning_messages += '- (W) ' + name + ' ' + used_pct_str + '% used (' + capacity_source + ') '
                    else:
                        ok_datasets += name + ' (' + used_pct_str + '%) '

                    if self._show_zpool_perfdata:
                        used_mb = used_bytes / BYTES_IN_MEGABYTE
                        warn_mb = total_bytes * (warnPercent / 100) / BYTES_IN_MEGABYTE
                        crit_mb = total_bytes * (critPercent / 100) / BYTES_IN_MEGABYTE
                        total_mb = total_bytes / BYTES_IN_MEGABYTE
                        safe_name = name.replace('/', '_').replace(' ', '_')
                        perfdata += f' {safe_name}={used_mb:.2f}MB;{warn_mb:.2f};{crit_mb:.2f};0;{total_mb:.2f}'
                else:
                    ok_datasets += name + ' '

                for child in ds.get('children', []):
                    check_ds(child)

            for ds in dataset_results:
                check_ds(ds)

        except:
            print('UNKNOWN - check_datasets() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if self._show_zpool_perfdata and perfdata:
            perfdata = ';|' + perfdata

        if crit > 0:
            print('CRITICAL ' + critical_messages + warning_messages + ('- OK: ' + ok_datasets.strip() if ok_datasets.strip() else '') + perfdata)
            sys.exit(2)
        elif warn > 0:
            print('WARNING ' + warning_messages + ('- OK: ' + ok_datasets.strip() if ok_datasets.strip() else '') + perfdata)
            sys.exit(1)
        else:
            print('OK - All datasets OK: ' + ok_datasets.strip() + perfdata)
            sys.exit(0)

    def check_apps(self):
        apps = self.call('app.query')
        crit = 0
        warn = 0
        critical_messages = ''
        warning_messages = ''
        ok_apps = ''

        try:
            if not apps:
                print('OK - No apps installed')
                sys.exit(0)

            for app in apps:
                name = app['name']
                state = app['state']
                if state == 'CRASHED':
                    crit += 1
                    critical_messages += '- (C) ' + name + ': ' + state + ' '
                elif state == 'STOPPED':
                    warn += 1
                    warning_messages += '- (W) ' + name + ': ' + state + ' '
                else:
                    ok_apps += name + ' (' + state + ') '
        except:
            print('UNKNOWN - check_apps() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if crit > 0:
            print('CRITICAL ' + critical_messages + warning_messages + ('- OK: ' + ok_apps if ok_apps else ''))
            sys.exit(2)
        elif warn > 0:
            print('WARNING ' + warning_messages + ('- OK: ' + ok_apps if ok_apps else ''))
            sys.exit(1)
        else:
            print('OK - All apps running: ' + ok_apps.strip())
            sys.exit(0)

    def check_sys_cpu(self):
        result = self.reporting_call('cpu')

        try:
            graph = result[0]
            aggs = graph['aggregations']
            mean_cpu = aggs['mean']['cpu']
            max_cpu = aggs['max']['cpu']
            cores = [l for l in graph['legend'] if l not in ('time', 'cpu')]

            perfdata = ''
            if self._show_zpool_perfdata:
                perfdata = f';| cpu={mean_cpu:.1f}%;{self._cpu_warn};{self._cpu_critical};0;100'
                for core in cores:
                    core_mean = aggs['mean'].get(core, 0)
                    perfdata += f' {core}={core_mean:.1f}%;{self._cpu_warn};{self._cpu_critical};0;100'
        except:
            print('UNKNOWN - check_sys_cpu() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        msg = f'CPU usage: {mean_cpu:.1f}% avg/1h, max: {max_cpu:.1f}%'
        if mean_cpu >= self._cpu_critical:
            print(f'CRITICAL - {msg}{perfdata}')
            sys.exit(2)
        elif mean_cpu >= self._cpu_warn:
            print(f'WARNING - {msg}{perfdata}')
            sys.exit(1)
        else:
            print(f'OK - {msg}{perfdata}')
            sys.exit(0)

    def check_sys_memory(self):
        BYTES_IN_MB = 1024 * 1024
        result = self.reporting_call('memory')

        try:
            graph = result[0]
            aggs = graph['aggregations']
            avail_bytes = aggs['mean']['available']
            avail_mb = avail_bytes / BYTES_IN_MB

            sys_info = self.call('system.info')
            total_mb = sys_info['physmem'] / BYTES_IN_MB

            used_mb = total_mb - avail_mb
            used_pct = (used_mb / total_mb) * 100

            perfdata = ''
            if self._show_zpool_perfdata:
                warn_mb = total_mb * (self._mem_warn / 100)
                crit_mb = total_mb * (self._mem_critical / 100)
                perfdata = f';| memory={used_mb:.0f}MB;{warn_mb:.0f};{crit_mb:.0f};0;{total_mb:.0f}'
        except:
            print('UNKNOWN - check_sys_memory() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        msg = f'Memory usage: {used_pct:.1f}% ({used_mb:.0f}MB of {total_mb:.0f}MB used)'
        if used_pct >= self._mem_critical:
            print(f'CRITICAL - {msg}{perfdata}')
            sys.exit(2)
        elif used_pct >= self._mem_warn:
            print(f'WARNING - {msg}{perfdata}')
            sys.exit(1)
        else:
            print(f'OK - {msg}{perfdata}')
            sys.exit(0)

    def check_sys_network(self):
        graphs_info = self.call('reporting.netdata_graphs')
        iface_graph = next((g for g in graphs_info if g['name'] == 'interface'), None)

        if not iface_graph or not iface_graph.get('identifiers'):
            print('OK - No network interfaces found in reporting')
            sys.exit(0)

        identifiers = iface_graph['identifiers']
        crit = 0
        warn = 0
        critical_messages = ''
        warning_messages = ''
        ok_ifaces = ''
        perfdata = ''

        try:
            for iface in identifiers:
                result = self.reporting_call('interface', iface)
                graph = result[0]
                aggs = graph['aggregations']
                recv_kbits = aggs['mean']['received']
                sent_kbits = aggs['mean']['sent']
                max_recv = aggs['max']['received']
                max_sent = aggs['max']['sent']

                if self._show_zpool_perfdata:
                    w = self._net_warn if self._net_warn > 0 else ''
                    c = self._net_critical if self._net_critical > 0 else ''
                    perfdata += f' {iface}_recv={recv_kbits:.1f}Kbs;{w};{c};0;'
                    perfdata += f' {iface}_sent={sent_kbits:.1f}Kbs;{w};{c};0;'

                threshold_exceeded = self._net_warn > 0 and (recv_kbits > self._net_critical or sent_kbits > self._net_critical)
                threshold_warned = self._net_warn > 0 and (recv_kbits > self._net_warn or sent_kbits > self._net_warn)
                iface_info = f'{iface}: rx={recv_kbits:.1f} tx={sent_kbits:.1f} Kbit/s (max rx={max_recv:.1f} tx={max_sent:.1f})'

                if threshold_exceeded:
                    crit += 1
                    critical_messages += f'- (C) {iface_info} '
                elif threshold_warned:
                    warn += 1
                    warning_messages += f'- (W) {iface_info} '
                else:
                    ok_ifaces += iface_info + ' '
        except:
            print('UNKNOWN - check_sys_network() - Error: ' + str(sys.exc_info()))
            sys.exit(3)

        if self._show_zpool_perfdata and perfdata:
            perfdata = ';|' + perfdata

        if crit > 0:
            print('CRITICAL ' + critical_messages + warning_messages + ('- OK: ' + ok_ifaces.strip() if ok_ifaces.strip() else '') + perfdata)
            sys.exit(2)
        elif warn > 0:
            print('WARNING ' + warning_messages + ('- OK: ' + ok_ifaces.strip() if ok_ifaces.strip() else '') + perfdata)
            sys.exit(1)
        else:
            print('OK - ' + ok_ifaces.strip() + perfdata)
            sys.exit(0)

    def handle_requested_alert_type(self, alert_type):
        if alert_type == 'alerts':
            self.check_alerts()
        elif alert_type == 'apps':
            self.check_apps()
        elif alert_type == 'datasets':
            self.check_datasets()
        elif alert_type == 'repl':
            self.check_repl()
        elif alert_type == 'update':
            self.check_update()
        elif alert_type == 'zpool':
            self.check_zpool()
        elif alert_type == 'zpool_capacity':
            self.check_zpool_capacity()
        elif alert_type == 'sys_cpu':
            self.check_sys_cpu()
        elif alert_type == 'sys_memory':
            self.check_sys_memory()
        elif alert_type == 'sys_network':
            self.check_sys_network()
        else:
            print('Unknown type: ' + alert_type)
            sys.exit(3)

    def setup_logging(self):
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG if self._debug_logging else logging.CRITICAL)


check_truenas_script_version = '2.0'

default_zpool_warning_percent = 80
default_zool_critical_percent = 90


def main():
    parser = argparse.ArgumentParser(
        description='Checks a TrueNAS server using the WebSocket/JSON-RPC API. Version ' + check_truenas_script_version)
    parser.add_argument('-H', '--hostname', required=True, type=str, help='Hostname or IP address')
    parser.add_argument('-u', '--user', required=False, type=str, help='Username for login (optional; if omitted, -p is treated as API key)')
    parser.add_argument('-p', '--passwd', required=True, type=str, help='Password (with -u) or API key (without -u)')
    parser.add_argument('-t', '--type', required=True, type=str,
                        help='Type of check: alerts, apps, datasets, zpool, zpool_capacity, repl, update, sys_cpu, sys_memory, sys_network')
    parser.add_argument('-pn', '--zpoolname', required=False, type=str, default='all',
                        help='ZPool name to check (default: all). Used with zpool and zpool_capacity.')
    parser.add_argument('-ns', '--no-ssl', required=False, action='store_true',
                        help='Disable SSL (use ws:// instead of wss://)')
    parser.add_argument('-nv', '--no-verify-cert', required=False, action='store_true',
                        help='Do not verify the server SSL certificate')
    parser.add_argument('-ig', '--ignore-dismissed-alerts', required=False, action='store_true',
                        help='Ignore alerts already dismissed in TrueNAS')
    parser.add_argument('-d', '--debug', required=False, action='store_true',
                        help='Display debugging information')
    parser.add_argument('-zw', '--zpool-warn', required=False, type=int, default=default_zpool_warning_percent,
                        help=f'ZPool/dataset warning threshold %% (default: {default_zpool_warning_percent})')
    parser.add_argument('-zc', '--zpool-critical', required=False, type=int, default=default_zool_critical_percent,
                        help=f'ZPool/dataset critical threshold %% (default: {default_zool_critical_percent})')
    parser.add_argument('-zp', '--zpool-perfdata', required=False, action='store_true',
                        help='Add perfdata to output (zpool_capacity, datasets, sys_cpu, sys_memory, sys_network)')
    parser.add_argument('-cw', '--cpu-warn', required=False, type=int, default=80,
                        help='CPU warning threshold %% avg/1h (default: 80)')
    parser.add_argument('-cc', '--cpu-critical', required=False, type=int, default=95,
                        help='CPU critical threshold %% avg/1h (default: 95)')
    parser.add_argument('-mw', '--mem-warn', required=False, type=int, default=80,
                        help='Memory warning threshold %% (default: 80)')
    parser.add_argument('-mc', '--mem-critical', required=False, type=int, default=95,
                        help='Memory critical threshold %% (default: 95)')
    parser.add_argument('-nw', '--net-warn', required=False, type=int, default=0,
                        help='Network warning threshold Kbit/s (0 = disabled)')
    parser.add_argument('-nc', '--net-critical', required=False, type=int, default=0,
                        help='Network critical threshold Kbit/s (0 = disabled)')

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args(sys.argv[1:])

    use_ssl = not args.no_ssl
    verify_ssl_cert = not args.no_verify_cert

    startup = Startup(
        args.hostname, args.user, args.passwd, use_ssl, verify_ssl_cert,
        args.ignore_dismissed_alerts, args.debug, args.zpoolname,
        args.zpool_warn, args.zpool_critical, args.zpool_perfdata,
        args.cpu_warn, args.cpu_critical,
        args.mem_warn, args.mem_critical,
        args.net_warn, args.net_critical
    )

    startup.connect()
    try:
        startup.handle_requested_alert_type(args.type)
    finally:
        startup.disconnect()


if __name__ == '__main__':
    main()
