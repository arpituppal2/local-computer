#!/usr/bin/env bash
PORT="${LOCAL_COMPUTER_PORT:-8765}"
HOST="${LOCAL_COMPUTER_HOST:-127.0.0.1}"
open "http://$HOST:$PORT"
