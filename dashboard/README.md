# Ceph Multi-Cluster Dashboard

Curses-based terminal dashboard for monitoring multiple Ceph clusters from a single screen. Connects to each cluster's mon node over SSH, batches 7 ceph commands into a single SSH session per cluster, and renders a live overview with drill-down detail views.

## Features

- **Overview** -- displays all clusters at a glance: health status, MON/OSD/PG counts, capacity bars, IO throughput, and active alerts.
- **Detail view** -- drills down into a single cluster with OSD latency tables and per-pool usage breakdowns.
- Batches `ceph status`, `ceph osd df`, `ceph df`, `ceph osd perf`, `ceph osd pool stats`, `ceph health detail`, and `ceph pg stat` into one SSH session per cluster to minimize connection overhead.
- Python 3.6+ stdlib only -- no external dependencies.

## Requirements

- Python 3.6+
- SSH access as root to each cluster's mon node

## Configuration

Create a `clusters.json` file (see `clusters.json.example` for the format):

```json
{
    "clusters": [
        {
            "name": "production",
            "ssh_host": "mon1.example.com"
        }
    ],
    "defaults": {
        "ssh_user": "root",
        "ssh_port": 22,
        "ceph_conf": null,
        "keyring": null,
        "ssh_key": null
    }
}
```

Each entry in `clusters` requires:

- `name` -- display name for the cluster.
- `ssh_host` -- hostname or IP of a mon node.

The `defaults` block sets values inherited by all clusters unless overridden per-cluster:

- `ssh_user` -- SSH username (default: `root`).
- `ssh_port` -- SSH port (default: `22`).
- `ceph_conf` -- path to a non-default `ceph.conf` on the remote host, or `null`.
- `keyring` -- path to a non-default keyring on the remote host, or `null`.
- `ssh_key` -- path to a local SSH private key, or `null` to use the default.

## Usage

```
python3 ceph_dashboard.py
python3 ceph_dashboard.py --config clusters.json
```

## Keyboard Controls

```
q         Quit
r         Refresh all clusters now
Up/Down   Select cluster in overview
Enter     Open detail view for selected cluster
Esc       Return to overview from detail view
1-9       Jump directly to cluster by number
```
