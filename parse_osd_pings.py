#!/usr/bin/env python3
"""
Parse OSD ping times from tcpdump hex output.
Extracts TCP timestamp options to calculate round-trip times between OSDs.
"""

import re
import sys
from collections import defaultdict

def parse_ip(hex_bytes):
    """Convert 4 hex bytes to IP address string."""
    return '.'.join(str(int(b, 16)) for b in hex_bytes)

def parse_timestamp(hex_bytes):
    """Convert 4 hex bytes to integer timestamp."""
    return int(''.join(hex_bytes), 16)

def parse_osd_pings(filename):
    """Parse the osdpings file and extract timestamp data."""

    packets = []
    current_packet = {}

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line == '--':
                if current_packet:
                    packets.append(current_packet)
                    current_packet = {}
                continue

            # Parse hex dump lines
            match = re.match(r'(\d+)\s+([0-9a-f]{4})\s+(.+)', line)
            if not match:
                continue

            offset = match.group(2)
            hex_data = match.group(3).split()[:16]  # First 16 hex bytes

            # Extract IP addresses from 0010 line
            if offset == '0010' and len(hex_data) >= 16:
                # Source IP: bytes 12-15
                src_ip = parse_ip(hex_data[12:16])
                current_packet['src_ip'] = src_ip

            # Extract destination IP from 0020 line
            if offset == '0020' and len(hex_data) >= 16:
                # Destination IP continues from 0010: first 2 bytes
                if 'src_ip' in current_packet:
                    dst_ip = parse_ip(hex_data[0:2] + ['00', '00'])  # Placeholder, will fix
                    # Actually dest IP is at the end of 0010 line, let me recalculate
                    pass

            # Extract TCP timestamps from 0030 line
            if offset == '0030' and len(hex_data) >= 16:
                # Looking for pattern: 01 01 08 0a [TSval 4 bytes] [TSecr 4 bytes]
                # TSval starts at byte 10 (index 10-13)
                # TSecr starts at byte 14 (index 14-17, but we only have 16 bytes)
                if len(hex_data) >= 16:
                    # Check for TCP timestamp option (08 0a)
                    if hex_data[8] == '08' and hex_data[9] == '0a':
                        tsval = parse_timestamp(hex_data[10:14])
                        current_packet['tsval'] = tsval
                        # TSecr might span into next line, get what we can
                        if len(hex_data) >= 16:
                            tsecr_partial = hex_data[14:16]
                            current_packet['tsecr_partial'] = tsecr_partial

    # Add last packet
    if current_packet:
        packets.append(current_packet)

    return packets


def parse_osd_pings_v2(filename):
    """Improved parser that correctly extracts IPs and timestamps."""

    packets = []
    lines_buffer = []

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line == '--':
                if lines_buffer:
                    packet = parse_packet_group(lines_buffer)
                    if packet:
                        packets.append(packet)
                    lines_buffer = []
            else:
                lines_buffer.append(line)

        # Handle last packet
        if lines_buffer:
            packet = parse_packet_group(lines_buffer)
            if packet:
                packets.append(packet)

    return packets


def parse_packet_group(lines):
    """Parse a group of hex dump lines for one packet."""
    packet = {}
    hex_lines = {}

    for line in lines:
        match = re.match(r'([0-9a-f]{4})\s+(.+)', line)
        if not match:
            continue

        offset = match.group(1)
        # Extract just the hex bytes (before the ASCII representation)
        hex_part = match.group(2).split('   ')[0] if '   ' in match.group(2) else match.group(2)
        hex_bytes = hex_part.strip().split()
        hex_lines[offset] = hex_bytes

    # Extract source and destination IPs from 0010 and 0020
    if '0010' in hex_lines and '0020' in hex_lines:
        line_0010 = hex_lines['0010']
        line_0020 = hex_lines['0020']

        if len(line_0010) >= 16 and len(line_0020) >= 2:
            # Source IP: last 4 bytes of 0010
            src_ip = parse_ip(line_0010[12:16])
            # Dest IP: first 4 bytes of 0020
            dst_ip = parse_ip(line_0020[0:4])

            packet['src_ip'] = src_ip
            packet['dst_ip'] = dst_ip

            # Extract ports from 0020
            if len(line_0020) >= 8:
                src_port = int(''.join(line_0020[4:6]), 16)
                dst_port = int(''.join(line_0020[6:8]), 16)
                packet['src_port'] = src_port
                packet['dst_port'] = dst_port

    # Extract TCP timestamps from 0030
    if '0030' in hex_lines:
        line_0030 = hex_lines['0030']

        # Find TCP timestamp option: 08 0a
        for i in range(len(line_0030) - 9):
            if line_0030[i] == '08' and line_0030[i+1] == '0a':
                # TSval: next 4 bytes
                tsval = parse_timestamp(line_0030[i+2:i+6])
                # TSecr: following 4 bytes
                tsecr = parse_timestamp(line_0030[i+6:i+10])

                packet['tsval'] = tsval
                packet['tsecr'] = tsecr
                break

    return packet if packet else None


def calculate_rtts(packets):
    """Calculate round-trip times by matching requests and responses."""

    # Group packets by connection (src_ip:src_port <-> dst_ip:dst_port)
    rtts = []

    # Create a map of TSval -> packet for matching
    tsval_map = {}

    for pkt in packets:
        if 'tsval' not in pkt or 'tsecr' not in pkt:
            continue

        connection = (pkt.get('src_ip'), pkt.get('dst_ip'))
        reverse_connection = (pkt.get('dst_ip'), pkt.get('src_ip'))

        # If TSecr is non-zero, this might be a response echoing a previous TSval
        if pkt['tsecr'] != 0:
            # Look for the original packet with this TSval
            key = (reverse_connection, pkt['tsecr'])
            if key in tsval_map:
                orig_pkt = tsval_map[key]
                # Calculate RTT: current TSval - original TSval
                rtt_ticks = pkt['tsval'] - orig_pkt['tsval']

                rtts.append({
                    'src': orig_pkt['src_ip'],
                    'dst': orig_pkt['dst_ip'],
                    'rtt_ticks': rtt_ticks,
                    'rtt_ms': rtt_ticks,  # Assume 1ms per tick (Linux HZ=1000)
                    'orig_tsval': orig_pkt['tsval'],
                    'resp_tsval': pkt['tsval'],
                    'resp_tsecr': pkt['tsecr']
                })

        # Store this packet for future matching
        key = (connection, pkt['tsval'])
        tsval_map[key] = pkt

    return rtts


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else 'osdpings'

    print(f"Parsing {filename}...")
    packets = parse_osd_pings_v2(filename)
    print(f"Found {len(packets)} packets\n")

    # Show sample packets
    print("Sample packets:")
    for i, pkt in enumerate(packets[:5]):
        print(f"  Packet {i}: {pkt.get('src_ip', '?')}:{pkt.get('src_port', '?')} -> "
              f"{pkt.get('dst_ip', '?')}:{pkt.get('dst_port', '?')} "
              f"TSval={pkt.get('tsval', '?')} TSecr={pkt.get('tsecr', '?')}")

    print("\nCalculating RTTs...")
    rtts = calculate_rtts(packets)

    if rtts:
        print(f"\nFound {len(rtts)} RTT measurements:")
        print("\nRTT Statistics:")
        print(f"{'Source':<15} {'Destination':<15} {'RTT (ms)':<10} {'RTT (ticks)'}")
        print("-" * 60)

        # Group by connection
        conn_rtts = defaultdict(list)
        for rtt in rtts:
            conn_key = (rtt['src'], rtt['dst'])
            conn_rtts[conn_key].append(rtt['rtt_ms'])

        # Show stats per connection
        for (src, dst), rtt_list in sorted(conn_rtts.items()):
            if rtt_list:
                avg_rtt = sum(rtt_list) / len(rtt_list)
                min_rtt = min(rtt_list)
                max_rtt = max(rtt_list)
                print(f"{src:<15} {dst:<15} avg={avg_rtt:6.2f}  min={min_rtt:6.2f}  max={max_rtt:6.2f}  (n={len(rtt_list)})")
    else:
        print("\nNo RTT measurements found. This could mean:")
        print("  - Packets don't have matching request/response pairs")
        print("  - TCP timestamp options are not being used")
        print("  - Need more packet data to match pairs")


if __name__ == '__main__':
    main()
