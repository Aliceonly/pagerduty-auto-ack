#!/usr/bin/env bash

until poetry run pagerduty-auto-ack --config config.toml --action resolve; do
    echo "[$(date)] Process crashed (exit code: $?), restarting in 1s..."
    sleep 1
done
