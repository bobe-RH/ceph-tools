#!/usr/bin/env python3
# Ceph Audit History
# Gathers admin command history and config changes from a Ceph cluster via SSH.
#
# Requirements:
#   - Python 3.6+ (stdlib only)
#   - SSH access as root to a mon node
#
# Usage:
#   python3 ceph_audit.py <mon_host>
#   python3 ceph_audit.py <mon_host> --all              # include read-only commands
#   python3 ceph_audit.py <mon_host> --lines 500        # more journalctl lines
#   python3 ceph_audit.py <mon_host> --since 2026-08-10 # filter by date
import argparse
import json
import re
import subprocess
import sys

SSH_TIMEOUT = 60
MARKER = '===AUDIT_SEP==='

READ_ONLY_PREFIXES = {
    'status', 'health', 'df', 'version', 'osd tree', 'osd pool ls',
    'osd dump', 'osd perf', 'osd find', 'osd pool get', 'mon dump',
    'mon stat', 'pg stat', 'pg dump', 'config get', 'config show',
    'config diff', 'log last', 'time-sync-status', 'auth get',
    'auth list', 'fs ls', 'fs status', 'mds stat', 'osd pool stats',
    'osd tree', 'osd stat', 'orch ls', 'orch ps', 'orch host ls',
    'device ls', 'balancer status', 'crash ls', 'crash info',
    'progress', 'mgr dump', 'mgr module ls', 'config log',
    'osd blocked-by', 'osd getcrushmap', 'osd crush dump',
    'osd crush tree', 'osd crush rule dump', 'osd lspools',
    'osd pool application get', 'dashboard get-alertmanager-api-host',
}

AUDIT_RE = re.compile(
    r"log_channel\(audit\) log \[(?:DBG|INF)\] : "
    r"from='([^']*)'\s+entity='([^']*)'\s+"
    r"cmd=(\{.*?\})\s*:\s*(\w+)"
)

AUDIT_ALT_RE = re.compile(
    r"entity='([^']*)'\s+cmd=(\{.*?\})\s*:\s*(\w+)"
)

CONFIG_HEADER_RE = re.compile(
    r'^--- (\d+) --- (\d{4}-\d{2}-\d{2}T[\d:.]+\+\d+) ---$'
)

TS_RE = re.compile(r'^(\w{3}\s+\d+\s+[\d:]+)')

AUDIT_FILE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2}T[\d:.]+[+-]\d+)\s+\S+\s+\[(?:DBG|INF)\]\s+'
    r"from='([^']*)'\s+entity='([^']*)'\s+"
    r"cmd=(\{.*?\})\s*:\s*(\w+)"
)


def ssh_cmd(host, command):
    cmd = [
        'ssh',
        '-o', 'ConnectTimeout=30',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'BatchMode=yes',
        '-o', 'LogLevel=ERROR',
        'root@%s' % host,
        command,
    ]
    return cmd


def run_ssh(host, command):
    try:
        result = subprocess.Popen(
            ssh_cmd(host, command),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = result.communicate(timeout=SSH_TIMEOUT)
        if result.returncode != 0:
            err = stderr.decode('utf-8', errors='replace').strip()
            print('SSH error: %s' % err, file=sys.stderr)
            return None
        return stdout.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        result.kill()
        print('SSH timeout after %ds' % SSH_TIMEOUT, file=sys.stderr)
        return None
    except Exception as e:
        print('SSH failed: %s' % e, file=sys.stderr)
        return None


def parse_audit_lines(text, show_all=False, since=None):
    entries = []
    for line in text.splitlines():
        timestamp = ''
        entity = ''
        cmd_json = ''
        action = ''

        fm = AUDIT_FILE_RE.match(line.strip())
        if fm:
            raw_ts = fm.group(1)
            timestamp = raw_ts[:19].replace('T', ' ')
            entity = fm.group(3)
            cmd_json = fm.group(4)
            action = fm.group(5)
        else:
            m = AUDIT_RE.search(line)
            if m:
                entity = m.group(2)
                cmd_json = m.group(3)
                action = m.group(4)
            else:
                m2 = AUDIT_ALT_RE.search(line)
                if m2:
                    entity = m2.group(1)
                    cmd_json = m2.group(2)
                    action = m2.group(3)
                else:
                    continue

            ts_m = TS_RE.match(line)
            timestamp = ts_m.group(1) if ts_m else ''

        try:
            cmd_data = json.loads(cmd_json)
        except (json.JSONDecodeError, ValueError):
            continue

        prefix = cmd_data.get('prefix', '')
        if not prefix:
            continue

        if not show_all and prefix in READ_ONLY_PREFIXES:
            continue

        if 'mgr.' in entity and prefix in READ_ONLY_PREFIXES:
            continue

        cmd_str = prefix
        for k, v in sorted(cmd_data.items()):
            if k == 'prefix':
                continue
            cmd_str += ' %s=%s' % (k, v)

        entries.append({
            'timestamp': timestamp,
            'source': 'audit',
            'entity': entity,
            'command': cmd_str,
            'action': action,
        })

    return entries


def parse_config_log(text, since=None):
    entries = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        hdr_m = CONFIG_HEADER_RE.match(lines[i].strip())
        if not hdr_m:
            i += 1
            continue

        rev = hdr_m.group(1)
        timestamp = hdr_m.group(2)
        ts_display = timestamp[:19].replace('T', ' ')

        changes = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith('---'):
            line = lines[i].strip()
            if line.startswith('+') or line.startswith('-'):
                changes.append(line)
            i += 1

        if changes:
            plus_lines = [c for c in changes if c.startswith('+')]
            for pl in plus_lines:
                cmd_str = 'set %s' % pl[2:]
                entries.append({
                    'timestamp': ts_display,
                    'source': 'config',
                    'entity': 'config#%s' % rev,
                    'command': cmd_str,
                    'action': 'set',
                })

    return entries


def main():
    parser = argparse.ArgumentParser(description='Ceph Audit History')
    parser.add_argument('host', help='Mon host to SSH into')
    parser.add_argument('--all', '-a', action='store_true',
                        help='Include read-only commands')
    parser.add_argument('--lines', '-n', type=int, default=500,
                        help='Number of journalctl lines (default: 500)')
    parser.add_argument('--since', '-s', default=None,
                        help='Filter entries since date (YYYY-MM-DD)')
    parser.add_argument('--no-config', action='store_true',
                        help='Skip config change history')
    parser.add_argument('--config-entries', type=int, default=50,
                        help='Number of config log entries (default: 50)')
    args = parser.parse_args()

    cmds = []
    cmds.append("journalctl -u 'ceph-*@mon*' --no-pager -n %d 2>/dev/null | grep 'log_channel(audit)'" % args.lines)
    cmds.append("cat /var/log/ceph/*/ceph.audit.log 2>/dev/null; for gz in /var/log/ceph/*/ceph.audit.log.*.gz; do [ -f \"$gz\" ] && zcat \"$gz\" 2>/dev/null; done")
    if not args.no_config:
        cmds.append('ceph config log %d 2>/dev/null' % args.config_entries)

    batch = (" ; echo '%s' ; " % MARKER).join(cmds)

    sys.stderr.write('Collecting audit data from %s...\n' % args.host)
    sys.stderr.flush()
    output = run_ssh(args.host, batch)
    if output is None:
        sys.exit(1)

    parts = output.split(MARKER)

    all_entries = []

    journalctl_text = parts[0] if len(parts) > 0 else ''
    all_entries.extend(parse_audit_lines(journalctl_text, show_all=args.all, since=args.since))

    file_text = parts[1] if len(parts) > 1 else ''
    if file_text.strip():
        file_entries = parse_audit_lines(file_text, show_all=args.all, since=args.since)
        existing = set((e['timestamp'], e['command']) for e in all_entries)
        for e in file_entries:
            if (e['timestamp'], e['command']) not in existing:
                all_entries.append(e)

    config_idx = 2
    if not args.no_config and len(parts) > config_idx:
        config_text = parts[config_idx]
        all_entries.extend(parse_config_log(config_text, since=args.since))

    all_entries.sort(key=lambda e: e['timestamp'])

    if args.since:
        all_entries = [e for e in all_entries if args.since in e['timestamp'] or e['timestamp'] >= args.since]

    if not all_entries:
        print('No audit entries found.')
        return

    col_t = max(len('TIMESTAMP'), max(len(e['timestamp']) for e in all_entries)) + 2
    col_s = max(len('SOURCE'), 8)
    col_e = max(len('ENTITY'), max(len(e['entity']) for e in all_entries)) + 2

    fmt = '%-*s  %-*s  %-*s  %s'
    header = fmt % (col_t, 'TIMESTAMP', col_s, 'SOURCE', col_e, 'ENTITY', 'COMMAND')
    print(header)
    print('-' * len(header))

    for e in all_entries:
        print(fmt % (col_t, e['timestamp'], col_s, e['source'], col_e, e['entity'], e['command']))

    print('\n%d entries.' % len(all_entries), file=sys.stderr)


if __name__ == '__main__':
    main()
