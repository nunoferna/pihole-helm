#!/bin/bash
# Script to download the Pi-hole Exporter Grafana dashboard

set -e

DASHBOARD_ID="10176"
DASHBOARD_NAME="pihole-exporter"
OUTPUT_DIR="charts/pihole/files/dashboards"
OUTPUT_FILE="${OUTPUT_DIR}/${DASHBOARD_NAME}.json"

echo "Downloading Grafana dashboard ${DASHBOARD_ID}..."

# Create directory if it doesn't exist
mkdir -p "${OUTPUT_DIR}"

# Download dashboard
curl -s "https://grafana.com/api/dashboards/${DASHBOARD_ID}/revisions/latest/download" > "${OUTPUT_FILE}"

if [ ! -s "${OUTPUT_FILE}" ]; then
    echo "Error: Failed to download dashboard"
    exit 1
fi

echo "Fixing datasource references..."
# Replace ${DS_PROMETHEUS} with Prometheus to avoid datasource not found errors
sed -i.bak "s/\"\\${DS_PROMETHEUS}\"/\"Prometheus\"/g" "${OUTPUT_FILE}" && rm "${OUTPUT_FILE}.bak"

echo "✅ Dashboard downloaded successfully!"
echo "📍 Location: ${OUTPUT_FILE}"
echo "📊 Size: $(wc -c < "${OUTPUT_FILE}") bytes"
echo ""
echo "To enable the dashboard in your deployment, set in values.yaml:"
echo "  metrics:"
echo "    enabled: true"
echo "    dashboards:"
echo "      enabled: true"
echo "      label: grafana_dashboard"
echo "      labelValue: \"1\""
