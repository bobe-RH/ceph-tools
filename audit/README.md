# Ceph Audit History

Gathers admin command history and config changes from a Ceph cluster via SSH.

## Data Sources

The script reads from three sources on the target mon node:

1. **journalctl audit channel** -- the systemd journal for the Ceph audit channel
2. **Audit log files on disk** -- `/var/log/ceph/*/ceph.audit.log` including
   rotated `.gz` files
3. **ceph config log** -- the cluster configuration change history

## Behavior

Read-only commands are filtered out by default.  Use `--all` to include them.

## Requirements

- Python 3.6+ (stdlib only, no third-party packages)
- SSH access as root to a mon node

## Usage

```
python3 ceph_audit.py <mon_host>
python3 ceph_audit.py <mon_host> --all
python3 ceph_audit.py <mon_host> --lines 500
python3 ceph_audit.py <mon_host> --since 2026-08-10
python3 ceph_audit.py <mon_host> --no-config
```

### Options

- `<mon_host>` -- hostname or IP of a Ceph mon node (required)
- `--all` -- include read-only commands in the output
- `--lines N` -- limit output to N lines
- `--since DATE` -- only show entries from this date onward
- `--no-config` -- skip the ceph config log source

## Output

A formatted table with four columns:

```
TIMESTAMP                SOURCE      ENTITY       COMMAND
2026-08-10 14:22:01      journal     client.admin osd pool create mypool
2026-08-10 14:23:15      log_file    client.admin config set osd_scrub_begin_hour 22
2026-08-10 14:25:00      config_log  --           osd/osd_scrub_end_hour = 6
```
