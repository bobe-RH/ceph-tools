#!/usr/bin/env python3
"""
Live OSD latency analyzer - processes streaming packet data in real-time.
"""

import sys
import time
import signal
import json
import os
from collections import defaultdict, deque
from statistics import mean, median
from datetime import datetime

class LiveRTTAnalyzer:
    def __init__(self, update_interval=5, osd_map_file='osd_ip_map.json'):
        self.update_interval = update_interval
        self.connections = {}  # connection_id -> {timestamps, last_seen}
        self.rtts = deque(maxlen=10000)  # Keep last 10k RTTs
        self.osd_rtts = defaultdict(lambda: deque(maxlen=1000))
        self.total_packets = 0
        self.total_rtts = 0
        self.last_update = time.time()
        self.running = True

        # Statistics buckets
        self.fast = 0
        self.normal = 0
        self.slow = 0
        self.very_slow = 0
        self.critical = 0

        # Load OSD IP mapping
        self.osd_map = self.load_osd_map(osd_map_file)

        # Build IP->hostname and IP->OSDs mapping
        self.ip_to_hostname = {
            '10.8.223.224': 'ceph501',
            '10.8.223.225': 'ceph502',
            '10.8.223.200': 'ceph503'
        }
        self.ip_to_osds = self.build_ip_to_osds_map()

    def load_osd_map(self, map_file):
        """Load IP to OSD ID mapping from JSON file."""
        if os.path.exists(map_file):
            try:
                with open(map_file, 'r') as f:
                    osd_map = json.load(f)
                print(f"Loaded OSD mapping: {len(osd_map)} entries")
                return osd_map
            except Exception as e:
                print(f"Warning: Could not load OSD map from {map_file}: {e}")
                print("Will display IP addresses instead of OSD IDs")
                return {}
        else:
            print(f"Warning: OSD map file {map_file} not found")
            print("Run './build_osd_map.sh' first to create it")
            print("Will display IP addresses instead of OSD IDs")
            return {}

    def build_ip_to_osds_map(self):
        """Build a mapping of IP -> list of OSD numbers on that IP."""
        ip_osds = {}
        for key, osd_id in self.osd_map.items():
            # Extract IP from key (could be "IP" or "IP:PORT")
            ip = key.split(':')[0]

            # Extract OSD number from "osd.N"
            if osd_id.startswith('osd.'):
                osd_num = int(osd_id.split('.')[1])
                if ip not in ip_osds:
                    ip_osds[ip] = set()
                ip_osds[ip].add(osd_num)

        # Convert sets to sorted lists
        return {ip: sorted(list(osds)) for ip, osds in ip_osds.items()}

    def ip_to_osd(self, ip, port=None):
        """Convert IP or IP:PORT to OSD ID with hostname, or return IP if not found."""
        hostname = self.ip_to_hostname.get(ip, ip)

        # Try IP:PORT first (most specific - exact match)
        if port:
            key = f"{ip}:{port}"
            if key in self.osd_map:
                osd_id = self.osd_map[key]
                return f"{hostname}:{osd_id}"

            # Try adjacent ports (OSDs typically use consecutive ports like 6800,6801)
            for offset in [-1, 1, -2, 2]:
                adjacent_key = f"{ip}:{port + offset}"
                if adjacent_key in self.osd_map:
                    osd_id = self.osd_map[adjacent_key]
                    return f"{hostname}:{osd_id}~{port}"  # ~ indicates approximate port match

        # Fall back to IP only
        if ip in self.ip_to_osds:
            osds = self.ip_to_osds[ip]

            if len(osds) == 1:
                # Single OSD on this IP
                if port:
                    return f"{hostname}:osd.{osds[0]}:{port}"
                return f"{hostname}:osd.{osds[0]}"
            else:
                # Multiple OSDs - show all possibilities with port if available
                osd_list = ','.join(str(n) for n in osds)
                if port:
                    return f"{hostname}[{osd_list}]:{port}"
                return f"{hostname}[{osd_list}]"

        # Completely unknown - return just IP or IP:PORT
        if port:
            return f"{ip}:{port}"
        return ip

    def process_packet(self, frame, timestamp, src, dst, sport, dport, tsval, tsecr):
        """Process a single packet and update statistics."""
        try:
            frame = int(frame)
            timestamp = float(timestamp)
            sport = int(sport)
            dport = int(dport)
            tsval = int(tsval)
            tsecr = int(tsecr)
        except (ValueError, TypeError):
            return

        self.total_packets += 1

        # Create a normalized connection ID (sorted so both directions map to same connection)
        conn_tuple = tuple(sorted([(src, sport), (dst, dport)]))
        conn_id = f"{conn_tuple[0][0]}:{conn_tuple[0][1]}<->{conn_tuple[1][0]}:{conn_tuple[1][1]}"

        # Initialize connection tracking if new
        if conn_id not in self.connections:
            self.connections[conn_id] = {
                'timestamps': {},  # tsval -> (timestamp, direction)
                'last_seen': timestamp
            }

        conn = self.connections[conn_id]
        conn['last_seen'] = timestamp

        # Determine packet direction within this connection
        direction = 'forward' if (src, sport) == conn_tuple[0] else 'reverse'

        # Check if this packet echoes a previous TSval from the SAME connection
        if tsecr != 0 and tsecr in conn['timestamps']:
            orig_time, orig_direction = conn['timestamps'][tsecr]

            # Only calculate RTT if this is echoing the opposite direction
            if orig_direction != direction:
                rtt_ms = (timestamp - orig_time) * 1000

                # Tighter filter: data center RTTs should be < 1000ms typically
                # Also add minimum bound to filter noise
                if 0.01 < rtt_ms < 1000:
                    self.rtts.append(rtt_ms)
                    self.total_rtts += 1

                    # Track by specific connection flow
                    flow_endpoints = sorted([
                        (src, sport, self.ip_to_osd(src, sport)),
                        (dst, dport, self.ip_to_osd(dst, dport))
                    ])
                    flow_key = (
                        f"{flow_endpoints[0][0]}:{flow_endpoints[0][1]}",
                        f"{flow_endpoints[1][0]}:{flow_endpoints[1][1]}",
                        flow_endpoints[0][2],  # OSD label for endpoint 1
                        flow_endpoints[1][2]   # OSD label for endpoint 2
                    )

                    self.osd_rtts[flow_key].append(rtt_ms)

                    # Update delay buckets
                    if rtt_ms < 1:
                        self.fast += 1
                    elif rtt_ms < 10:
                        self.normal += 1
                    elif rtt_ms < 100:
                        self.slow += 1
                    elif rtt_ms < 500:
                        self.very_slow += 1
                    else:
                        self.critical += 1

                # Remove the matched timestamp to prevent reuse
                del conn['timestamps'][tsecr]

        # Store this packet's timestamp for future matching (within this connection only)
        if tsval != 0:
            conn['timestamps'][tsval] = (timestamp, direction)

        # Cleanup: remove stale connections (not seen in 60 seconds)
        current_time = timestamp
        stale_conns = [cid for cid, c in self.connections.items()
                       if current_time - c['last_seen'] > 60]
        for cid in stale_conns:
            del self.connections[cid]

        # Also limit per-connection timestamp cache size
        if len(conn['timestamps']) > 1000:
            # Keep only the most recent 500
            sorted_ts = sorted(conn['timestamps'].items(),
                             key=lambda x: x[1][0], reverse=True)
            conn['timestamps'] = dict(sorted_ts[:500])

    def display_stats(self):
        """Display current statistics."""
        # Clear screen
        print("\033[2J\033[H", end='')

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("=" * 90)
        print(f"Live OSD Latency Monitor - {now}")
        print("=" * 90)
        print(f"Total Packets: {self.total_packets:,}  |  RTT Measurements: {self.total_rtts:,}")
        print()

        if not self.rtts:
            print("Waiting for data...")
            return

        # Overall statistics
        rtts_list = list(self.rtts)
        sorted_rtts = sorted(rtts_list)

        print("Overall Latency Statistics (last 10,000 measurements):")
        print("-" * 80)
        print(f"  Min:    {min(rtts_list):8.3f} ms")
        print(f"  Mean:   {mean(rtts_list):8.3f} ms")
        print(f"  Median: {median(rtts_list):8.3f} ms")
        print(f"  P95:    {sorted_rtts[int(len(sorted_rtts)*0.95)]:8.3f} ms")
        print(f"  P99:    {sorted_rtts[int(len(sorted_rtts)*0.99)]:8.3f} ms")
        print(f"  Max:    {max(rtts_list):8.3f} ms")
        print()

        # Delay distribution
        total = self.fast + self.normal + self.slow + self.very_slow + self.critical
        if total > 0:
            print("Delay Distribution:")
            print("-" * 80)
            print(f"  {'Fast (<1ms):':<25} {self.fast:8,} ({self.fast/total*100:5.1f}%) ✓")
            print(f"  {'Normal (1-10ms):':<25} {self.normal:8,} ({self.normal/total*100:5.1f}%)")
            print(f"  {'Slow (10-100ms):':<25} {self.slow:8,} ({self.slow/total*100:5.1f}%) ⚠")
            print(f"  {'Very Slow (100-500ms):':<25} {self.very_slow:8,} ({self.very_slow/total*100:5.1f}%) ⚠⚠")
            print(f"  {'Critical (>=500ms):':<25} {self.critical:8,} ({self.critical/total*100:5.1f}%) 🔴")
            print()

        # Top problem connections by average latency
        if self.osd_rtts:
            print("Top 10 Connections by Average Latency:")
            print("  Notation: ~PORT=adjacent port match, [N,M]=ambiguous OSDs, :PORT=specific port")
            print("-" * 90)
            print(f"{'Endpoint 1':<30} {'Endpoint 2':<30} {'Samples':<8} {'Avg (ms)':<10} {'Max (ms)'}")
            print("-" * 90)

            flow_stats = []
            for flow_key, rtt_list in self.osd_rtts.items():
                if len(rtt_list) >= 5:
                    # flow_key is (ip:port_1, ip:port_2, osd_label_1, osd_label_2)
                    ip_port_1, ip_port_2, osd_label_1, osd_label_2 = flow_key

                    # Use the enhanced OSD labels which now include hostname and port info
                    # Format can be:
                    #   - hostname:osd.N (exact match)
                    #   - hostname:osd.N~PORT (adjacent port match)
                    #   - hostname:osd.N:PORT (single OSD, specific port)
                    #   - hostname[N,M]:PORT (ambiguous OSDs, specific port)
                    #   - IP:PORT (completely unknown)

                    # If the label contains hostname info, use it; otherwise show raw IP:port
                    if any(h in osd_label_1 for h in self.ip_to_hostname.values()) or 'osd.' in osd_label_1:
                        label_1 = osd_label_1
                    else:
                        label_1 = ip_port_1

                    if any(h in osd_label_2 for h in self.ip_to_hostname.values()) or 'osd.' in osd_label_2:
                        label_2 = osd_label_2
                    else:
                        label_2 = ip_port_2

                    flow_stats.append({
                        'label1': label_1,
                        'label2': label_2,
                        'count': len(rtt_list),
                        'avg': mean(rtt_list),
                        'max': max(rtt_list),
                    })

            flow_stats.sort(key=lambda x: x['avg'], reverse=True)

            for stat in flow_stats[:10]:
                status = "🔴" if stat['avg'] > 100 else "⚠" if stat['avg'] > 10 else ""
                print(f"{stat['label1']:<30} {stat['label2']:<30} {stat['count']:<8} "
                      f"{stat['avg']:<10.3f} {stat['max']:<10.3f} {status}")

        print("=" * 90)
        print(f"Updates every {self.update_interval}s | Press Ctrl+C to stop")

    def run(self, fifo_path):
        """Main loop - read from FIFO and update display."""
        signal.signal(signal.SIGINT, lambda s, f: self.stop())
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())

        print(f"Starting live analysis from {fifo_path}...")
        print("Waiting for packets...\n")

        # Wait for FIFO to exist
        timeout = 10
        start = time.time()
        import os
        while not os.path.exists(fifo_path) and (time.time() - start) < timeout:
            time.sleep(0.1)

        if not os.path.exists(fifo_path):
            print(f"Error: FIFO {fifo_path} not found after {timeout}s")
            return

        try:
            with open(fifo_path, 'r') as fifo:
                line_count = 0
                for line in fifo:
                    if not self.running:
                        break

                    line = line.strip()
                    if not line:
                        continue

                    line_count += 1

                    # Parse CSV line
                    parts = [p.strip('"') for p in line.split(',')]
                    if len(parts) >= 8:
                        self.process_packet(*parts)
                    else:
                        # Debug: show malformed lines for first 5
                        if line_count <= 5:
                            print(f"DEBUG: Skipping malformed line ({len(parts)} fields): {line[:100]}")

                    # Update display periodically
                    if time.time() - self.last_update >= self.update_interval:
                        self.display_stats()
                        self.last_update = time.time()

                # If we got here, FIFO closed (tshark exited)
                if line_count == 0:
                    print("Error: No data received from tshark. Check:")
                    print("  1. Is the interface correct?")
                    print("  2. Is there OSD traffic on the specified ports?")
                    print("  3. Check /tmp/osd_monitor_*/tshark.err for errors")

        except (KeyboardInterrupt, BrokenPipeError):
            pass
        except Exception as e:
            print(f"Error: {e}")
        finally:
            self.stop()

    def stop(self):
        """Stop the analyzer."""
        self.running = False
        print("\n\nFinal Statistics:")
        self.display_stats()
        print("\nAnalysis stopped.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: live_analyzer.py <fifo_path> [update_interval] [osd_map_file]")
        sys.exit(1)

    fifo_path = sys.argv[1]
    update_interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    osd_map_file = sys.argv[3] if len(sys.argv) > 3 else 'osd_ip_map.json'

    analyzer = LiveRTTAnalyzer(update_interval, osd_map_file)
    analyzer.run(fifo_path)
