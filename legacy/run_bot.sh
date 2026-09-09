#!/usr/bin/env bash

# Example script to run Freqtrade with the specified configuration and strategy.
# Activate your Python virtual environment before running this script.

CONFIG="config.json"
STRATEGY="CryptoStrategy"

# Use --dry-run to simulate trades without executing real orders
freqtrade trade \
  --config "$CONFIG" \
  --strategy "$STRATEGY" \
  --dry-run
