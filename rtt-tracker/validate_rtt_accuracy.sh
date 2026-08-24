#!/usr/bin/env bash
# RTT Accuracy Validation Script
# Compares ceph-rtt-tracker measurements against multiple reference sources

set -u

OUTPUT_DIR="./rtt_validation_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

REPORT="$OUTPUT_DIR/validation_report.txt"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_section() {
    echo "" | tee -a "$REPORT"
    echo "========================================================================" | tee -a "$REPORT"
    echo "$1" | tee -a "$REPORT"
    echo "========================================================================" | tee -a "$REPORT"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$REPORT"
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $1" | tee -a "$REPORT"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$REPORT"
}

# 1. Get Ceph's view of OSD ping times
check_ceph_osd_ping() {
    log_section "1. CEPH OSD PING COMMAND (Reference Baseline)"

    if ! command -v ceph &>/dev/null; then
        log_warn "ceph command not available"
        return
    fi

    log_info "Running 'ceph osd ping' to get Ceph's RTT measurements..."
    echo "" | tee -a "$REPORT"

    # Get list of OSDs
    local osds=$(ceph osd ls 2>/dev/null)

    if [[ -z "$osds" ]]; then
        log_warn "Could not get OSD list"
        return
    fi

    # Sample ping between a few OSD pairs
    local count=0
    for src_osd in $osds; do
        for dst_osd in $osds; do
            if [[ $src_osd -ge $dst_osd ]]; then
                continue
            fi

            # Limit to 5 pairs for quick test
            if [[ $count -ge 5 ]]; then
                break 2
            fi

            log_info "Testing OSD.$src_osd -> OSD.$dst_osd"

            # Run ceph osd ping
            local ping_result=$(ceph tell osd.$src_osd ping osd.$dst_osd 2>&1 || true)
            echo "  $ping_result" | tee -a "$REPORT"

            ((count++))
        done
    done

    echo "" | tee -a "$REPORT"
    log_ok "Ceph OSD ping baseline captured"
}

# 2. Manual ping to OSD hosts
check_icmp_ping() {
    log_section "2. ICMP PING TO OSD HOSTS (Network Layer Baseline)"

    log_info "Getting OSD host IPs..."

    if ! command -v ceph &>/dev/null; then
        log_warn "ceph command not available"
        return
    fi

    # Get unique IPs from OSD tree
    local hosts=$(ceph osd tree 2>/dev/null | grep "host" | awk '{print $4}' | sort -u)

    if [[ -z "$hosts" ]]; then
        log_warn "Could not get host list"
        return
    fi

    log_info "Pinging each OSD host (100 packets)..."
    echo "" | tee -a "$REPORT"

    for host in $hosts; do
        log_info "Pinging $host..."
        ping -c 100 -i 0.01 "$host" > "$OUTPUT_DIR/ping_${host}.txt" 2>&1

        local summary=$(tail -2 "$OUTPUT_DIR/ping_${host}.txt")
        echo "  $host:" | tee -a "$REPORT"
        echo "$summary" | sed 's/^/    /' | tee -a "$REPORT"
        echo "" | tee -a "$REPORT"
    done
}

# 3. Capture packets and calculate RTT manually with tcpdump
capture_and_analyze() {
    log_section "3. TCPDUMP RTT ANALYSIS (Packet-Level Validation)"

    if [[ $EUID -ne 0 ]]; then
        log_warn "Not running as root - skipping packet capture"
        return
    fi

    local interface="${1:-enp0s3}"
    local duration=10

    log_info "Capturing $duration seconds of OSD traffic on $interface..."

    local pcap_file="$OUTPUT_DIR/osd_traffic_sample.pcap"

    timeout $duration tcpdump -i "$interface" -w "$pcap_file" \
        'tcp portrange 6800-7100' 2>/dev/null || true

    if [[ ! -f "$pcap_file" ]]; then
        log_warn "Packet capture failed"
        return
    fi

    local packet_count=$(tcpdump -r "$pcap_file" 2>/dev/null | wc -l)
    log_info "Captured $packet_count packets"

    # Analyze with tshark if available
    if command -v tshark &>/dev/null; then
        log_info "Analyzing TCP conversations..."

        tshark -r "$pcap_file" -q -z conv,tcp 2>/dev/null | \
            tee "$OUTPUT_DIR/tcp_conversations.txt" | \
            head -20 | tee -a "$REPORT"
    fi
}

# 4. Check TCP timestamp clock frequency
check_tcp_timestamp_hz() {
    log_section "4. TCP TIMESTAMP CLOCK VALIDATION"

    log_info "Checking TCP timestamp parameters..."
    echo "" | tee -a "$REPORT"

    # Check if timestamps are enabled
    local ts_enabled=$(cat /proc/sys/net/ipv4/tcp_timestamps 2>/dev/null || echo "unknown")
    echo "  tcp_timestamps: $ts_enabled" | tee -a "$REPORT"

    if [[ "$ts_enabled" != "1" ]]; then
        log_warn "TCP timestamps are disabled! RTT measurement will not work."
        log_info "Enable with: sysctl -w net.ipv4.tcp_timestamps=1"
    else
        log_ok "TCP timestamps enabled"
    fi

    # The kernel typically uses jiffies/HZ for timestamps
    # HZ is usually 100, 250, or 1000
    echo "" | tee -a "$REPORT"
    log_info "TCP timestamp clock info:"
    echo "  Linux kernel uses jiffies for TCP timestamps" | tee -a "$REPORT"
    echo "  Typical HZ values: 100 (10ms), 250 (4ms), 1000 (1ms)" | tee -a "$REPORT"
    echo "  Current kernel: $(uname -r)" | tee -a "$REPORT"

    # Try to determine HZ
    if [[ -f /boot/config-$(uname -r) ]]; then
        local hz_config=$(grep "^CONFIG_HZ=" /boot/config-$(uname -r) 2>/dev/null || echo "")
        if [[ -n "$hz_config" ]]; then
            echo "  $hz_config" | tee -a "$REPORT"
            local hz_val=$(echo "$hz_config" | cut -d'=' -f2)
            local resolution=$(awk "BEGIN {printf \"%.2f\", 1000/$hz_val}")
            echo "  Timestamp resolution: ~${resolution}ms" | tee -a "$REPORT"
        fi
    fi
}

# 5. Sample live_analyzer.py output for comparison
sample_live_analyzer() {
    log_section "5. CEPH-RTT-TRACKER SAMPLE (10 Second Capture)"

    if [[ ! -f "./live_osd_monitor.sh" ]]; then
        log_warn "live_osd_monitor.sh not found"
        return
    fi

    if [[ $EUID -ne 0 ]]; then
        log_warn "Not running as root - skipping live capture"
        log_info "Run with sudo to capture live RTT data"
        return
    fi

    log_info "Running live_osd_monitor.sh for 10 seconds..."

    # Run for 10 seconds and capture output
    timeout 10 ./live_osd_monitor.sh enp0s3 6800-7100 5 > "$OUTPUT_DIR/live_monitor_sample.txt" 2>&1 || true

    log_info "Sample captured to: $OUTPUT_DIR/live_monitor_sample.txt"
}

# 6. Compare methodologies
compare_methods() {
    log_section "6. METHODOLOGY COMPARISON"

    cat <<EOF | tee -a "$REPORT"

RTT Measurement Methods Comparison:
-----------------------------------

1. ICMP Ping (ping command)
   - Measures: Network layer round-trip time
   - Includes: Network propagation + host processing
   - Does NOT include: Application/OSD processing time
   - Accuracy: ~0.1ms resolution
   - Use case: Network baseline

2. Ceph OSD Ping (ceph tell osd.X ping osd.Y)
   - Measures: OSD-to-OSD application-level RTT
   - Includes: Network + OSD messenger processing
   - Represents: Actual Ceph operation latency
   - Accuracy: Varies based on OSD load
   - Use case: Ceph operational baseline

3. TCP Timestamp-based (ceph-rtt-tracker)
   - Measures: TCP connection RTT (tsval/tsecr)
   - Includes: Network propagation + TCP stack
   - Does NOT include: Application processing after TCP ACK
   - Accuracy: Limited by HZ (typically 1-10ms)
   - Use case: Continuous passive monitoring

Expected Relationships:
   ICMP Ping < TCP Timestamp RTT < Ceph OSD Ping

   - ICMP is lowest (just network)
   - TCP adds stack processing
   - Ceph adds application processing

Validation Checks:
   ✓ TCP timestamps should be ~1-5ms higher than ICMP ping
   ✓ Ceph OSD ping should be highest (includes messenger overhead)
   ✓ All should show similar relative ordering (slow pairs in all methods)

EOF
}

# Main execution
main() {
    local interface="${1:-enp0s3}"

    log_section "RTT MEASUREMENT ACCURACY VALIDATION - $(date)"
    log_info "Interface: $interface"
    echo "" | tee -a "$REPORT"

    check_tcp_timestamp_hz
    check_icmp_ping
    check_ceph_osd_ping
    capture_and_analyze "$interface"
    sample_live_analyzer
    compare_methods

    log_section "VALIDATION COMPLETE"
    log_info "All validation data saved to: $OUTPUT_DIR"
    log_info "Main report: $REPORT"
    echo "" | tee -a "$REPORT"
    log_info "Review the report to compare RTT measurements across methods"
}

# Usage
if [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
    cat <<EOF
Usage: $0 [interface]

Validates RTT measurement accuracy by comparing multiple methods:
  - ICMP ping (network baseline)
  - Ceph OSD ping (application baseline)
  - TCP timestamp analysis (ceph-rtt-tracker method)
  - Packet capture validation

Arguments:
    interface   Network interface (default: enp0s3)

Examples:
    sudo $0
    sudo $0 bond1

Must run as root for packet capture features.
EOF
    exit 0
fi

main "${1:-enp0s3}"
