#!/usr/bin/env python3
"""RGW Multisite Sync Dashboard - Monitor sync status, bucket sync, period validation, and errors."""

import argparse
import curses
import json
import locale
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor


VERSION = "1.0"
MARKER = "===CEPH_CMD_SEP==="
SSH_TIMEOUT = 60
MIN_WIDTH = 80
MIN_HEIGHT = 24
MAX_BUCKETS_PER_BATCH = 10


def format_bytes(nbytes):
    if nbytes is None or nbytes < 0:
        return "N/A"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(nbytes) < 1024.0:
            if nbytes == int(nbytes):
                return f"{int(nbytes)} {unit}"
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f} EiB"


def format_ago(ts):
    if ts is None:
        return "never"
    delta = time.time() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta/60)}m{int(delta%60)}s ago"
    return f"{int(delta/3600)}h{int((delta%3600)/60)}m ago"


class ZoneConfig:
    def __init__(self, name, ssh_host, ssh_user="root", ssh_port=22,
                 ssh_key=None, is_master=False):
        self.name = name
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.ssh_key = ssh_key
        self.is_master = is_master

    def ssh_cmd(self, remote_command):
        cmd = [
            "ssh",
            "-o", "ConnectTimeout=30",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR",
            "-p", str(self.ssh_port),
        ]
        if self.ssh_key:
            cmd.extend(["-i", self.ssh_key])
        cmd.append(f"{self.ssh_user}@{self.ssh_host}")
        cmd.append(remote_command)
        return cmd


class ZoneState:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.sync_status_raw = None
        self.sync_errors = None
        self.period_data = None
        self.realm_data = None
        self.zonegroup_data = None
        self.zone_data = None
        self.bucket_list = None
        self.datalog_status = None
        self.prev_datalog_status = None
        self.metadata_sync = None
        self.data_sync_sources = None
        self.bucket_sync = {}
        self.stuck_datalog_shards = 0
        self.last_update = None
        self.last_bucket_update = None
        self.last_attempt = None
        self.error = None
        self.reachable = False
        self.collecting = False
        self.collecting_buckets = False

    def period_id(self):
        if not self.period_data:
            return "N/A"
        return self.period_data.get("id", "N/A")

    def period_epoch(self):
        if not self.period_data:
            return 0
        return self.period_data.get("epoch", 0)

    def master_zone_name(self):
        if not self.period_data:
            return "N/A"
        master_id = self.period_data.get("master_zone", "")
        pm = self.period_data.get("period_map", {})
        for zg in pm.get("zonegroups", []):
            for z in zg.get("zones", []):
                if z.get("id") == master_id:
                    return z.get("name", master_id[:12])
        return master_id[:12] if master_id else "N/A"

    def enabled_features(self):
        if not self.zonegroup_data:
            return []
        return self.zonegroup_data.get("enabled_features", [])

    def zone_endpoints(self):
        if not self.zone_data:
            return []
        return self.zone_data.get("endpoints", [])

    def error_count(self):
        total = 0
        for shard in (self.sync_errors or []):
            total += len(shard.get("entries", []))
        return total

    def is_caught_up(self):
        if self.metadata_sync and not self.metadata_sync.get("caught_up", False):
            return False
        if self.data_sync_sources:
            for src in self.data_sync_sources:
                if not src.get("caught_up", False):
                    return False
        return True

    def total_behind(self):
        total = 0
        if self.data_sync_sources:
            for src in self.data_sync_sources:
                total += src.get("behind_shards", 0)
        return total


class SyncStatusParser:
    RE_REALM = re.compile(r'realm\s+(\S+)\s+\(([^)]+)\)')
    RE_ZONEGROUP = re.compile(r'zonegroup\s+(\S+)\s+\(([^)]+)\)')
    RE_ZONE = re.compile(r'^\s+zone\s+(\S+)\s+\(([^)]+)\)', re.MULTILINE)
    RE_FULL_SYNC = re.compile(r'full sync:\s+(\d+)/(\d+)\s+shards')
    RE_INC_SYNC = re.compile(r'incremental sync:\s+(\d+)/(\d+)\s+shards')
    RE_CAUGHT_UP = re.compile(r'data is caught up with source|caught up with (?:master|source)')
    RE_BEHIND = re.compile(r'behind on (\d+) shards?')
    RE_RECOVERING = re.compile(r'recovering\s+(\d+)\s+shards?')
    RE_DATA_SOURCE = re.compile(r'source:\s+(\S+)\s+\(([^)]+)\)')

    @staticmethod
    def parse_sync_status(text):
        if not text or not text.strip():
            return {"metadata_sync": None, "data_sync_sources": []}

        result = {"metadata_sync": None, "data_sync_sources": []}

        sections = re.split(r'\n(?=\s*(?:metadata sync|data sync source:))', text)

        for section in sections:
            stripped = section.strip()

            if stripped.startswith("metadata sync"):
                meta = {"status": "", "full_synced": 0, "full_total": 0,
                        "inc_synced": 0, "inc_total": 0, "caught_up": False,
                        "behind_shards": 0, "recovering_shards": 0}

                if "no sync" in stripped.lower():
                    meta["status"] = "no sync (master zone)"
                    meta["caught_up"] = True
                    result["metadata_sync"] = meta
                    continue

                full_m = SyncStatusParser.RE_FULL_SYNC.search(section)
                if full_m:
                    meta["full_synced"] = int(full_m.group(1))
                    meta["full_total"] = int(full_m.group(2))

                inc_m = SyncStatusParser.RE_INC_SYNC.search(section)
                if inc_m:
                    meta["inc_synced"] = int(inc_m.group(1))
                    meta["inc_total"] = int(inc_m.group(2))

                if SyncStatusParser.RE_CAUGHT_UP.search(section):
                    meta["caught_up"] = True
                    meta["status"] = "caught up"
                else:
                    meta["status"] = "syncing"

                behind_m = SyncStatusParser.RE_BEHIND.search(section)
                if behind_m:
                    meta["behind_shards"] = int(behind_m.group(1))
                    meta["status"] = "behind"

                recovering_m = SyncStatusParser.RE_RECOVERING.search(section)
                if recovering_m:
                    meta["recovering_shards"] = int(recovering_m.group(1))
                    meta["status"] = "recovering"

                result["metadata_sync"] = meta

            elif stripped.startswith("data sync source:"):
                source_m = SyncStatusParser.RE_DATA_SOURCE.search(stripped)
                src = {
                    "source_name": source_m.group(2) if source_m else "unknown",
                    "source_id": source_m.group(1) if source_m else "",
                    "status": "", "full_synced": 0, "full_total": 0,
                    "inc_synced": 0, "inc_total": 0, "caught_up": False,
                    "behind_shards": 0, "recovering_shards": 0,
                }

                full_m = SyncStatusParser.RE_FULL_SYNC.search(section)
                if full_m:
                    src["full_synced"] = int(full_m.group(1))
                    src["full_total"] = int(full_m.group(2))

                inc_m = SyncStatusParser.RE_INC_SYNC.search(section)
                if inc_m:
                    src["inc_synced"] = int(inc_m.group(1))
                    src["inc_total"] = int(inc_m.group(2))

                if SyncStatusParser.RE_CAUGHT_UP.search(section):
                    src["caught_up"] = True
                    src["status"] = "caught up"
                else:
                    src["status"] = "syncing"

                behind_m = SyncStatusParser.RE_BEHIND.search(section)
                if behind_m:
                    src["behind_shards"] = int(behind_m.group(1))
                    src["status"] = "behind"

                recovering_m = SyncStatusParser.RE_RECOVERING.search(section)
                if recovering_m:
                    src["recovering_shards"] = int(recovering_m.group(1))
                    src["status"] = "recovering"

                result["data_sync_sources"].append(src)

        return result

    @staticmethod
    def parse_bucket_sync_status(text):
        if not text or not text.strip():
            return {"sources": []}

        result = {"sources": []}
        sections = re.split(r'\n(?=\s*source zone:)', text)

        for section in sections:
            stripped = section.strip()
            if not stripped.startswith("source zone:"):
                continue

            zone_m = re.search(r'source zone:\s+(\S+)\s+\(([^)]+)\)', stripped)
            src = {
                "source_name": zone_m.group(2) if zone_m else "unknown",
                "source_id": zone_m.group(1) if zone_m else "",
                "full_synced": 0, "full_total": 0,
                "inc_synced": 0, "inc_total": 0,
                "caught_up": False, "behind_shards": 0,
            }

            full_m = SyncStatusParser.RE_FULL_SYNC.search(section)
            if full_m:
                src["full_synced"] = int(full_m.group(1))
                src["full_total"] = int(full_m.group(2))

            inc_m = SyncStatusParser.RE_INC_SYNC.search(section)
            if inc_m:
                src["inc_synced"] = int(inc_m.group(1))
                src["inc_total"] = int(inc_m.group(2))

            if SyncStatusParser.RE_CAUGHT_UP.search(section):
                src["caught_up"] = True

            behind_m = SyncStatusParser.RE_BEHIND.search(section)
            if behind_m:
                src["behind_shards"] = int(behind_m.group(1))

            result["sources"].append(src)

        return result


class MultisiteCollector:
    def __init__(self, zones, interval=30, bucket_interval=60):
        self.zones = zones
        self.interval = interval
        self.bucket_interval = bucket_interval
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=min(8, len(zones) * 2 + 2))
        self._timer = None
        self._last_bucket_collect = 0

    def _run_ssh(self, config, command):
        try:
            result = subprocess.run(
                config.ssh_cmd(command),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=SSH_TIMEOUT
            )
            if result.returncode != 0:
                err = result.stderr.strip()
                if "Permission denied" in err or "publickey" in err:
                    return False, "Authentication failed"
                if "Connection refused" in err:
                    return False, "Connection refused"
                if "No route to host" in err:
                    return False, "No route to host"
                if "Could not resolve" in err:
                    return False, f"Cannot resolve hostname: {config.ssh_host}"
                return False, err or f"SSH exit code {result.returncode}"
            return True, result.stdout
        except subprocess.TimeoutExpired:
            return False, f"SSH timeout ({SSH_TIMEOUT}s)"
        except Exception as e:
            return False, str(e)

    def _collect_zone(self, zone):
        zone.collecting = True
        zone.last_attempt = time.time()
        config = zone.config

        commands = [
            "radosgw-admin sync status 2>/dev/null",
            "radosgw-admin sync error list --format json 2>/dev/null",
            "radosgw-admin period get --format json 2>/dev/null",
            "radosgw-admin realm get --format json 2>/dev/null",
            "radosgw-admin zonegroup get --format json 2>/dev/null",
            "radosgw-admin zone get --format json 2>/dev/null",
            "radosgw-admin bucket list --format json 2>/dev/null",
            "radosgw-admin datalog status --format json 2>/dev/null",
        ]
        batch = f" ; echo '{MARKER}' ; ".join(commands)

        ok, output = self._run_ssh(config, batch)
        with zone.lock:
            if not ok:
                zone.error = output
                zone.reachable = False
                zone.collecting = False
                return

            zone.reachable = True
            zone.error = None
            parts = output.split(MARKER)

            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                try:
                    if i == 0:
                        zone.sync_status_raw = part
                        parsed = SyncStatusParser.parse_sync_status(part)
                        zone.metadata_sync = parsed.get("metadata_sync")
                        zone.data_sync_sources = parsed.get("data_sync_sources", [])
                    elif i == 1:
                        zone.sync_errors = json.loads(part)
                    elif i == 2:
                        zone.period_data = json.loads(part)
                    elif i == 3:
                        zone.realm_data = json.loads(part)
                    elif i == 4:
                        zone.zonegroup_data = json.loads(part)
                    elif i == 5:
                        zone.zone_data = json.loads(part)
                    elif i == 6:
                        zone.bucket_list = json.loads(part)
                    elif i == 7:
                        new_datalog = json.loads(part)
                        if zone.datalog_status and new_datalog:
                            stuck = 0
                            for old, new in zip(zone.datalog_status, new_datalog):
                                old_marker = old.get("marker", "")
                                new_marker = new.get("marker", "")
                                if old_marker == new_marker and old_marker != "":
                                    stuck += 1
                            zone.stuck_datalog_shards = stuck
                        zone.prev_datalog_status = zone.datalog_status
                        zone.datalog_status = new_datalog
                except (json.JSONDecodeError, TypeError, ValueError):
                    if i == 1:
                        zone.sync_errors = []

            zone.last_update = time.time()
            zone.collecting = False

    def _collect_bucket_sync(self, zone):
        with zone.lock:
            buckets = list(zone.bucket_list or [])
        if not buckets:
            return

        zone.collecting_buckets = True
        new_bucket_sync = {}

        for chunk_start in range(0, len(buckets), MAX_BUCKETS_PER_BATCH):
            chunk = buckets[chunk_start:chunk_start + MAX_BUCKETS_PER_BATCH]
            commands = [
                f"radosgw-admin bucket sync status --bucket='{b}' 2>/dev/null"
                for b in chunk
            ]
            batch = f" ; echo '{MARKER}' ; ".join(commands)
            ok, output = self._run_ssh(zone.config, batch)
            if not ok:
                break
            parts = output.split(MARKER)
            for j, part in enumerate(parts):
                if j < len(chunk):
                    parsed = SyncStatusParser.parse_bucket_sync_status(part.strip())
                    new_bucket_sync[chunk[j]] = parsed

        with zone.lock:
            zone.bucket_sync = new_bucket_sync
            zone.last_bucket_update = time.time()
            zone.collecting_buckets = False

    def collect_all(self):
        futures = []
        for zone in self.zones:
            if not zone.collecting:
                futures.append(self._executor.submit(self._collect_zone, zone))
        for f in futures:
            try:
                f.result(timeout=SSH_TIMEOUT + 5)
            except Exception:
                pass

        now = time.time()
        if now - self._last_bucket_collect >= self.bucket_interval:
            self._last_bucket_collect = now
            bucket_futures = []
            for zone in self.zones:
                if not zone.collecting_buckets:
                    bucket_futures.append(
                        self._executor.submit(self._collect_bucket_sync, zone))
            for f in bucket_futures:
                try:
                    f.result(timeout=SSH_TIMEOUT * 5)
                except Exception:
                    pass

    def start_periodic(self):
        def _loop():
            while not self._stop.is_set():
                self.collect_all()
                self._stop.wait(self.interval)
        self._timer = threading.Thread(target=_loop, daemon=True)
        self._timer.start()

    def force_refresh(self):
        threading.Thread(target=self.collect_all, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._executor.shutdown(wait=False)


C_OK = 1
C_WARN = 2
C_ERR = 3
C_CYAN = 4
C_BAR = 5
C_SELECTED = 6
C_DIM = 7


class RGWSyncDashboard:
    def __init__(self, zones, realm, zonegroup, interval=30,
                 bucket_interval=60, no_color=False):
        self.zones = zones
        self.realm = realm
        self.zonegroup = zonegroup
        self.collector = MultisiteCollector(zones, interval, bucket_interval)
        self.interval = interval
        self.no_color = no_color
        self.view_mode = "overview"
        self.selected_bucket = 0
        self.scroll_offset = 0
        self.running = True
        self.pad = None
        self.pad_lines = 0

    def run(self, stdscr):
        self.stdscr = stdscr
        self._init_curses()
        self.collector.start_periodic()

        try:
            while self.running:
                try:
                    self._draw()
                except curses.error:
                    pass
                key = stdscr.getch()
                if key != -1:
                    self._handle_key(key)
        finally:
            self.collector.stop()

    def _init_curses(self):
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.timeout(500)
        if curses.has_colors() and not self.no_color:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(C_OK, curses.COLOR_GREEN, -1)
            curses.init_pair(C_WARN, curses.COLOR_YELLOW, -1)
            curses.init_pair(C_ERR, curses.COLOR_RED, -1)
            curses.init_pair(C_CYAN, curses.COLOR_CYAN, -1)
            curses.init_pair(C_BAR, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(C_SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(C_DIM, curses.COLOR_WHITE, -1)

    def _color(self, pair_id, bold=False):
        if self.no_color or not curses.has_colors():
            attr = curses.A_NORMAL
        else:
            attr = curses.color_pair(pair_id)
        if bold:
            attr |= curses.A_BOLD
        return attr

    def _draw(self):
        my, mx = self.stdscr.getmaxyx()
        if my < MIN_HEIGHT or mx < MIN_WIDTH:
            self.stdscr.clear()
            msg = f"Terminal too small ({mx}x{my}). Need {MIN_WIDTH}x{MIN_HEIGHT}."
            try:
                self.stdscr.addstr(my // 2, max(0, (mx - len(msg)) // 2), msg)
            except curses.error:
                pass
            self.stdscr.refresh()
            return

        pad_h = max(1000, len(self.zones) * 30 + 200)
        if self.pad is None or self.pad.getmaxyx()[0] < pad_h or self.pad.getmaxyx()[1] < mx:
            self.pad = curses.newpad(pad_h, max(mx, 300))
        self.pad.clear()

        self._draw_top_bar(my, mx)
        self._draw_bottom_bar(my, mx)

        self.pad_lines = 0
        if self.view_mode == "overview":
            self._draw_overview(mx)
        elif self.view_mode == "buckets":
            self._draw_buckets(mx)
        elif self.view_mode == "period":
            self._draw_period(mx)
        elif self.view_mode == "errors":
            self._draw_errors(mx)
        elif self.view_mode == "bucket_detail":
            self._draw_bucket_detail(mx)

        content_h = my - 2
        max_scroll = max(0, self.pad_lines - content_h)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        try:
            self.pad.refresh(self.scroll_offset, 0, 1, 0, my - 2, mx - 1)
        except curses.error:
            pass
        self.stdscr.refresh()

    def _draw_top_bar(self, my, mx):
        collecting = any(z.collecting or z.collecting_buckets for z in self.zones)
        ts = time.strftime("%H:%M:%S")

        view_labels = {
            "overview": "Overview",
            "buckets": "Bucket Sync",
            "period": "Period Validation",
            "errors": "Sync Errors",
            "bucket_detail": "Bucket Detail",
        }
        view_label = view_labels.get(self.view_mode, "")

        left = f" RGW SYNC | Realm: {self.realm} | {view_label}"
        right = f"Refresh: {self.interval}s | {ts} "
        if collecting:
            right = f"[collecting...] {right}"

        bar = left + " " * max(1, mx - len(left) - len(right)) + right
        bar = bar[:mx]

        try:
            self.stdscr.attron(self._color(C_BAR))
            self.stdscr.addstr(0, 0, bar.ljust(mx)[:mx])
            self.stdscr.attroff(self._color(C_BAR))
        except curses.error:
            pass

    def _draw_bottom_bar(self, my, mx):
        keys_map = {
            "overview": " q:Quit  r:Refresh  b:Buckets  e:Errors  p:Period  Up/Dn:Scroll ",
            "buckets": " q:Quit  r:Refresh  Esc:Back  Up/Dn:Select  Enter:Detail  PgUp/Dn ",
            "period": " q:Quit  r:Refresh  Esc:Back  Up/Dn:Scroll  PgUp/Dn ",
            "errors": " q:Quit  r:Refresh  Esc:Back  Up/Dn:Scroll  PgUp/Dn ",
            "bucket_detail": " q:Quit  r:Refresh  Esc:Back  Up/Dn:Scroll ",
        }
        keys = keys_map.get(self.view_mode, "")

        scroll_ind = ""
        if self.pad_lines > my - 2:
            if self.scroll_offset > 0 and self.scroll_offset < self.pad_lines - (my - 2):
                scroll_ind = " [^v]"
            elif self.scroll_offset > 0:
                scroll_ind = " [^]"
            else:
                scroll_ind = " [v]"

        bar = keys + scroll_ind
        bar = bar + " " * max(0, mx - len(bar))
        bar = bar[:mx]

        try:
            self.stdscr.attron(self._color(C_BAR))
            self.stdscr.addstr(my - 1, 0, bar[:mx - 1])
            self.stdscr.attroff(self._color(C_BAR))
        except curses.error:
            pass

    def _pad_addstr(self, row, col, text, attr=curses.A_NORMAL):
        try:
            self.pad.addstr(row, col, text, attr)
        except curses.error:
            pass

    def _merged_bucket_list(self):
        buckets = set()
        for zone in self.zones:
            with zone.lock:
                if zone.bucket_list:
                    buckets.update(zone.bucket_list)
        return sorted(buckets)

    # ── Overview ──

    def _draw_overview(self, mx):
        row = 0

        period_id = "N/A"
        epoch = 0
        for z in self.zones:
            with z.lock:
                if z.period_data:
                    period_id = z.period_id()
                    epoch = z.period_epoch()
                    break

        pid_short = period_id[:12] + ".." if len(period_id) > 14 else period_id
        header = f"REALM: {self.realm}   Zonegroup: {self.zonegroup}   Period: {pid_short}  Epoch: {epoch}"
        self._pad_addstr(row, 2, header, self._color(C_CYAN, bold=True))
        row += 2

        for zone in self.zones:
            with zone.lock:
                row = self._draw_zone_block(row, mx, zone)
            row += 1

        row = self._draw_validation_summary(row, mx)
        self.pad_lines = row

    def _draw_zone_block(self, row, mx, zone):
        name = zone.config.name
        master_tag = " (master)" if zone.config.is_master else ""
        ts_str = ""
        if zone.last_update:
            ts_str = time.strftime("%H:%M:%S", time.localtime(zone.last_update))

        if zone.reachable:
            reach_str = "REACHABLE"
            reach_attr = self._color(C_OK, bold=True)
        else:
            reach_str = "UNREACHABLE"
            reach_attr = self._color(C_ERR, bold=True)

        name_part = f" {name}{master_tag} "
        reach_part = f" {reach_str} "
        ts_part = f" ({ts_str})" if ts_str else ""
        fill_len = max(1, mx - len(name_part) - len(reach_part) - len(ts_part) - 6)

        header_line = f"=={name_part}" + "=" * fill_len
        self._pad_addstr(row, 0, header_line, self._color(C_CYAN, bold=True))
        self._pad_addstr(row, len(header_line), reach_part, reach_attr)
        if ts_part:
            self._pad_addstr(row, len(header_line) + len(reach_part), ts_part,
                            self._color(C_DIM))
        row += 1

        if not zone.reachable:
            self._pad_addstr(row, 4, f"Error: {zone.error or 'Unknown'}",
                            self._color(C_ERR))
            row += 1
            if zone.last_update:
                self._pad_addstr(row, 4,
                                f"Last successful: {format_ago(zone.last_update)}",
                                self._color(C_DIM))
                row += 1
            return row

        # Metadata sync
        if zone.config.is_master:
            self._pad_addstr(row, 4, "Metadata Sync: N/A (master zone)",
                            self._color(C_DIM))
            row += 1
        elif zone.metadata_sync:
            ms = zone.metadata_sync
            status = ms.get("status", "unknown")
            if status == "caught up":
                attr = self._color(C_OK)
            elif status in ("behind", "recovering"):
                attr = self._color(C_WARN)
            else:
                attr = curses.A_NORMAL

            line = f"Metadata Sync: {ms['full_synced']}/{ms['full_total']} full, " \
                   f"{ms['inc_synced']}/{ms['inc_total']} inc"
            self._pad_addstr(row, 4, line)
            row += 1

            if ms.get("caught_up"):
                self._pad_addstr(row, 6, "caught up with master", self._color(C_OK))
            elif ms.get("behind_shards", 0) > 0:
                self._pad_addstr(row, 6, f"behind on {ms['behind_shards']} shards",
                                self._color(C_WARN))
            elif ms.get("recovering_shards", 0) > 0:
                self._pad_addstr(row, 6,
                                f"recovering {ms['recovering_shards']} shards",
                                self._color(C_WARN))
            row += 1
        else:
            self._pad_addstr(row, 4, "Metadata Sync: waiting for data...",
                            self._color(C_DIM))
            row += 1

        # Data sync per source
        if zone.data_sync_sources:
            for src in zone.data_sync_sources:
                src_name = src.get("source_name", "unknown")
                self._pad_addstr(row, 4, f"Data Sync <- {src_name}:",
                                curses.A_BOLD)
                row += 1

                line = f"Full: {src['full_synced']}/{src['full_total']}  " \
                       f"Incremental: {src['inc_synced']}/{src['inc_total']}"
                self._pad_addstr(row, 6, line)

                if src.get("caught_up"):
                    self._pad_addstr(row, 6 + len(line) + 2, "caught up",
                                    self._color(C_OK))
                elif src.get("behind_shards", 0) > 0:
                    behind_str = f"behind {src['behind_shards']} shards"
                    attr = self._color(C_ERR) if src["behind_shards"] >= 10 \
                           else self._color(C_WARN)
                    self._pad_addstr(row, 6 + len(line) + 2, behind_str, attr)
                elif src.get("recovering_shards", 0) > 0:
                    rec_str = f"recovering {src['recovering_shards']} shards"
                    self._pad_addstr(row, 6 + len(line) + 2, rec_str,
                                    self._color(C_WARN))
                row += 1
        else:
            self._pad_addstr(row, 4, "Data Sync: waiting for data...",
                            self._color(C_DIM))
            row += 1

        # Datalog
        if zone.datalog_status:
            total_shards = len(zone.datalog_status)
            stuck = zone.stuck_datalog_shards
            is_behind = not zone.is_caught_up()
            if stuck > 0 and is_behind:
                self._pad_addstr(row, 4,
                                f"Datalog: {total_shards} shards, {stuck} not advancing",
                                self._color(C_ERR))
            elif is_behind:
                self._pad_addstr(row, 4,
                                f"Datalog: {total_shards} shards, syncing",
                                self._color(C_WARN))
            elif stuck > 0:
                self._pad_addstr(row, 4,
                                f"Datalog: {total_shards} shards, idle (caught up)",
                                self._color(C_OK))
            else:
                self._pad_addstr(row, 4,
                                f"Datalog: {total_shards} shards, all advancing",
                                self._color(C_OK))
            row += 1

        # Error count
        err_count = zone.error_count()
        if err_count > 0:
            self._pad_addstr(row, 4, f"Errors: {err_count}",
                            self._color(C_ERR, bold=True))
        else:
            self._pad_addstr(row, 4, "Errors: 0", self._color(C_OK))
        row += 1

        return row

    def _draw_validation_summary(self, row, mx):
        row += 1
        self._pad_addstr(row, 0, "  " + "-" * (mx - 4), self._color(C_DIM))
        row += 1

        # Period validation
        period_match = self._periods_match()
        if period_match is None:
            self._pad_addstr(row, 2, "PERIOD: waiting for data",
                            self._color(C_DIM))
        elif period_match:
            self._pad_addstr(row, 2, "PERIOD: MATCH", self._color(C_OK, bold=True))
        else:
            self._pad_addstr(row, 2, "PERIOD: MISMATCH",
                            self._color(C_ERR, bold=True))

        # Features
        features = set()
        for z in self.zones:
            with z.lock:
                features.update(z.enabled_features())
        feat_str = ", ".join(sorted(features)) if features else "none"
        feat_col = 25
        self._pad_addstr(row, feat_col, f"FEATURES: {feat_str}")

        # Bucket summary
        all_buckets = self._merged_bucket_list()
        caught_up_count = 0
        for bname in all_buckets:
            bucket_ok = True
            for z in self.zones:
                with z.lock:
                    bsync = z.bucket_sync.get(bname)
                    if bsync:
                        for src in bsync.get("sources", []):
                            if not src.get("caught_up", False):
                                bucket_ok = False
            if bucket_ok:
                caught_up_count += 1

        bucket_col = feat_col + len(f"FEATURES: {feat_str}") + 4
        if all_buckets:
            bkt_str = f"BUCKETS: {caught_up_count} synced / {len(all_buckets)} total"
            bkt_attr = self._color(C_OK) if caught_up_count == len(all_buckets) \
                       else self._color(C_WARN)
            self._pad_addstr(row, bucket_col, bkt_str, bkt_attr)
        row += 1

        return row

    def _periods_match(self):
        ids = []
        for z in self.zones:
            with z.lock:
                if not z.period_data:
                    return None
                ids.append(z.period_id())
        if len(ids) < 2:
            return None
        return len(set(ids)) == 1

    # ── Bucket Sync View ──

    def _draw_buckets(self, mx):
        row = 0
        buckets = self._merged_bucket_list()

        ts_str = ""
        for z in self.zones:
            with z.lock:
                if z.last_bucket_update:
                    ts_str = time.strftime("%H:%M:%S",
                                          time.localtime(z.last_bucket_update))
                    break

        header = f"  BUCKET SYNC STATUS ({len(buckets)} buckets)"
        if ts_str:
            header += f"   [Updated: {ts_str}]"
        self._pad_addstr(row, 0, header, self._color(C_CYAN, bold=True))
        row += 2

        if not buckets:
            self._pad_addstr(row, 4, "No buckets found", self._color(C_DIM))
            row += 1
            self.pad_lines = row
            return

        any_data = any(z.bucket_sync for z in self.zones)
        if not any_data:
            collecting = any(z.collecting_buckets for z in self.zones)
            msg = "Collecting bucket sync data..." if collecting \
                  else "Bucket sync data not yet collected. Press r to refresh."
            self._pad_addstr(row, 4, msg, self._color(C_DIM))
            row += 1
            self.pad_lines = row
            return

        zone_names = [z.config.name for z in self.zones]
        col1_w = max(25, max(len(b) for b in buckets) + 2)
        col2_w = 25

        hdr = f"  {'BUCKET':<{col1_w}}"
        for zn in zone_names:
            hdr += f"  {zn:<{col2_w}}"
        self._pad_addstr(row, 0, hdr[:mx], curses.A_BOLD)
        row += 1
        self._pad_addstr(row, 0, "  " + "-" * min(mx - 4, col1_w + col2_w * len(zone_names) + 4))
        row += 1

        self.selected_bucket = max(0, min(self.selected_bucket, len(buckets) - 1))

        for bi, bname in enumerate(buckets):
            is_selected = (bi == self.selected_bucket)
            row_attr = self._color(C_SELECTED) if is_selected else curses.A_NORMAL

            self._pad_addstr(row, 2, f"{bname:<{col1_w}}", row_attr)

            col = 2 + col1_w + 2
            for z in self.zones:
                with z.lock:
                    bsync = z.bucket_sync.get(bname)

                if not bsync or not bsync.get("sources"):
                    self._pad_addstr(row, col, f"{'--':<{col2_w}}", self._color(C_DIM))
                else:
                    all_caught = all(s.get("caught_up", False)
                                    for s in bsync["sources"])
                    total_behind = sum(s.get("behind_shards", 0)
                                       for s in bsync["sources"])
                    if all_caught:
                        status_str = "caught up"
                        attr = self._color(C_OK) if not is_selected else row_attr
                    elif total_behind > 0:
                        status_str = f"behind ({total_behind} shards)"
                        attr = self._color(C_WARN) if not is_selected else row_attr
                    else:
                        status_str = "syncing"
                        attr = self._color(C_WARN) if not is_selected else row_attr

                    self._pad_addstr(row, col, f"{status_str:<{col2_w}}", attr)
                col += col2_w + 2

            row += 1

        self.pad_lines = row

    # ── Period Validation View ──

    def _draw_period(self, mx):
        row = 0
        self._pad_addstr(row, 0, "  PERIOD CONFIGURATION COMPARISON",
                        self._color(C_CYAN, bold=True))
        row += 2

        comparisons = self._compare_periods()
        if not comparisons:
            self._pad_addstr(row, 4, "Waiting for period data from all zones...",
                            self._color(C_DIM))
            row += 1
            self.pad_lines = row
            return

        zone_names = [z.config.name for z in self.zones]
        field_w = 22
        val_w = max(20, (mx - field_w - 12) // len(zone_names))

        hdr = f"  {'Field':<{field_w}}"
        for zn in zone_names:
            hdr += f"  {zn:<{val_w}}"
        hdr += f"  {'Status':<10}"
        self._pad_addstr(row, 0, hdr[:mx], curses.A_BOLD)
        row += 1
        self._pad_addstr(row, 0, "  " + "-" * min(mx - 4, field_w + val_w * len(zone_names) + 16))
        row += 1

        for comp in comparisons:
            field_name = comp["field"]
            values = comp["values"]
            match = comp["match"]

            self._pad_addstr(row, 2, f"{field_name:<{field_w}}")
            col = 2 + field_w + 2
            for v in values:
                display_v = str(v)
                if len(display_v) > val_w - 2:
                    display_v = display_v[:val_w - 4] + ".."
                self._pad_addstr(row, col, f"{display_v:<{val_w}}")
                col += val_w + 2

            if match:
                self._pad_addstr(row, col, "MATCH", self._color(C_OK, bold=True))
            else:
                self._pad_addstr(row, col, "MISMATCH", self._color(C_ERR, bold=True))
            row += 1

        # Zone endpoints section
        row += 1
        self._pad_addstr(row, 0, "  ZONE ENDPOINTS",
                        self._color(C_CYAN, bold=True))
        row += 1
        self._pad_addstr(row, 0, "  " + "-" * min(mx - 4, 60))
        row += 1

        for z in self.zones:
            with z.lock:
                endpoints = z.zone_endpoints()
                ep_str = ", ".join(endpoints) if endpoints else "none"
            self._pad_addstr(row, 4, f"{z.config.name}: {ep_str}")
            row += 1

        # Enabled features section
        row += 1
        self._pad_addstr(row, 0, "  ENABLED FEATURES",
                        self._color(C_CYAN, bold=True))
        row += 1
        self._pad_addstr(row, 0, "  " + "-" * min(mx - 4, 60))
        row += 1

        features_by_zone = []
        for z in self.zones:
            with z.lock:
                features_by_zone.append((z.config.name, z.enabled_features()))

        all_features = set()
        for _, feats in features_by_zone:
            all_features.update(feats)

        if not all_features:
            self._pad_addstr(row, 4, "No features enabled", self._color(C_DIM))
            row += 1
        else:
            for feat in sorted(all_features):
                zones_with = [name for name, feats in features_by_zone if feat in feats]
                zones_without = [name for name, feats in features_by_zone
                                 if feat not in feats]
                if zones_without:
                    self._pad_addstr(row, 4, f"{feat}: ",)
                    self._pad_addstr(row, 4 + len(feat) + 2,
                                    f"enabled on {', '.join(zones_with)}, "
                                    f"MISSING on {', '.join(zones_without)}",
                                    self._color(C_ERR))
                else:
                    self._pad_addstr(row, 4, f"{feat}: enabled on all zones",
                                    self._color(C_OK))
                row += 1

        self.pad_lines = row

    def _compare_periods(self):
        zone_data = []
        for z in self.zones:
            with z.lock:
                if not z.period_data:
                    return []
                zone_data.append(z.period_data.copy())

        comparisons = []
        fields = [
            ("Period ID", "id"),
            ("Epoch", "epoch"),
            ("Realm ID", "realm_id"),
            ("Realm Name", "realm_name"),
            ("Realm Epoch", "realm_epoch"),
            ("Master Zone", "master_zone"),
            ("Master Zonegroup", "master_zonegroup"),
        ]

        for field_name, json_key in fields:
            values = []
            for pd in zone_data:
                v = pd.get(json_key, "N/A")
                if json_key == "master_zone":
                    master_id = str(v)
                    pm = pd.get("period_map", {})
                    for zg in pm.get("zonegroups", []):
                        for zz in zg.get("zones", []):
                            if zz.get("id") == master_id:
                                v = f"{zz.get('name', '?')} ({master_id[:8]}..)"
                                break
                values.append(str(v))

            raw_values = [pd.get(json_key, "N/A") for pd in zone_data]
            match = len(set(str(rv) for rv in raw_values)) == 1
            comparisons.append({"field": field_name, "values": values, "match": match})

        return comparisons

    # ── Sync Errors View ──

    def _draw_errors(self, mx):
        row = 0
        self._pad_addstr(row, 0, "  SYNC ERRORS", self._color(C_CYAN, bold=True))
        row += 2

        for zone in self.zones:
            with zone.lock:
                name = zone.config.name
                shards = list(zone.sync_errors or [])

            all_entries = []
            for shard in shards:
                shard_id = shard.get("shard_id", "?")
                for entry in shard.get("entries", []):
                    entry["_shard_id"] = shard_id
                    all_entries.append(entry)

            header = f"  {name} ({len(all_entries)} errors):"
            self._pad_addstr(row, 0, header, self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * min(mx - 4, 60))
            row += 1

            if not all_entries:
                self._pad_addstr(row, 4, "No sync errors",
                                self._color(C_OK))
                row += 2
                continue

            for i, entry in enumerate(all_entries[:50]):
                raw_name = entry.get("name", "")
                bucket_name = raw_name.split(":")[0] if raw_name else "N/A"

                self._pad_addstr(row, 4,
                                f"{i+1}. Bucket: {bucket_name}",
                                self._color(C_ERR))
                row += 1

                info = entry.get("info", {})
                err_code = info.get("error_code", "")
                err_msg = info.get("message", "")
                if err_code:
                    self._pad_addstr(row, 7, f"Error: {err_code} - {err_msg}")
                elif err_msg:
                    self._pad_addstr(row, 7, f"Error: {err_msg}")
                row += 1

                source = info.get("source_zone", "")
                if source:
                    self._pad_addstr(row, 7, f"Source zone: {source[:12]}..")
                    row += 1

                ts = entry.get("timestamp", "")
                if ts:
                    self._pad_addstr(row, 7, f"Time: {ts}", self._color(C_DIM))
                    row += 1

                row += 1

            if len(all_entries) > 50:
                self._pad_addstr(row, 4,
                                f"... and {len(all_entries) - 50} more errors",
                                self._color(C_WARN))
                row += 1

        self.pad_lines = row

    # ── Bucket Detail View ──

    def _draw_bucket_detail(self, mx):
        row = 0
        buckets = self._merged_bucket_list()

        if not buckets:
            self._pad_addstr(row, 4, "No buckets", self._color(C_DIM))
            self.pad_lines = 1
            return

        self.selected_bucket = max(0, min(self.selected_bucket, len(buckets) - 1))
        bname = buckets[self.selected_bucket]

        self._pad_addstr(row, 0, f"  BUCKET: {bname}   Sync Detail",
                        self._color(C_CYAN, bold=True))
        row += 2

        for zone in self.zones:
            with zone.lock:
                zname = zone.config.name
                bsync = zone.bucket_sync.get(bname)

            self._pad_addstr(row, 2, f"Zone: {zname}",
                            self._color(C_CYAN, bold=True))
            row += 1

            if not bsync or not bsync.get("sources"):
                self._pad_addstr(row, 4, "No sync data available",
                                self._color(C_DIM))
                row += 2
                continue

            for src in bsync["sources"]:
                src_name = src.get("source_name", "unknown")
                self._pad_addstr(row, 4, f"Source: {src_name} -> {zname}",
                                curses.A_BOLD)
                row += 1

                full_line = f"Full sync:  {src['full_synced']}/{src['full_total']} shards"
                self._pad_addstr(row, 6, full_line)
                row += 1

                inc_line = f"Inc sync:   {src['inc_synced']}/{src['inc_total']} shards"
                self._pad_addstr(row, 6, inc_line)
                row += 1

                if src.get("caught_up"):
                    self._pad_addstr(row, 6, "Status: caught up with source",
                                    self._color(C_OK))
                elif src.get("behind_shards", 0) > 0:
                    self._pad_addstr(row, 6,
                                    f"Status: behind on {src['behind_shards']} shards",
                                    self._color(C_WARN))
                else:
                    self._pad_addstr(row, 6, "Status: syncing",
                                    self._color(C_WARN))
                row += 2

        self.pad_lines = row

    # ── Keyboard ──

    def _handle_key(self, key):
        if key in (ord("q"), ord("Q")):
            self.running = False
        elif key in (ord("r"), ord("R")):
            self.collector.force_refresh()
        elif key == 27:
            if self.view_mode == "bucket_detail":
                self.view_mode = "buckets"
            elif self.view_mode != "overview":
                self.view_mode = "overview"
            self.scroll_offset = 0
        elif key in (ord("b"), ord("B")) and self.view_mode != "bucket_detail":
            self.view_mode = "buckets"
            self.scroll_offset = 0
        elif key in (ord("e"), ord("E")) and self.view_mode != "bucket_detail":
            self.view_mode = "errors"
            self.scroll_offset = 0
        elif key in (ord("p"), ord("P")) and self.view_mode != "bucket_detail":
            self.view_mode = "period"
            self.scroll_offset = 0
        elif key == curses.KEY_UP:
            if self.view_mode == "buckets":
                self.selected_bucket = max(0, self.selected_bucket - 1)
            else:
                self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == curses.KEY_DOWN:
            if self.view_mode == "buckets":
                max_idx = max(0, len(self._merged_bucket_list()) - 1)
                self.selected_bucket = min(max_idx, self.selected_bucket + 1)
            else:
                self.scroll_offset += 1
        elif key in (10, 13, curses.KEY_ENTER):
            if self.view_mode == "buckets":
                self.view_mode = "bucket_detail"
                self.scroll_offset = 0
        elif key == curses.KEY_PPAGE:
            my = self.stdscr.getmaxyx()[0]
            self.scroll_offset = max(0, self.scroll_offset - (my - 4))
        elif key == curses.KEY_NPAGE:
            my = self.stdscr.getmaxyx()[0]
            self.scroll_offset += (my - 4)
        elif key == curses.KEY_RESIZE:
            self.pad = None
            self.stdscr.clear()


def load_config(path):
    with open(path) as f:
        data = json.load(f)

    defaults = data.get("defaults", {})
    zones_raw = data.get("zones", [])
    if not zones_raw:
        print("Error: No zones defined in config file.", file=sys.stderr)
        sys.exit(1)

    realm = data.get("realm", "default")
    zonegroup = data.get("zonegroup", "default")

    configs = []
    for z in zones_raw:
        if "name" not in z or "ssh_host" not in z:
            print(f"Error: Each zone needs 'name' and 'ssh_host'. Got: {z}",
                  file=sys.stderr)
            sys.exit(1)
        configs.append(ZoneConfig(
            name=z["name"],
            ssh_host=z["ssh_host"],
            ssh_user=z.get("ssh_user", defaults.get("ssh_user", "root")),
            ssh_port=z.get("ssh_port", defaults.get("ssh_port", 22)),
            ssh_key=z.get("ssh_key", defaults.get("ssh_key")),
            is_master=z.get("is_master", False),
        ))
    return realm, zonegroup, configs


def main():
    parser = argparse.ArgumentParser(
        description="RGW Multisite Sync Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
                %(prog)s                              # Use ./multisite.json, 30s refresh
                %(prog)s --config /etc/multisite.json
                %(prog)s --interval 10

            Keyboard:
                q          Quit
                r          Force refresh
                b          Bucket sync view
                e          Error list view
                p          Period comparison view
                Esc        Back to overview
                Up/Down    Scroll / select bucket
                Enter      Bucket detail (in bucket view)
                PgUp/PgDn  Page scroll
        """)
    )
    parser.add_argument("--config", "-c", default=None,
                        help="Path to multisite.json")
    parser.add_argument("--interval", "-i", type=int, default=30,
                        help="Main refresh interval in seconds (default: 30)")
    parser.add_argument("--bucket-interval", type=int, default=60,
                        help="Bucket sync refresh interval in seconds (default: 60)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colors")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    config_path = args.config
    if not config_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "multisite.json")
        if not os.path.exists(config_path):
            config_path = os.path.join(os.getcwd(), "multisite.json")

    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        print("Create a multisite.json with your zone definitions.", file=sys.stderr)
        print('Example: {"realm": "movies", "zonegroup": "us", "zones": ['
              '{"name": "us-east", "ssh_host": "ceph12", "is_master": true}]}',
              file=sys.stderr)
        sys.exit(1)

    try:
        realm, zonegroup, configs = load_config(config_path)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    locale.setlocale(locale.LC_ALL, "")

    zones = [ZoneState(c) for c in configs]
    dashboard = RGWSyncDashboard(
        zones, realm, zonegroup,
        interval=args.interval,
        bucket_interval=args.bucket_interval,
        no_color=args.no_color,
    )

    def sig_handler(signum, frame):
        dashboard.running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    curses.wrapper(dashboard.run)


if __name__ == "__main__":
    main()
