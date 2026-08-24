#!/usr/bin/env bash
# Extract OSD round-trip times using tshark's built-in TCP analysis

PCAP_FILE="${1:-../out.pcap}"
OUTPUT_FILE="osd_rtts.csv"

echo "Extracting TCP timestamp data from $PCAP_FILE..."

# Extract: frame number, time, src IP, dst IP, src port, dst port, TSval, TSecr
tshark -r "$PCAP_FILE" -T fields \
    -e frame.number \
    -e frame.time_relative \
    -e ip.src \
    -e ip.dst \
    -e tcp.srcport \
    -e tcp.dstport \
    -e tcp.options.timestamp.tsval \
    -e tcp.options.timestamp.tsecr \
    -E header=y \
    -E separator=, \
    -E quote=d \
    -E occurrence=f \
    'tcp.options.timestamp' > "$OUTPUT_FILE"

echo "Saved to $OUTPUT_FILE"
echo "Total packets with TCP timestamps: $(wc -l < "$OUTPUT_FILE" | awk '{print $1-1}')"

echo ""
echo "Sample data:"
head -6 "$OUTPUT_FILE" | column -t -s,

echo ""
echo "Now analyzing RTTs..."
