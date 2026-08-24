#!/usr/bin/env bash
# Live OSD ping time monitor - captures and analyzes in real-time

INTERFACE="${1:-enp0s3}"
PORT_RANGE="${2:-6800-7100}"
UPDATE_INTERVAL="${3:-5}"  # seconds between display updates

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)"
    exit 1
fi

echo "=========================================="
echo "Live OSD Latency Monitor"
echo "=========================================="
echo "Interface: $INTERFACE"
echo "Port Range: $PORT_RANGE"
echo "Update Interval: ${UPDATE_INTERVAL}s"
echo "Press Ctrl+C to stop"
echo "=========================================="
echo ""

# Check if OSD mapping file exists, if not, try to build it
OSD_MAP_FILE="osd_ip_map.json"
if [ ! -f "$OSD_MAP_FILE" ]; then
    echo "OSD mapping file not found. Attempting to build it..."
    # Try port-aware version first (best), fall back to others
    if [ -f "./build_osd_map_with_ports.sh" ]; then
        bash ./build_osd_map_with_ports.sh "$OSD_MAP_FILE"
        if [ $? -ne 0 ]; then
            echo "Warning: Could not build OSD mapping. Will display IP addresses."
            echo ""
        fi
    elif [ -f "./build_osd_map_enhanced.sh" ]; then
        bash ./build_osd_map_enhanced.sh "$OSD_MAP_FILE"
        if [ $? -ne 0 ]; then
            echo "Warning: Could not build OSD mapping. Will display IP addresses."
            echo ""
        fi
    elif [ -f "./build_osd_map.sh" ]; then
        bash ./build_osd_map.sh "$OSD_MAP_FILE"
        if [ $? -ne 0 ]; then
            echo "Warning: Could not build OSD mapping. Will display IP addresses."
            echo ""
        fi
    else
        echo "Warning: No OSD mapping script found. Will display IP addresses."
        echo ""
    fi
else
    echo "Using OSD mapping from $OSD_MAP_FILE"
    echo ""
fi

# Create temporary directory for this session
TMPDIR="/tmp/osd_monitor_$$"
mkdir -p "$TMPDIR"
FIFO="$TMPDIR/pipe"
ERRLOG="$TMPDIR/tshark.err"

# Create named pipe for communication
mkfifo "$FIFO"

# Cleanup on exit
cleanup() {
    echo ""
    echo "Stopping capture..."
    kill $TSHARK_PID 2>/dev/null
    kill $ANALYZER_PID 2>/dev/null

    # Show any tshark errors
    if [ -s "$ERRLOG" ]; then
        echo ""
        echo "Tshark errors/warnings:"
        cat "$ERRLOG"
    fi

    rm -rf "$TMPDIR"
    exit 0
}
trap cleanup INT TERM EXIT

# Start tshark in live capture mode, writing to the FIFO
echo "Starting packet capture..."
tshark -i "$INTERFACE" -f "tcp portrange $PORT_RANGE" \
    -Y 'tcp.options.timestamp.tsval' \
    -T fields \
    -e frame.number \
    -e frame.time_relative \
    -e ip.src \
    -e ip.dst \
    -e tcp.srcport \
    -e tcp.dstport \
    -e tcp.options.timestamp.tsval \
    -e tcp.options.timestamp.tsecr \
    -E separator=, \
    -E quote=d \
    -E occurrence=f \
    > "$FIFO" 2>"$ERRLOG" &

TSHARK_PID=$!

# Give tshark a moment to start
sleep 1

# Check if tshark is still running
if ! kill -0 $TSHARK_PID 2>/dev/null; then
    echo "Error: tshark failed to start"
    cat "$ERRLOG"
    exit 1
fi

echo "Tshark started (PID: $TSHARK_PID)"
echo "Starting analyzer..."

# Start the live analyzer reading from the FIFO
python3 live_analyzer.py "$FIFO" "$UPDATE_INTERVAL" "$OSD_MAP_FILE" &
ANALYZER_PID=$!

echo "Analyzer started (PID: $ANALYZER_PID)"
echo ""

# Wait for analyzer to finish
wait $ANALYZER_PID
