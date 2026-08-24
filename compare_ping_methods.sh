#!/usr/bin/env bash
# Compare Ceph internal heartbeat vs TCP timestamp RTT measurements

set -u

INTERFACE="${1:-enp0s3}"
DURATION="${2:-30}"

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

OUTPUT_DIR="./ping_method_comparison_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  OSD Ping Method Comparison${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""
echo "Interface: $INTERFACE"
echo "Duration: ${DURATION}s"
echo "Output: $OUTPUT_DIR"
echo ""

# Step 1: Capture Ceph's internal view (baseline)
echo -e "${CYAN}[1/3]${NC} Capturing Ceph internal heartbeat data..."

./osd_heartbeat_monitor.sh 0 export > "$OUTPUT_DIR/ceph_internal.log" 2>&1

# Move export to our output dir
LATEST_EXPORT=$(ls -td ./osd_heartbeat_export_* 2>/dev/null | head -1)
if [[ -n "$LATEST_EXPORT" ]]; then
    mv "$LATEST_EXPORT"/* "$OUTPUT_DIR/" 2>/dev/null || true
    rmdir "$LATEST_EXPORT" 2>/dev/null || true
fi

echo "  ✓ Ceph internal data captured"
echo ""

# Step 2: Run TCP timestamp monitoring
echo -e "${CYAN}[2/3]${NC} Running TCP timestamp monitoring for ${DURATION}s..."

if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}  WARNING: Not root - skipping TCP monitoring${NC}"
    echo "  Run with sudo for complete comparison"
else
    timeout "$DURATION" ./live_osd_monitor.sh "$INTERFACE" 6800-7100 5 \
        > "$OUTPUT_DIR/tcp_timestamp.log" 2>&1 || true
    echo "  ✓ TCP timestamp data captured"
fi

echo ""

# Step 3: Parse and compare
echo -e "${CYAN}[3/3]${NC} Analyzing and comparing results..."
echo ""

# Create comparison report
REPORT="$OUTPUT_DIR/comparison_report.txt"

cat > "$REPORT" <<EOF
OSD Ping Method Comparison Report
Generated: $(date)
========================================

This report compares two methods of measuring OSD-to-OSD network latency:

1. Ceph Internal Heartbeat (dump_osd_network)
   - Application-level measurement
   - Built into ceph-osd daemon
   - ~6 second interval

2. TCP Timestamp RTT (ceph-rtt-tracker)
   - Transport-level measurement
   - Passive packet analysis
   - Continuous monitoring

========================================
CEPH INTERNAL HEARTBEAT RESULTS
========================================

EOF

# Parse Ceph internal data
for json_file in "$OUTPUT_DIR"/osd.*_network.json; do
    if [[ -f "$json_file" ]]; then
        local osd_id=$(basename "$json_file" | sed 's/osd\.\(.*\)_network.json/\1/')
        echo "OSD.$osd_id:" >> "$REPORT"

        # Check if there are slow connections
        if grep -q '"entries":\[\]' "$json_file"; then
            echo "  ✓ No slow connections (all peers <1000ms)" >> "$REPORT"
        else
            echo "  ⚠ Slow connections detected:" >> "$REPORT"
            cat "$json_file" >> "$REPORT"
        fi
        echo "" >> "$REPORT"
    fi
done

cat >> "$REPORT" <<EOF

========================================
TCP TIMESTAMP RESULTS (${DURATION}s sample)
========================================

EOF

# Parse TCP timestamp data
if [[ -f "$OUTPUT_DIR/tcp_timestamp.log" ]]; then
    # Extract final statistics
    sed -n '/Final Statistics:/,/Analysis stopped/p' "$OUTPUT_DIR/tcp_timestamp.log" | \
        head -50 >> "$REPORT"
else
    echo "TCP timestamp monitoring not run (requires root)" >> "$REPORT"
fi

cat >> "$REPORT" <<EOF

========================================
COMPARISON & ANALYSIS
========================================

Expected Patterns:
  ✓ Both methods should identify the same "slow" OSD pairs
  ✓ TCP RTT values should be ≤ Ceph heartbeat RTT
  ✓ Ceph heartbeat includes more processing overhead

Key Differences:
  • Ceph heartbeat: Reports only connections >1000ms by default
  • TCP timestamp: Shows all connections, more granular data
  • Ceph heartbeat: Point-in-time snapshot
  • TCP timestamp: Statistical distribution over time

When to Use Each:
  • Ceph heartbeat: Quick check if cluster sees issues
  • TCP timestamp: Detailed troubleshooting and trending

EOF

# Display summary
echo "========================================" | tee -a "$REPORT"
echo "SUMMARY" | tee -a "$REPORT"
echo "========================================" | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

# Count slow connections in Ceph internal
CEPH_SLOW_COUNT=0
for json_file in "$OUTPUT_DIR"/osd.*_network.json; do
    if [[ -f "$json_file" ]] && ! grep -q '"entries":\[\]' "$json_file"; then
        ((CEPH_SLOW_COUNT++))
    fi
done

echo "Ceph Internal Heartbeat:" | tee -a "$REPORT"
if [[ $CEPH_SLOW_COUNT -eq 0 ]]; then
    echo "  ✓ No OSDs reporting slow peers (all <1000ms)" | tee -a "$REPORT"
else
    echo "  ⚠ $CEPH_SLOW_COUNT OSD(s) have slow peer connections" | tee -a "$REPORT"
fi
echo "" | tee -a "$REPORT"

# Extract TCP summary
if [[ -f "$OUTPUT_DIR/tcp_timestamp.log" ]]; then
    echo "TCP Timestamp Monitoring:" | tee -a "$REPORT"

    # Get delay distribution
    grep -A 5 "Delay Distribution:" "$OUTPUT_DIR/tcp_timestamp.log" | tail -6 | tee -a "$REPORT"
    echo "" | tee -a "$REPORT"

    # Check for alignment
    echo "Consistency Check:" | tee -a "$REPORT"
    if [[ $CEPH_SLOW_COUNT -eq 0 ]]; then
        # Ceph sees no issues - check if TCP agrees
        CRITICAL_PCT=$(grep "Critical (>=500ms):" "$OUTPUT_DIR/tcp_timestamp.log" | tail -1 | awk '{print $4}' | tr -d '(%)' || echo "0")
        VERY_SLOW_PCT=$(grep "Very Slow (100-500ms):" "$OUTPUT_DIR/tcp_timestamp.log" | tail -1 | awk '{print $4}' | tr -d '(%)' || echo "0")

        TOTAL_SLOW=$(echo "$CRITICAL_PCT + $VERY_SLOW_PCT" | bc 2>/dev/null || echo "0")

        if (( $(echo "$TOTAL_SLOW < 5.0" | bc -l 2>/dev/null || echo "0") )); then
            echo "  ✓ Methods agree: Both show healthy network" | tee -a "$REPORT"
        else
            echo "  ⚠ Methods differ: Ceph sees no issues, TCP shows ${TOTAL_SLOW}% slow" | tee -a "$REPORT"
            echo "    (This is normal - TCP captures transient delays Ceph may not see)" | tee -a "$REPORT"
        fi
    else
        echo "  ⚠ Ceph reports slow connections - check TCP data for details" | tee -a "$REPORT"
    fi
else
    echo "TCP Timestamp Monitoring: Not run (requires root)" | tee -a "$REPORT"
fi

echo "" | tee -a "$REPORT"
echo "Full comparison report: $REPORT" | tee -a "$REPORT"
echo "All data saved to: $OUTPUT_DIR" | tee -a "$REPORT"
echo ""

cat <<EOF

========================================
NEXT STEPS
========================================

Review the comparison report:
  cat $REPORT

If methods show different results:
  1. Check if TCP is catching transient issues
  2. Run longer monitoring (increase duration)
  3. Check Ceph heartbeat interval settings:
     ceph config get osd osd_heartbeat_interval

To adjust Ceph's slow threshold:
  ceph config set osd osd_heartbeat_grace <seconds>

To run longer comparison:
  sudo $0 $INTERFACE 300  # 5 minutes

EOF
