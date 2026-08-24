#!/usr/bin/env python3
"""Multi-cluster Ceph terminal dashboard with htop-style interface."""

import argparse
import curses
import json
import locale
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor


VERSION = "1.0"
MARKER = "===CEPH_CMD_SEP==="
SSH_TIMEOUT = 15
MIN_WIDTH = 80
MIN_HEIGHT = 24


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


def format_rate(bps):
    if bps is None or bps < 0:
        return "0 B/s"
    return format_bytes(bps) + "/s"


def format_ops(ops):
    if ops is None or ops < 0:
        return "0"
    if ops >= 1000000:
        return f"{ops/1000000:.1f}M"
    if ops >= 1000:
        return f"{ops/1000:.1f}k"
    return str(int(ops))


def format_ago(ts):
    if ts is None:
        return "never"
    delta = time.time() - ts
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta/60)}m{int(delta%60)}s ago"
    return f"{int(delta/3600)}h{int((delta%3600)/60)}m ago"


class ClusterConfig:
    def __init__(self, name, ssh_host, ssh_user="root", ssh_port=22,
                 ssh_key=None, ceph_conf=None, keyring=None):
        self.name = name
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.ssh_key = ssh_key
        self.ceph_conf = ceph_conf
        self.keyring = keyring

    def ssh_cmd(self, remote_command):
        cmd = [
            "ssh",
            "-o", "ConnectTimeout=10",
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

    def ceph_opts(self):
        opts = ""
        if self.ceph_conf:
            opts += f" -c {self.ceph_conf}"
        if self.keyring:
            opts += f" -k {self.keyring}"
        return opts


class ClusterState:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.status_data = None
        self.df_data = None
        self.osd_tree_data = None
        self.osd_perf_data = None
        self.health_detail = None
        self.time_sync_data = None
        self.version_data = None
        self.last_update = None
        self.last_attempt = None
        self.error = None
        self.reachable = False
        self.collecting = False

    def health_status(self):
        if not self.reachable:
            return "UNREACHABLE"
        if self.status_data is None:
            return "UNKNOWN"
        health = self.status_data.get("health", {})
        return health.get("status", "UNKNOWN")

    def mon_summary(self):
        if not self.status_data:
            return {"total": 0, "in_quorum": 0, "names": []}
        monmap = self.status_data.get("monmap", {})
        total = monmap.get("num_mons", len(monmap.get("mons", [])))
        quorum_names = self.status_data.get("quorum_names", [])
        return {"total": total, "in_quorum": len(quorum_names), "names": quorum_names}

    def osd_summary(self):
        if not self.status_data:
            return {"total": 0, "up": 0, "in": 0, "down": 0}
        osdmap = self.status_data.get("osdmap", {})
        if "osdmap" in osdmap:
            osdmap = osdmap["osdmap"]
        total = osdmap.get("num_osds", 0)
        up = osdmap.get("num_up_osds", 0)
        in_count = osdmap.get("num_in_osds", 0)
        return {"total": total, "up": up, "in": in_count, "down": total - up}

    def pg_summary(self):
        if not self.status_data:
            return {"total": 0, "by_state": []}
        pgmap = self.status_data.get("pgmap", {})
        total = pgmap.get("num_pgs", 0)
        by_state = pgmap.get("pgs_by_state", [])
        return {"total": total, "by_state": by_state}

    def io_summary(self):
        if not self.status_data:
            return {"read_bps": 0, "write_bps": 0, "read_ops": 0, "write_ops": 0}
        pgmap = self.status_data.get("pgmap", {})
        return {
            "read_bps": pgmap.get("read_bytes_sec", 0),
            "write_bps": pgmap.get("write_bytes_sec", 0),
            "read_ops": pgmap.get("read_op_per_sec", 0),
            "write_ops": pgmap.get("write_op_per_sec", 0),
        }

    def capacity(self):
        if self.df_data:
            stats = self.df_data.get("stats", {})
            return {
                "total": stats.get("total_bytes", 0),
                "used": stats.get("total_used_bytes", stats.get("total_used_raw_bytes", 0)),
                "avail": stats.get("total_avail_bytes", 0),
            }
        if self.status_data:
            pgmap = self.status_data.get("pgmap", {})
            return {
                "total": pgmap.get("bytes_total", 0),
                "used": pgmap.get("bytes_used", 0),
                "avail": pgmap.get("bytes_avail", 0),
            }
        return {"total": 0, "used": 0, "avail": 0}

    def usage_pct(self):
        cap = self.capacity()
        if cap["total"] == 0:
            return 0.0
        return (cap["used"] / cap["total"]) * 100.0

    def pools(self):
        if not self.df_data:
            return []
        return self.df_data.get("pools", [])

    def osd_nodes(self):
        if not self.osd_tree_data:
            return []
        nodes = self.osd_tree_data.get("nodes", [])
        host_map = {}
        for n in nodes:
            if n.get("type") == "host":
                for child_id in n.get("children", []):
                    host_map[child_id] = n.get("name", "?")
        osds = []
        for n in nodes:
            if n.get("type") == "osd":
                n["host"] = host_map.get(n.get("id"), "?")
                osds.append(n)
        return sorted(osds, key=lambda x: x.get("id", 0))

    def osd_latencies(self):
        if not self.osd_perf_data:
            return {}
        lat = {}
        infos = self.osd_perf_data.get("osd_perf_infos",
                    self.osd_perf_data.get("osd_perf", []))
        for entry in infos:
            osd_id = entry.get("id")
            perf = entry.get("perf_stats", {})
            lat[osd_id] = {
                "commit_ms": perf.get("commit_latency_ms", 0),
                "apply_ms": perf.get("apply_latency_ms", 0),
            }
        return lat

    def alert_lines(self):
        if not self.health_detail:
            return []
        lines = []
        for line in self.health_detail.strip().splitlines():
            line = line.strip()
            if line and line != "HEALTH_OK":
                lines.append(line)
        return lines

    def ceph_version(self):
        if not self.version_data:
            return ""
        ver = self.version_data.get("version", "")
        if ver:
            return ver
        return ""

    def time_sync_issues(self):
        if not self.time_sync_data:
            return []
        issues = []
        skew_status = self.time_sync_data.get("time_skew_status", {})
        for mon, info in skew_status.items():
            if info.get("health") != "HEALTH_OK":
                skew = info.get("skew", 0)
                issues.append(f"{mon}: skew {skew:.3f}s")
        return issues


class DataCollector:
    def __init__(self, clusters, interval=30):
        self.clusters = clusters
        self.interval = interval
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=min(16, len(clusters) * 2))
        self._timer = None

    def _run_ssh(self, config, command):
        try:
            result = subprocess.run(
                config.ssh_cmd(command),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=SSH_TIMEOUT
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

    def _collect_one(self, cluster):
        cluster.collecting = True
        cluster.last_attempt = time.time()
        config = cluster.config
        opts = config.ceph_opts()

        commands = [
            f"ceph{opts} status --format json 2>/dev/null",
            f"ceph{opts} df --format json 2>/dev/null",
            f"ceph{opts} osd tree --format json 2>/dev/null",
            f"ceph{opts} osd perf --format json 2>/dev/null",
            f"ceph{opts} health detail 2>/dev/null",
            f"ceph{opts} time-sync-status --format json 2>/dev/null",
            f"ceph{opts} version --format json 2>/dev/null",
        ]
        batch = f" ; echo '{MARKER}' ; ".join(commands)

        ok, output = self._run_ssh(config, batch)
        with cluster.lock:
            if not ok:
                cluster.error = output
                cluster.reachable = False
                cluster.collecting = False
                return

            cluster.reachable = True
            cluster.error = None
            parts = output.split(MARKER)

            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                if i == 4:
                    cluster.health_detail = part
                    continue
                try:
                    data = json.loads(part)
                    if i == 0:
                        cluster.status_data = data
                    elif i == 1:
                        cluster.df_data = data
                    elif i == 2:
                        cluster.osd_tree_data = data
                    elif i == 3:
                        cluster.osd_perf_data = data
                    elif i == 5:
                        cluster.time_sync_data = data
                    elif i == 6:
                        cluster.version_data = data
                except json.JSONDecodeError:
                    pass

            cluster.last_update = time.time()
            cluster.collecting = False

    def collect_all(self):
        futures = []
        for cluster in self.clusters:
            if not cluster.collecting:
                futures.append(self._executor.submit(self._collect_one, cluster))
        for f in futures:
            try:
                f.result(timeout=SSH_TIMEOUT + 5)
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


# Color pair constants
C_OK = 1
C_WARN = 2
C_ERR = 3
C_CYAN = 4
C_BAR = 5
C_SELECTED = 6
C_DIM = 7
C_USAGE_GREEN = 8
C_USAGE_YELLOW = 9
C_USAGE_RED = 10


class CephDashboard:
    def __init__(self, configs, interval=30, no_color=False):
        self.clusters = [ClusterState(c) for c in configs]
        self.collector = DataCollector(self.clusters, interval)
        self.interval = interval
        self.no_color = no_color
        self.view_mode = "overview"
        self.selected = 0
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
            curses.init_pair(C_USAGE_GREEN, curses.COLOR_GREEN, -1)
            curses.init_pair(C_USAGE_YELLOW, curses.COLOR_YELLOW, -1)
            curses.init_pair(C_USAGE_RED, curses.COLOR_RED, -1)

    def _color(self, pair_id, bold=False):
        if self.no_color or not curses.has_colors():
            attr = curses.A_NORMAL
        else:
            attr = curses.color_pair(pair_id)
        if bold:
            attr |= curses.A_BOLD
        return attr

    def _health_color(self, status):
        if status == "HEALTH_OK":
            return self._color(C_OK, bold=True)
        elif status == "HEALTH_WARN":
            return self._color(C_WARN, bold=True)
        elif status in ("HEALTH_ERR", "HEALTH_CRIT"):
            return self._color(C_ERR, bold=True)
        elif status == "UNREACHABLE":
            return self._color(C_ERR)
        return self._color(C_DIM)

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

        pad_h = max(500, len(self.clusters) * 10 + 200)
        if self.pad is None or self.pad.getmaxyx()[0] < pad_h or self.pad.getmaxyx()[1] < mx:
            self.pad = curses.newpad(pad_h, max(mx, 300))
        self.pad.clear()

        self._draw_top_bar(my, mx)
        self._draw_bottom_bar(my, mx)

        self.pad_lines = 0
        if self.view_mode == "overview":
            self._draw_overview(mx)
        else:
            self._draw_detail(mx)

        content_h = my - 2
        max_scroll = max(0, self.pad_lines - content_h)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        try:
            self.pad.refresh(self.scroll_offset, 0, 1, 0, my - 2, mx - 1)
        except curses.error:
            pass
        self.stdscr.refresh()

    def _draw_top_bar(self, my, mx):
        collecting = any(c.collecting for c in self.clusters)
        ok_count = sum(1 for c in self.clusters if c.health_status() == "HEALTH_OK")
        warn_count = sum(1 for c in self.clusters if c.health_status() == "HEALTH_WARN")
        err_count = sum(1 for c in self.clusters
                       if c.health_status() in ("HEALTH_ERR", "HEALTH_CRIT"))
        unreach = sum(1 for c in self.clusters if c.health_status() == "UNREACHABLE")

        ts = time.strftime("%H:%M:%S")
        left = f" CEPH DASHBOARD | Clusters: {len(self.clusters)}"
        if ok_count:
            left += f" | OK:{ok_count}"
        if warn_count:
            left += f" | WARN:{warn_count}"
        if err_count:
            left += f" | ERR:{err_count}"
        if unreach:
            left += f" | DOWN:{unreach}"

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
        if self.view_mode == "overview":
            keys = " q:Quit  r:Refresh  Up/Down:Select  Enter:Detail  1-9:Jump "
        else:
            keys = " q:Quit  r:Refresh  Esc:Back  Up/Down:Scroll  PgUp/PgDn:Page "

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

    def _draw_overview(self, mx):
        row = 0
        for i, cluster in enumerate(self.clusters):
            with cluster.lock:
                row = self._draw_cluster_summary(row, mx, i, cluster)
            self.pad_lines = row

    def _draw_cluster_summary(self, row, mx, idx, cluster):
        status = cluster.health_status()
        name = cluster.config.name
        is_selected = (idx == self.selected)

        ts_str = ""
        if cluster.last_update:
            ts_str = time.strftime("%H:%M:%S", time.localtime(cluster.last_update))

        header_attr = self._color(C_SELECTED) if is_selected else self._color(C_CYAN, bold=True)
        status_attr = self._health_color(status)

        ver = cluster.ceph_version()
        ver_short = ""
        if ver:
            parts = ver.split()
            if len(parts) >= 3:
                ver_short = f" [{parts[2]}]" if parts[2] != "version" else f" [{parts[0]}]"
            else:
                ver_short = f" [{ver}]"

        name_part = f" {idx+1}. {name}{ver_short} "
        status_part = f" {status} "
        ts_part = f" ({ts_str})" if ts_str else ""
        fill_len = max(1, mx - len(name_part) - len(status_part) - len(ts_part) - 4)
        header_line = f"--{name_part}" + "-" * fill_len

        self._pad_addstr(row, 0, header_line, header_attr)
        self._pad_addstr(row, len(header_line), status_part, status_attr)
        if ts_part:
            self._pad_addstr(row, len(header_line) + len(status_part), ts_part,
                            self._color(C_DIM))
        row += 1

        if status == "UNREACHABLE":
            self._pad_addstr(row, 2, f"Error: {cluster.error or 'Unknown'}",
                            self._color(C_ERR))
            row += 1
            if cluster.last_update:
                self._pad_addstr(row, 2,
                                f"Last successful: {format_ago(cluster.last_update)}",
                                self._color(C_DIM))
                row += 1
            row += 1
            return row

        mons = cluster.mon_summary()
        osds = cluster.osd_summary()
        pgs = cluster.pg_summary()

        mon_str = f"MONs: {mons['in_quorum']}/{mons['total']}"
        osd_str = f"OSDs: {osds['up']} up, {osds['in']} in"
        if osds["down"] > 0:
            osd_str += f" ({osds['down']} DOWN)"

        active_clean = 0
        pg_issues = []
        for ps in pgs["by_state"]:
            sname = ps.get("state_name", "")
            count = ps.get("count", 0)
            if sname == "active+clean":
                active_clean = count
            elif count > 0:
                pg_issues.append(f"{count} {sname}")
        pg_str = f"PGs: {active_clean} active+clean"
        if pg_issues:
            pg_str += ", " + ", ".join(pg_issues[:2])

        line2 = f"  {mon_str}    {osd_str}    {pg_str}"
        self._pad_addstr(row, 0, line2[:mx])
        if osds["down"] > 0:
            down_start = line2.find("DOWN")
            if down_start >= 0:
                self._pad_addstr(row, down_start, "DOWN", self._color(C_ERR, bold=True))
        row += 1

        cap = cluster.capacity()
        pct = cluster.usage_pct()
        bar_width = min(30, mx - 35)
        filled = int(bar_width * pct / 100)
        empty = bar_width - filled

        if pct < 70:
            bar_color = self._color(C_USAGE_GREEN)
        elif pct < 85:
            bar_color = self._color(C_USAGE_YELLOW)
        else:
            bar_color = self._color(C_USAGE_RED)

        self._pad_addstr(row, 2, "[")
        self._pad_addstr(row, 3, "█" * filled, bar_color)
        self._pad_addstr(row, 3 + filled, "░" * empty)
        bar_end = 3 + bar_width + 1
        cap_str = f"] {pct:.1f}%  {format_bytes(cap['used'])}/{format_bytes(cap['total'])}"
        self._pad_addstr(row, bar_end, cap_str)
        row += 1

        io = cluster.io_summary()
        io_str = (f"  IO: R {format_rate(io['read_bps'])}  W {format_rate(io['write_bps'])}"
                 f"  |  Ops: R {format_ops(io['read_ops'])}  W {format_ops(io['write_ops'])}")
        self._pad_addstr(row, 0, io_str[:mx])
        row += 1

        alerts = cluster.alert_lines()
        if alerts:
            alert_count = len(alerts)
            first_alert = alerts[0][:mx-12] if alerts else ""
            if alert_count == 1:
                alert_str = f"  Alert: {first_alert}"
            else:
                alert_str = f"  Alerts ({alert_count}): {first_alert}"
            warn_attr = self._color(C_WARN) if "WARN" in status else self._color(C_ERR)
            self._pad_addstr(row, 0, alert_str[:mx], warn_attr)
        else:
            self._pad_addstr(row, 2, "No alerts", self._color(C_OK))
        row += 1

        sync_issues = cluster.time_sync_issues()
        if sync_issues:
            self._pad_addstr(row, 2, f"Clock skew: {', '.join(sync_issues)}",
                            self._color(C_WARN))
            row += 1

        row += 1
        return row

    def _draw_detail(self, mx):
        if self.selected >= len(self.clusters):
            return
        cluster = self.clusters[self.selected]
        row = 0

        with cluster.lock:
            name = cluster.config.name
            host = cluster.config.ssh_host
            status = cluster.health_status()

            header = f"  {name}  "
            fill = max(1, mx - len(header) - len(status) - 8)
            self._pad_addstr(row, 0, "==" + header + "=" * fill,
                            self._color(C_CYAN, bold=True))
            self._pad_addstr(row, len("==" + header) + fill + 1, f" {status} ",
                            self._health_color(status))
            row += 1

            ts_str = time.strftime("%H:%M:%S", time.localtime(cluster.last_update)) \
                     if cluster.last_update else "never"
            ver = cluster.ceph_version()
            info_line = f"Host: {host}  |  Last update: {ts_str}"
            if ver:
                info_line += f"  |  Ceph: {ver}"
            self._pad_addstr(row, 2, info_line, self._color(C_DIM))
            row += 2

            if status == "UNREACHABLE":
                self._pad_addstr(row, 2, f"Error: {cluster.error or 'Unknown'}",
                                self._color(C_ERR))
                row += 1
                self.pad_lines = row
                return

            # Health checks
            alerts = cluster.alert_lines()
            self._pad_addstr(row, 0, "  HEALTH CHECKS", self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
            row += 1
            if alerts:
                for alert in alerts:
                    if "HEALTH_WARN" in alert:
                        attr = self._color(C_WARN)
                    elif "HEALTH_ERR" in alert or "HEALTH_CRIT" in alert:
                        attr = self._color(C_ERR)
                    else:
                        attr = curses.A_NORMAL
                    self._pad_addstr(row, 4, alert[:mx-6], attr)
                    row += 1
            else:
                self._pad_addstr(row, 4, "HEALTH_OK - No issues", self._color(C_OK))
                row += 1
            row += 1

            # Monitor status
            mons = cluster.mon_summary()
            self._pad_addstr(row, 0, "  MONITORS", self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
            row += 1
            mon_attr = self._color(C_OK) if mons["in_quorum"] == mons["total"] \
                      else self._color(C_WARN)
            self._pad_addstr(row, 4,
                            f"{mons['in_quorum']}/{mons['total']} in quorum: "
                            f"{', '.join(mons['names'])}",
                            mon_attr)
            row += 1

            sync_issues = cluster.time_sync_issues()
            if sync_issues:
                for issue in sync_issues:
                    self._pad_addstr(row, 4, f"Clock skew: {issue}",
                                    self._color(C_WARN))
                    row += 1
            row += 1

            # Capacity
            cap = cluster.capacity()
            pct = cluster.usage_pct()
            self._pad_addstr(row, 0, "  CAPACITY", self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
            row += 1
            self._pad_addstr(row, 4,
                            f"Total: {format_bytes(cap['total'])}  "
                            f"Used: {format_bytes(cap['used'])} ({pct:.1f}%)  "
                            f"Avail: {format_bytes(cap['avail'])}")
            row += 1

            bar_width = min(50, mx - 10)
            filled = int(bar_width * pct / 100)
            empty = bar_width - filled
            if pct < 70:
                bar_color = self._color(C_USAGE_GREEN)
            elif pct < 85:
                bar_color = self._color(C_USAGE_YELLOW)
            else:
                bar_color = self._color(C_USAGE_RED)
            self._pad_addstr(row, 4, "[")
            self._pad_addstr(row, 5, "█" * filled, bar_color)
            self._pad_addstr(row, 5 + filled, "░" * empty)
            self._pad_addstr(row, 5 + bar_width, f"] {pct:.1f}%")
            row += 2

            # Pool table
            pools = cluster.pools()
            if pools:
                self._pad_addstr(row, 0, "  POOLS", self._color(C_CYAN, bold=True))
                row += 1
                self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
                row += 1
                hdr = f"    {'Pool':<25} {'Used':>10} {'%Used':>7} {'Max Avail':>12} {'Objects':>10}"
                self._pad_addstr(row, 0, hdr[:mx], curses.A_BOLD)
                row += 1
                for pool in pools:
                    pname = pool.get("name", "?")
                    pstats = pool.get("stats", {})
                    pused = pstats.get("bytes_used", pstats.get("stored", 0))
                    ppct = pstats.get("percent_used", 0) * 100
                    pavail = pstats.get("max_avail", 0)
                    pobjs = pstats.get("objects", 0)

                    pct_attr = curses.A_NORMAL
                    if ppct > 85:
                        pct_attr = self._color(C_ERR)
                    elif ppct > 70:
                        pct_attr = self._color(C_WARN)

                    line = f"    {pname:<25} {format_bytes(pused):>10} "
                    self._pad_addstr(row, 0, line[:mx])
                    pct_str = f"{ppct:>6.1f}%"
                    self._pad_addstr(row, len(line), pct_str, pct_attr)
                    rest = f" {format_bytes(pavail):>12} {pobjs:>10}"
                    self._pad_addstr(row, len(line) + len(pct_str), rest[:mx - len(line) - len(pct_str)])
                    row += 1
                row += 1

            # OSD table
            osds_summary = cluster.osd_summary()
            osd_nodes = cluster.osd_nodes()
            latencies = cluster.osd_latencies()
            self._pad_addstr(row, 0, "  OSDs", self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
            row += 1
            self._pad_addstr(row, 4,
                            f"Total: {osds_summary['total']}  "
                            f"Up: {osds_summary['up']}  "
                            f"In: {osds_summary['in']}  "
                            f"Down: {osds_summary['down']}")
            row += 1

            if osd_nodes:
                hdr = (f"    {'OSD':<8} {'Status':<8} {'Weight':>7} "
                       f"{'Reweight':>9} {'Apply(ms)':>10} {'Commit(ms)':>11} {'Host':<15}")
                self._pad_addstr(row, 0, hdr[:mx], curses.A_BOLD)
                row += 1

                for osd in osd_nodes:
                    osd_id = osd.get("id", 0)
                    osd_name = osd.get("name", f"osd.{osd_id}")
                    osd_status = osd.get("status", "unknown")
                    weight = osd.get("crush_weight", 0)
                    reweight = osd.get("reweight", 0)
                    host = osd.get("host", "?")
                    lat = latencies.get(osd_id, {})
                    apply_ms = lat.get("apply_ms", "-")
                    commit_ms = lat.get("commit_ms", "-")

                    if osd_status == "up":
                        status_str = "up"
                        status_attr = self._color(C_OK)
                    elif osd_status == "down":
                        status_str = "DOWN"
                        status_attr = self._color(C_ERR, bold=True)
                    else:
                        status_str = osd_status
                        status_attr = self._color(C_WARN)

                    apply_str = str(apply_ms) if apply_ms != "-" else "-"
                    commit_str = str(commit_ms) if commit_ms != "-" else "-"

                    lat_attr = curses.A_NORMAL
                    if isinstance(apply_ms, (int, float)) and apply_ms > 100:
                        lat_attr = self._color(C_ERR)
                    elif isinstance(apply_ms, (int, float)) and apply_ms > 20:
                        lat_attr = self._color(C_WARN)

                    line_prefix = f"    {osd_name:<8} "
                    self._pad_addstr(row, 0, line_prefix)
                    self._pad_addstr(row, len(line_prefix), f"{status_str:<8}", status_attr)
                    rest = (f"{weight:>7.3f} {reweight:>9.3f} "
                           f"{apply_str:>10} {commit_str:>11} {host:<15}")
                    col_after_status = len(line_prefix) + 8
                    self._pad_addstr(row, col_after_status, rest[:mx - col_after_status])
                    if lat_attr != curses.A_NORMAL:
                        apply_col = col_after_status + 18
                        self._pad_addstr(row, apply_col,
                                        f"{apply_str:>10}", lat_attr)
                    row += 1
                row += 1

            # PG states
            pgs = cluster.pg_summary()
            self._pad_addstr(row, 0, "  PG STATES", self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
            row += 1
            self._pad_addstr(row, 4, f"Total PGs: {pgs['total']}")
            row += 1
            for ps in sorted(pgs["by_state"], key=lambda x: -x.get("count", 0)):
                sname = ps.get("state_name", "?")
                count = ps.get("count", 0)
                if pgs["total"] > 0:
                    spct = count / pgs["total"] * 100
                else:
                    spct = 0

                pg_attr = curses.A_NORMAL
                if sname == "active+clean":
                    pg_attr = self._color(C_OK)
                elif "degraded" in sname or "down" in sname or "incomplete" in sname:
                    pg_attr = self._color(C_ERR)
                elif sname != "active+clean":
                    pg_attr = self._color(C_WARN)

                self._pad_addstr(row, 4,
                                f"{sname:<35} {count:>6} ({spct:>5.1f}%)", pg_attr)
                row += 1
            row += 1

            # IO
            io = cluster.io_summary()
            self._pad_addstr(row, 0, "  IO", self._color(C_CYAN, bold=True))
            row += 1
            self._pad_addstr(row, 0, "  " + "-" * (mx - 4))
            row += 1
            self._pad_addstr(row, 4,
                            f"Read:  {format_rate(io['read_bps']):>12}  "
                            f"({format_ops(io['read_ops'])} ops/s)")
            row += 1
            self._pad_addstr(row, 4,
                            f"Write: {format_rate(io['write_bps']):>12}  "
                            f"({format_ops(io['write_ops'])} ops/s)")
            row += 2

        self.pad_lines = row

    def _handle_key(self, key):
        if key in (ord("q"), ord("Q")):
            self.running = False
        elif key in (ord("r"), ord("R")):
            self.collector.force_refresh()
        elif key == curses.KEY_UP:
            if self.view_mode == "overview":
                self.selected = max(0, self.selected - 1)
                self.scroll_offset = 0
            else:
                self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == curses.KEY_DOWN:
            if self.view_mode == "overview":
                self.selected = min(len(self.clusters) - 1, self.selected + 1)
                self.scroll_offset = 0
            else:
                self.scroll_offset += 1
        elif key in (curses.KEY_PPAGE,):
            my = self.stdscr.getmaxyx()[0]
            self.scroll_offset = max(0, self.scroll_offset - (my - 4))
        elif key in (curses.KEY_NPAGE,):
            my = self.stdscr.getmaxyx()[0]
            self.scroll_offset += (my - 4)
        elif key in (10, 13, curses.KEY_ENTER):
            if self.view_mode == "overview":
                self.view_mode = "detail"
                self.scroll_offset = 0
            else:
                self.view_mode = "overview"
                self.scroll_offset = 0
        elif key == 27:
            if self.view_mode == "detail":
                self.view_mode = "overview"
                self.scroll_offset = 0
        elif key == 9:
            if self.view_mode == "overview":
                self.view_mode = "detail"
            else:
                self.view_mode = "overview"
            self.scroll_offset = 0
        elif ord("1") <= key <= ord("9"):
            idx = key - ord("1")
            if idx < len(self.clusters):
                self.selected = idx
                self.view_mode = "detail"
                self.scroll_offset = 0
        elif key == curses.KEY_RESIZE:
            self.pad = None
            self.stdscr.clear()


def load_config(path):
    with open(path) as f:
        data = json.load(f)

    defaults = data.get("defaults", {})
    clusters_raw = data.get("clusters", [])
    if not clusters_raw:
        print("Error: No clusters defined in config file.", file=sys.stderr)
        sys.exit(1)

    configs = []
    for c in clusters_raw:
        if "name" not in c or "ssh_host" not in c:
            print(f"Error: Each cluster needs 'name' and 'ssh_host'. Got: {c}",
                  file=sys.stderr)
            sys.exit(1)
        configs.append(ClusterConfig(
            name=c["name"],
            ssh_host=c["ssh_host"],
            ssh_user=c.get("ssh_user", defaults.get("ssh_user", "root")),
            ssh_port=c.get("ssh_port", defaults.get("ssh_port", 22)),
            ssh_key=c.get("ssh_key", defaults.get("ssh_key")),
            ceph_conf=c.get("ceph_conf", defaults.get("ceph_conf")),
            keyring=c.get("keyring", defaults.get("keyring")),
        ))
    return configs


def main():
    parser = argparse.ArgumentParser(
        description="Ceph Multi-Cluster Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
                %(prog)s                              # Use ./clusters.json, 30s refresh
                %(prog)s --config /etc/clusters.json
                %(prog)s --interval 10

            Keyboard:
                q          Quit
                r          Force refresh
                Up/Down    Select cluster / scroll
                Enter      Toggle detail view
                Esc        Back to overview
                1-9        Jump to cluster N
                PgUp/PgDn  Scroll pages
        """)
    )
    parser.add_argument("--config", "-c", default=None,
                        help="Path to clusters.json")
    parser.add_argument("--interval", "-i", type=int, default=30,
                        help="Refresh interval in seconds (default: 30)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colors")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    config_path = args.config
    if not config_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "clusters.json")
        if not os.path.exists(config_path):
            config_path = os.path.join(os.getcwd(), "clusters.json")

    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        print("Create a clusters.json with your cluster definitions.", file=sys.stderr)
        print('Example: {"clusters": [{"name": "prod", "ssh_host": "ceph-mon01"}]}',
              file=sys.stderr)
        sys.exit(1)

    try:
        configs = load_config(config_path)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    locale.setlocale(locale.LC_ALL, "")

    dashboard = CephDashboard(configs, interval=args.interval, no_color=args.no_color)

    def sig_handler(signum, frame):
        dashboard.running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    curses.wrapper(dashboard.run)


if __name__ == "__main__":
    main()
