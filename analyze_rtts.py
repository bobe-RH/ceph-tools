#!/usr/bin/env python3
"""
Analyze OSD RTTs from tshark-extracted TCP timestamp data.
"""

import csv
import sys
from collections import defaultdict
from statistics import mean, median, stdev

def analyze_rtts(csv_file='osd_rtts.csv'):
    """Calculate RTTs by matching TCP timestamp echoes."""

    packets = []
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                packets.append({
                    'frame': int(row['frame.number']),
                    'time': float(row['frame.time_relative']),
                    'src': row['ip.src'],
                    'dst': row['ip.dst'],
                    'sport': int(row['tcp.srcport']),
                    'dport': int(row['tcp.dstport']),
                    'tsval': int(row['tcp.options.timestamp.tsval']),
                    'tsecr': int(row['tcp.options.timestamp.tsecr']),
                })
            except (ValueError, KeyError):
                continue

    print(f"Loaded {len(packets)} packets with TCP timestamps\n")

    # Build index: (src_ip, dst_ip, src_port, dst_port, tsval) -> packet
    # This helps us find the original packet when we see its TSval echoed back
    tsval_index = {}
    rtts = []

    for pkt in packets:
        # Check if this packet echoes a previous TSval
        if pkt['tsecr'] != 0:
            # Look for the packet that sent this TSval
            # The connection is reversed: we're looking for dst->src with TSval=this TSecr
            key = (pkt['dst'], pkt['src'], pkt['dport'], pkt['sport'], pkt['tsecr'])

            if key in tsval_index:
                orig_pkt = tsval_index[key]
                # Calculate RTT using frame timestamps (more accurate than TSval arithmetic)
                rtt_seconds = pkt['time'] - orig_pkt['time']
                rtt_ms = rtt_seconds * 1000

                # Also calculate using TCP timestamps (less precise, but independent)
                # Assuming HZ=1000 (1ms per tick on modern Linux)
                ts_diff = pkt['tsval'] - orig_pkt['tsecr']

                if rtt_ms > 0 and rtt_ms < 1000:  # Filter out unreasonable values
                    rtts.append({
                        'src': orig_pkt['src'],
                        'dst': orig_pkt['dst'],
                        'sport': orig_pkt['sport'],
                        'dport': orig_pkt['dport'],
                        'rtt_ms': rtt_ms,
                        'ts_rtt': ts_diff,
                        'orig_frame': orig_pkt['frame'],
                        'resp_frame': pkt['frame'],
                    })

        # Index this packet for future matching
        key = (pkt['src'], pkt['dst'], pkt['sport'], pkt['dport'], pkt['tsval'])
        tsval_index[key] = pkt

    print(f"Calculated {len(rtts)} RTT measurements\n")

    if not rtts:
        print("No RTT measurements found!")
        return

    # Group by OSD connection (IP pairs)
    osd_rtts = defaultdict(list)
    for rtt in rtts:
        # Normalize connection (order IPs consistently)
        ips = tuple(sorted([rtt['src'], rtt['dst']]))
        osd_rtts[ips].append(rtt['rtt_ms'])

    print("=" * 80)
    print("OSD Ping Times Summary")
    print("=" * 80)
    print(f"{'OSD 1':<17} {'OSD 2':<17} {'Samples':<8} {'Min (ms)':<10} {'Avg (ms)':<10} {'Max (ms)':<10} {'StdDev':<10}")
    print("-" * 80)

    for ips, rtt_list in sorted(osd_rtts.items()):
        if len(rtt_list) < 2:
            continue

        avg = mean(rtt_list)
        min_rtt = min(rtt_list)
        max_rtt = max(rtt_list)
        std = stdev(rtt_list) if len(rtt_list) > 1 else 0

        print(f"{ips[0]:<17} {ips[1]:<17} {len(rtt_list):<8} {min_rtt:<10.3f} {avg:<10.3f} {max_rtt:<10.3f} {std:<10.3f}")

    # Overall statistics
    all_rtts = [r for rtts_list in osd_rtts.values() for r in rtts_list]
    print("-" * 80)
    print(f"{'OVERALL':<17} {'':<17} {len(all_rtts):<8} {min(all_rtts):<10.3f} {mean(all_rtts):<10.3f} {max(all_rtts):<10.3f} {stdev(all_rtts):<10.3f}")
    print("=" * 80)

    # Show distribution
    print("\nRTT Distribution:")
    buckets = defaultdict(int)
    for rtt in all_rtts:
        bucket = int(rtt / 0.1) * 0.1  # 0.1ms buckets
        buckets[bucket] += 1

    for bucket in sorted(buckets.keys())[:20]:  # Show first 20 buckets
        bar = '#' * (buckets[bucket] // 10)
        print(f"  {bucket:5.1f}-{bucket+0.1:5.1f} ms: {bar} ({buckets[bucket]})")

    # Percentile analysis
    sorted_rtts = sorted(all_rtts)
    print("\n" + "=" * 80)
    print("Percentile Analysis (Delay Detection)")
    print("=" * 80)
    percentiles = [50, 75, 90, 95, 99, 99.9]
    for p in percentiles:
        idx = int(len(sorted_rtts) * p / 100)
        if idx < len(sorted_rtts):
            print(f"  P{p:5.1f}: {sorted_rtts[idx]:8.3f} ms")

    # Identify problematic delays
    print("\n" + "=" * 80)
    print("Delay Analysis")
    print("=" * 80)

    # Count delays by severity
    fast = sum(1 for r in all_rtts if r < 1)
    normal = sum(1 for r in all_rtts if 1 <= r < 10)
    slow = sum(1 for r in all_rtts if 10 <= r < 100)
    very_slow = sum(1 for r in all_rtts if 100 <= r < 500)
    critical = sum(1 for r in all_rtts if r >= 500)

    total = len(all_rtts)
    print(f"  Fast      (< 1 ms):      {fast:6} ({fast/total*100:5.1f}%) ✓ Healthy")
    print(f"  Normal    (1-10 ms):     {normal:6} ({normal/total*100:5.1f}%)")
    print(f"  Slow      (10-100 ms):   {slow:6} ({slow/total*100:5.1f}%) ⚠ Minor delay")
    print(f"  Very Slow (100-500 ms):  {very_slow:6} ({very_slow/total*100:5.1f}%) ⚠⚠ Significant delay")
    print(f"  Critical  (>= 500 ms):   {critical:6} ({critical/total*100:5.1f}%) 🔴 CRITICAL delay")

    # Show worst offenders
    if critical or very_slow:
        print("\n" + "=" * 80)
        print("Top 10 Slowest Round Trips (DELAYS DETECTED)")
        print("=" * 80)
        print(f"{'Rank':<6} {'Source IP':<17} {'Destination IP':<17} {'RTT (ms)':<12} {'Frames':<15}")
        print("-" * 80)

        worst_rtts = sorted(rtts, key=lambda x: x['rtt_ms'], reverse=True)[:10]
        for i, rtt in enumerate(worst_rtts, 1):
            frames = f"{rtt['orig_frame']}->{rtt['resp_frame']}"
            print(f"{i:<6} {rtt['src']:<17} {rtt['dst']:<17} {rtt['rtt_ms']:<12.3f} {frames:<15}")

    # Time-based delay pattern analysis
    print("\n" + "=" * 80)
    print("Delay Pattern Over Time")
    print("=" * 80)

    # Group RTTs by time windows
    time_windows = defaultdict(list)
    for rtt_data in rtts:
        # Find the original packet to get its timestamp
        for pkt in packets:
            if pkt['frame'] == rtt_data['orig_frame']:
                window = int(pkt['time'] / 1.0)  # 1-second windows
                time_windows[window].append(rtt_data['rtt_ms'])
                break

    # Show windows with high average delays
    if time_windows:
        window_stats = []
        for window, rtt_list in time_windows.items():
            if len(rtt_list) >= 5:  # Only consider windows with enough samples
                avg_rtt = mean(rtt_list)
                max_rtt = max(rtt_list)
                window_stats.append({
                    'window': window,
                    'avg': avg_rtt,
                    'max': max_rtt,
                    'count': len(rtt_list)
                })

        # Sort by average RTT
        window_stats.sort(key=lambda x: x['avg'], reverse=True)

        print(f"{'Time (s)':<10} {'Avg RTT (ms)':<15} {'Max RTT (ms)':<15} {'Samples':<10} {'Status'}")
        print("-" * 80)
        for w in window_stats[:10]:  # Show top 10 problematic windows
            status = "🔴 CRITICAL" if w['avg'] > 100 else "⚠ SLOW" if w['avg'] > 10 else "OK"
            print(f"{w['window']:<10} {w['avg']:<15.3f} {w['max']:<15.3f} {w['count']:<10} {status}")

    # Save detailed results
    with open('rtt_details.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['src', 'dst', 'sport', 'dport', 'rtt_ms', 'ts_rtt', 'orig_frame', 'resp_frame'])
        writer.writeheader()
        writer.writerows(rtts)

    print(f"\nDetailed results saved to rtt_details.csv")

if __name__ == '__main__':
    csv_file = sys.argv[1] if len(sys.argv) > 1 else 'osd_rtts.csv'
    analyze_rtts(csv_file)
