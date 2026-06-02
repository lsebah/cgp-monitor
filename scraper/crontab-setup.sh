#!/bin/bash
# Setup daily scraper execution via crontab
# Usage: ./scraper/crontab-setup.sh

# Get the absolute path to the scraper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
SCRAPER_CMD="cd $REPO_DIR && python scraper/main.py >> /tmp/cgp-monitor-scrape.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "cgp-monitor"; then
    echo "✓ CGP Monitor cron job already installed"
    crontab -l | grep "cgp-monitor"
else
    echo "Installing CGP Monitor daily scraper..."

    # Create a temporary crontab file
    TEMP_CRON=$(mktemp)
    crontab -l > "$TEMP_CRON" 2>/dev/null || true

    # Add the daily scrape job (2 AM every day)
    echo "0 2 * * * $SCRAPER_CMD # CGP Monitor daily scrape" >> "$TEMP_CRON"

    # Install the new crontab
    crontab "$TEMP_CRON"
    rm "$TEMP_CRON"

    echo "✓ Installed CGP Monitor daily scraper"
    echo "  Runs every day at 02:00 (2 AM)"
    echo "  Logs: /tmp/cgp-monitor-scrape.log"
    echo ""
    crontab -l | grep "cgp-monitor"
fi
