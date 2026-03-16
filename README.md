# check_truenas_extended_play

Nagios/Icinga check plugin for **TrueNAS SCALE 25.x and later**. Connects via the WebSocket/JSON-RPC 2.0 API (`wss://host/api/current`) — no REST API required. This matters because:

- TrueNAS SCALE 25.x: the REST API only works for `FULL_ADMIN` users; the WebSocket API respects granular roles.
- TrueNAS SCALE 26+: the REST API is deprecated and will be removed.

Original script by Goran Tornqvist, extended by Stewart Loving-Gibbard, Folke Ashberg.

---

## Checks

| Type | Description | Key args |
|---|---|---|
| `alerts` | Active (non-dismissed) TrueNAS alerts | `-ig` to ignore dismissed |
| `apps` | App status — alerts if any app is not RUNNING | |
| `zpool` | ZPool health (ONLINE/DEGRADED/FAULTED/…) | `-pn` for specific pool |
| `zpool_capacity` | ZPool used space vs warn/crit thresholds | `-zw`, `-zc`, `-zp` |
| `datasets` | Dataset used space + locked detection | `-zw`, `-zc`, `-zp` |
| `repl` | Replication task health | |
| `update` | TrueNAS software update available? | |
| `sys_cpu` | CPU usage avg/1h from reporting | `-cw`, `-cc`, `-zp` |
| `sys_memory` | RAM usage avg/1h from reporting | `-mw`, `-mc`, `-zp` |
| `sys_network` | Network interface traffic avg/1h | `-nw`, `-nc`, `-zp` |

---

## Requirements

- Python 3.7+
- `websockets` library:
  ```
  pip3 install websockets
  # or: apt install python3-websockets
  ```

---

## TrueNAS Setup: Creating an API Key with Read-Only Access

### 1. Create a local user for the monitoring agent

Go to **Credentials → Local Users → Add**:

- **Username**: `icinga` (or any name)
- **Password**: set one or disable it
- **Shell**: `nologin`
- Do **not** add to any group yet

### 2. Add the user to the built-in Read-Only Administrators group

Go to **Credentials → Local Groups**. Find the group **`truenas_readonly_administrators`** (built-in). Click **Edit** → add your monitoring user to the **Members** list. Save.

This assigns the `READONLY_ADMIN` role, which grants read access to all resources via the WebSocket API.

> **Minimal roles alternative:** If you prefer, create a custom privilege under
> **Credentials → Privileges → Add** with only these roles:
> `ALERT_LIST_READ`, `APPS_READ`, `POOL_READ`, `DATASET_READ`,
> `REPLICATION_TASK_READ`, `SYSTEM_UPDATE_READ`, `REPORTING_READ`.
> Assign it to a new local group, and add your user to that group.

### 3. Create an API Key for the monitoring user

Log in to TrueNAS as the monitoring user (or have an admin do it on their behalf via **API Key** management).

Go to the **user menu (top right) → API Keys → Add**:

- **Name**: `icinga` (or similar)
- **Allowed Access**: leave as default (inherits the user's roles)
- Click **Generate** and copy the key — it won't be shown again.

The key format looks like: `4-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

---

## Usage

```
check_truenas_extended_play.py -H <host> -p <apikey> -t <type> [options]
```

### All arguments

```
  -H, --hostname        Hostname or IP address (required)
  -u, --user            Username for password login (optional; omit to use API key)
  -p, --passwd          Password (with -u) or API key (without -u) (required)
  -t, --type            Check type (required): alerts, apps, datasets, zpool,
                        zpool_capacity, repl, update, sys_cpu, sys_memory, sys_network
  -pn, --zpoolname      ZPool name to filter (default: all). For zpool/zpool_capacity.
  -ns, --no-ssl         Use ws:// instead of wss://
  -nv, --no-verify-cert Do not verify the SSL certificate
  -ig, --ignore-dismissed-alerts
                        Skip alerts already dismissed in TrueNAS
  -d, --debug           Print debug output
  -zw, --zpool-warn     ZPool/dataset warning threshold % (default: 80)
  -zc, --zpool-critical ZPool/dataset critical threshold % (default: 90)
  -zp, --zpool-perfdata Emit perfdata for capacity, cpu, memory, network checks
  -cw, --cpu-warn       CPU warning threshold % avg/1h (default: 80)
  -cc, --cpu-critical   CPU critical threshold % avg/1h (default: 95)
  -mw, --mem-warn       Memory warning threshold % (default: 80)
  -mc, --mem-critical   Memory critical threshold % (default: 95)
  -nw, --net-warn       Network warning threshold Kbit/s (0 = disabled)
  -nc, --net-critical   Network critical threshold Kbit/s (0 = disabled)
```

### Examples

```bash
# Alerts
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t alerts -nv

# ZPool health (all pools)
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t zpool -nv

# ZPool capacity with perfdata (warn 80%, crit 90%)
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t zpool_capacity -zw 80 -zc 90 -zp -nv

# Datasets with capacity check and perfdata
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t datasets -zw 80 -zc 90 -zp -nv

# Apps
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t apps -nv

# Replication
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t repl -nv

# Update check
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t update -nv

# CPU (warn 80%, crit 95%, with perfdata)
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t sys_cpu -cw 80 -cc 95 -zp -nv

# Memory (warn 80%, crit 95%, with perfdata)
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t sys_memory -mw 80 -mc 95 -zp -nv

# Network (no thresholds, perfdata only)
check_truenas_extended_play.py -H nas.example.com -p 4-xxxxx -t sys_network -zp -nv
```

---

## Icinga Integration

See the `config/` directory for ready-to-use configuration files:

| File | Purpose |
|---|---|
| `config/icinga2_commands.conf` | `CheckCommand` definition |
| `config/icinga2_services.conf` | Service templates and `apply` rules |
| `config/director_basket.json` | Icinga Director import basket |

### API Key: set once per host

The API key only needs to be set **once on the host object**. Icinga 2 resolves command macros (`$truenas_apikey$`) by checking service vars first, then host vars — so all 10 TrueNAS services on a host inherit the key automatically without any repetition.

### Classic Icinga 2 config

1. Place `check_truenas_extended_play.py` in your plugin directory (e.g. `/usr/lib/nagios/plugins/`) and make it executable.
2. Include `icinga2_commands.conf` and `icinga2_services.conf` in your Icinga config (e.g. drop them into `/etc/icinga2/conf.d/`).
3. Set `vars.truenas_apikey` on each TrueNAS host — all 10 checks activate automatically via `apply` rules:

```
object Host "nas01" {
  import "generic-host"
  address                     = "nas.example.com"
  vars.truenas_apikey         = "4-xxxxxxx"   // set once — inherited by all services
  vars.truenas_no_verify_cert = true           // for self-signed TrueNAS certs
}
```

All checks appear automatically. To override thresholds per host:

```
  vars.truenas_zpool_warn = 70   // override default of 80%
  vars.truenas_cpu_crit   = 90   // override default of 95%
```

### Icinga Director

Import `config/director_basket.json` via **Icinga Director → Configuration → Baskets → Upload / Import**.

The basket creates:

| Object | Details |
|---|---|
| `DataList` | `truenas_check_types` — dropdown with all 10 check types |
| `Datafield` | 18 fields for all parameters (shown as UI form fields in Director) |
| `CheckCommand` | `check_truenas` with all arguments wired to Datafields |
| `HostTemplate` | `TrueNAS Host` — import this on your host; set `truenas_apikey` here once |
| `ServiceTemplate` | `check_truenas_generic` base template + one per check type |
| `ServiceSet` | `TrueNAS` — contains all 10 services; assign to hosts in one step |

**Workflow after import:**

1. **Host:** Create or edit your TrueNAS host in Director → import template `TrueNAS Host` → fill in `TrueNAS API-Key/Passwort`. The key is stored on the host and shared by all services.

2. **Services:** All 10 checks are assigned automatically via the ServiceSet's assign filter (`"TrueNAS Host" = host.templates`). No manual step required.

3. **Deploy** the configuration.

---

## Version History

| Version | Date | Notes |
|---|---|---|
| 1.0 | 2020-06-18 | Initial release |
| 1.1 | 2020-08-14 | Replication check fixes |
| 1.2 | 2021-12-03 | API key authentication (Folke Ashberg) |
| 1.3 | 2021-12-04 | Update check |
| 1.4 | 2021-12-06 | ZPool capacity check + perfdata |
| 1.41 | 2022-03-01 | Python version check |
| 1.42 | 2023-01-30 | Byte count typo fix (1204→1024) |
| 2.0 | 2026-03-13 | Full rewrite: WebSocket/JSON-RPC 2.0 transport, TrueNAS SCALE 25.x support, new checks: apps, datasets, sys_cpu, sys_memory, sys_network |
