#!/usr/bin/env python3
"""Native macOS menu-bar host for the local Locus dashboard."""
from __future__ import annotations

import sys
import os
import time
import webbrowser
from pathlib import Path


HOST = os.getenv("LOCAL_COMPUTER_HOST", "127.0.0.1")
PORT = int(os.getenv("LOCAL_COMPUTER_PORT", "8765"))
DASH_URL = f"http://{HOST}:{PORT}"
ROOT = Path(__file__).resolve().parent.parent


def _fallback_browser() -> None:
    webbrowser.open(DASH_URL)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        return


try:
    import AppKit
    import WebKit
    from Foundation import NSURL, NSURLRequest
except Exception:
    _fallback_browser()
    raise SystemExit(0)


def _event_mask(*names: str) -> int:
    mask = 0
    for name in names:
        mask |= getattr(AppKit, name)
    return mask


class LocusAppDelegate(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        self.panel = None
        self.webview = None
        self.setup_window = None
        self.setup_webview = None
        self.last_command_down = 0.0
        self.status_item = None
        self._build_status_item()
        self._build_panel()
        self._install_shortcut_monitors()
        self._show_setup_overlay()
        self._schedule_setup_poll()

    def _icon_image(self, size: float = 18.0):
        image_path = ROOT / "assets" / "icons" / "locus-app-icon-64.png"
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(image_path))
        if image is None:
            image = AppKit.NSImage.imageNamed_(AppKit.NSImageNameComputer)
        image.setSize_(AppKit.NSMakeSize(size, size))
        image.setTemplate_(True)
        return image

    def _build_status_item(self) -> None:
        status_bar = AppKit.NSStatusBar.systemStatusBar()
        self.status_item = status_bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = self.status_item.button()
        button.setImage_(self._icon_image())
        button.setToolTip_("Locus")
        button.setTarget_(self)
        button.setAction_("togglePanel:")
        menu = AppKit.NSMenu.alloc().initWithTitle_("Locus")
        for title, selector in [
            ("Open Locus", "showPanel:"),
            ("Settings", "openSettings:"),
            ("Plugin Center", "openPlugins:"),
            ("Safety", "openSafety:"),
        ]:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
            item.setTarget_(self)
            menu.addItem_(item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit Locus", "terminate:", "q")
        quit_item.setTarget_(AppKit.NSApp)
        menu.addItem_(quit_item)
        self.status_menu = menu

    def _screen_rect_for_panel(self):
        visible = AppKit.NSScreen.mainScreen().visibleFrame()
        width = min(1120, visible.size.width - 96)
        height = min(760, visible.size.height - 96)
        x = visible.origin.x + (visible.size.width - width) / 2
        y = visible.origin.y + (visible.size.height - height) / 2
        return AppKit.NSMakeRect(x, y, width, height)

    def _build_panel(self) -> None:
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskResizable
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._screen_rect_for_panel(),
            style,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Locus")
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setMovableByWindowBackground_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorTransient
        )
        panel.setDelegate_(self)

        cfg = WebKit.WKWebViewConfiguration.alloc().init()
        prefs = cfg.preferences()
        if hasattr(prefs, "setValue_forKey_"):
            prefs.setValue_forKey_(True, "developerExtrasEnabled")
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(panel.contentView().bounds(), cfg)
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        try:
            webview.setValue_forKey_(False, "drawsBackground")
        except Exception:
            pass
        panel.contentView().addSubview_(webview)
        webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"{DASH_URL}/?surface=overlay")))
        self.panel = panel
        self.webview = webview

    def _show_setup_overlay(self) -> None:
        screen = AppKit.NSScreen.mainScreen().frame()
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            screen,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setOpaque_(False)
        window.setIgnoresMouseEvents_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorTransient
        )
        cfg = WebKit.WKWebViewConfiguration.alloc().init()
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(window.contentView().bounds(), cfg)
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        try:
            webview.setValue_forKey_(False, "drawsBackground")
        except Exception:
            pass
        window.contentView().addSubview_(webview)
        webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"{DASH_URL}/?surface=setup")))
        window.orderFrontRegardless()
        self.setup_window = window
        self.setup_webview = webview

    def _schedule_setup_poll(self) -> None:
        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.85,
            self,
            "pollSetupOverlay:",
            None,
            False,
        )

    def _install_shortcut_monitors(self) -> None:
        mask = _event_mask("NSEventMaskKeyDown", "NSEventMaskFlagsChanged")
        self.local_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self._handle_local_event)
        self.global_monitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._handle_global_event)

    def _handle_local_event(self, event):
        if self._handle_shortcut_event(event):
            return None
        return event

    def _handle_global_event(self, event):
        self._handle_shortcut_event(event)

    def _handle_shortcut_event(self, event) -> bool:
        event_type = event.type()
        flags = event.modifierFlags()
        if event_type == AppKit.NSEventTypeKeyDown and event.keyCode() == 49:
            if flags & AppKit.NSEventModifierFlagOption:
                self.showPanel_(None)
                return True
        if event_type == AppKit.NSEventTypeFlagsChanged and flags & AppKit.NSEventModifierFlagCommand:
            now = time.monotonic()
            if now - self.last_command_down < 0.42:
                self.showPanel_(None)
                self.last_command_down = 0.0
                return True
            self.last_command_down = now
        return False

    def _run_js(self, code: str) -> None:
        if self.webview is not None:
            self.webview.evaluateJavaScript_completionHandler_(code, None)

    def pollSetupOverlay_(self, _timer):
        if self.setup_webview is None or self.setup_window is None:
            return

        def completion(result, _error):
            should_close = bool(result)
            if should_close:
                if self.setup_window is not None:
                    self.setup_window.orderOut_(None)
                self.setup_window = None
                self.setup_webview = None
                self.showPanel_(None)
            else:
                self._schedule_setup_poll()

        self.setup_webview.evaluateJavaScript_completionHandler_(
            "window.locusSetupShouldClose ? window.locusSetupShouldClose() : false;",
            completion,
        )

    def showPanel_(self, _sender):
        if self.panel is None:
            return
        self.panel.setFrame_display_(self._screen_rect_for_panel(), True)
        self.panel.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self._run_js("window.focusLocusSurface && window.focusLocusSurface();")

    def togglePanel_(self, _sender):
        if self.panel is not None and self.panel.isVisible():
            self.panel.orderOut_(None)
        else:
            self.showPanel_(None)

    def openSettings_(self, _sender):
        self.showPanel_(None)
        self._run_js("window.openSettingsCenter && window.openSettingsCenter();")

    def openPlugins_(self, _sender):
        self.showPanel_(None)
        self._run_js("window.openPluginCenter && window.openPluginCenter();")

    def openSafety_(self, _sender):
        self.showPanel_(None)
        self._run_js("window.openSafetyCenter && window.openSafetyCenter();")

    def windowDidResignKey_(self, notification):
        if notification.object() is not self.panel or self.webview is None:
            return

        def completion(result, _error):
            should_poof = bool(result)
            if self.panel is None:
                return
            if should_poof:
                self.panel.orderOut_(None)
            else:
                self.panel.orderBack_(None)

        self.webview.evaluateJavaScript_completionHandler_(
            "window.locusShouldPoof ? window.locusShouldPoof() : true;",
            completion,
        )

    def applicationShouldTerminateAfterLastWindowClosed_(self, _app):
        return False


def main() -> None:
    app = AppKit.NSApplication.sharedApplication()
    delegate = LocusAppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    app.run()


if __name__ == "__main__":
    if sys.platform != "darwin":
        _fallback_browser()
        raise SystemExit(0)
    main()
