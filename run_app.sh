#!/usr/bin/env bash
# run_app.sh  —  Launch local-computer as a native macOS window.
# This is called by the .app bundle in your Dock.
# It activates the venv, starts the dashboard server,
# then opens the UI in a dedicated frameless WebKit window via Python.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"

# ── 1. Bootstrap venv if needed ────────────────────────────────────────────
if [ ! -f "$VENV/bin/python" ]; then
  echo "[local-computer] Creating venv..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r "$DIR/requirements.txt"
  "$VENV/bin/playwright" install chromium
fi

# ── 2. Kill any stale dashboard server ─────────────────────────────────────
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null || true

# ── 3. Start dashboard WebSocket server in background ──────────────────────
"$VENV/bin/python" "$DIR/scripts/localhost_server.py" &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for the server to be ready (max 5 s)
for i in $(seq 1 10); do
  nc -z 127.0.0.1 8765 2>/dev/null && break
  sleep 0.5
done

# ── 4. Open the dashboard in a native macOS WebKit window ──────────────────
# Uses PyObjC (ships with macOS Python) to create a frameless WKWebView app.
"$VENV/bin/python" - <<'PYAPP'
import sys, os, threading, time

try:
    import AppKit
    import WebKit
    import objc
except ImportError:
    # PyObjC not in venv — fall back to opening in the default browser
    import webbrowser
    webbrowser.open("http://localhost:8765")
    # Keep the server alive
    import time
    while True:
        time.sleep(60)

import AppKit, WebKit, objc
from Foundation import NSURL, NSURLRequest

DASH_URL = "http://localhost:8765"

class AppDelegate(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, note):
        # Window
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
        )
        rect = AppKit.NSMakeRect(100, 100, 1280, 820)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style,
            AppKit.NSBackingStoreBuffered, False
        )
        win.setTitle_("local-computer")
        win.setMinSize_(AppKit.NSMakeSize(800, 500))

        # WKWebView
        cfg = WebKit.WKWebViewConfiguration.alloc().init()
        wv = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            win.contentView().bounds(), cfg
        )
        wv.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        win.contentView().addSubview_(wv)

        url  = NSURL.URLWithString_(DASH_URL)
        req  = NSURLRequest.requestWithURL_(url)
        wv.loadRequest_(req)

        win.makeKeyAndOrderFront_(None)
        self._win = win
        self._wv  = wv

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

app  = AppKit.NSApplication.sharedApplication()
del_ = AppDelegate.alloc().init()
app.setDelegate_(del_)
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
app.activateIgnoringOtherApps_(True)
app.run()
PYAPP
