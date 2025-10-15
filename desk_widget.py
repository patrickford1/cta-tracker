#!/usr/bin/env python3
"""
Cocoa-based floating widget (no Tk) to show CTA next departures.
Requires: pip install pyobjc

Runs against your local FastAPI endpoints:
  /api/departures  (trains)
  /api/bus         (buses)
"""

import json
import threading
import urllib.request

from Foundation import NSObject, NSTimer
from Quartz import CGColorCreateGenericRGB
try:
    from Quartz import CABasicAnimation
except ImportError:
    CABasicAnimation = None
from AppKit import (
    NSApplication, NSApp, NSWindow, NSTextField, NSScreen,
    NSFloatingWindowLevel, NSColor, NSFont, NSView, NSStackView,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskFullSizeContentView,
    NSWindowCloseButton, NSWindowMiniaturizeButton, NSWindowZoomButton, NSBackingStoreBuffered,
    NSLayoutAttributeCenterY, NSLayoutAttributeCenterX,
    NSUserInterfaceLayoutOrientationHorizontal, NSUserInterfaceLayoutOrientationVertical,
    NSImage, NSImageView, NSBezierPath, NSImageScaleProportionallyDown
)

APP = "http://127.0.0.1:8000"
TRAIN = f"{APP}/api/departures"
BUS   = f"{APP}/api/bus"

TRAIN_PREFIX = "🚇"
BUS_ICON = "🚌"
BUS_ROUTE = "9️⃣"
BUS_PREFIX = f"{BUS_ICON} {BUS_ROUTE}"

ROUTE_COLORS = {
    "RED": (198/255, 12/255, 48/255, 1.0),        # #C60C30
    "BLUE": (0/255, 161/255, 222/255, 1.0),       # #00A1DE
    "BRN": (98/255, 54/255, 27/255, 1.0),         # #62361B
    "BROWN": (98/255, 54/255, 27/255, 1.0),
    "PUR": (82/255, 35/255, 152/255, 1.0),        # Support variant
    "P": (82/255, 35/255, 152/255, 1.0),          # Purple
    "PURPLE": (82/255, 35/255, 152/255, 1.0),
    "PINK": (226/255, 126/255, 166/255, 1.0),     # #E27EA6
    "PNK": (226/255, 126/255, 166/255, 1.0),
    "GREEN": (0/255, 155/255, 58/255, 1.0),       # #009B3A
    "G": (0/255, 155/255, 58/255, 1.0),
    "ORANGE": (249/255, 70/255, 28/255, 1.0),     # #F9461C
    "ORG": (249/255, 70/255, 28/255, 1.0),
    "YELLOW": (249/255, 227/255, 0/255, 1.0),     # #F9E300
    "Y": (249/255, 227/255, 0/255, 1.0),
    "PEXP": (82/255, 35/255, 152/255, 1.0),       # Purple Express
}

UPDATE_SECONDS = 30


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
            return {"ok": True, "json": data}
    except Exception as e:
        print("desk_widget fetch error:", url, e)
        return {"ok": False, "error": str(e)}


def next_two_mins(data):
    xs = data.get("data", []) if isinstance(data, dict) else []
    mins = []
    for x in xs:
        m = x.get("minutes")
        if isinstance(m, int) and m >= 0:
            mins.append(m)
        elif isinstance(m, str) and m.isdigit():
            val = int(m)
            if val >= 0:
                mins.append(val)
    mins.sort()
    return mins[:2]


def fmt(m):
    if m is None:
        return "—"
    if m == 0:
        return "DUE"
    return f"{m}m"


def fmt_val(v):
    if v == "ERR":
        return "ERR"
    return fmt(v)


def fmt_list(arr):
    if not arr:
        return "n/a"
    return ", ".join(fmt(m) for m in arr)


class Controller(NSObject):
    def _font_or_default(self, name, size, fallback_family):
        font = NSFont.fontWithName_size_(name, size)
        if font is None and fallback_family:
            font = NSFont.fontWithName_size_(fallback_family, size)
        if font is None:
            return NSFont.boldSystemFontOfSize_(size)
        return font

    def applicationDidFinishLaunching_(self, notification):
        # Create window near top-left
        screen = NSScreen.mainScreen().visibleFrame()
        width, height = 340, 59
        x = int(screen.origin.x)
        y = int(screen.origin.y + screen.size.height - height)
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((x, y), (width, height)), style, NSBackingStoreBuffered, False)
        # Style: rounded, semi-translucent, CTA blue background
        self.window.setOpaque_(False)
        self.window.setAlphaValue_(0.95)
        try:
            self.window.setTitlebarAppearsTransparent_(True)
            self.window.setTitleVisibility_(1)  # hide title text
            current_mask = self.window.styleMask()
            self.window.setStyleMask_(current_mask | NSWindowStyleMaskFullSizeContentView)
            for button in (
                NSWindowCloseButton,
                NSWindowMiniaturizeButton,
                NSWindowZoomButton,
            ):
                btn = self.window.standardWindowButton_(button)
                if btn is not None:
                    btn.setHidden_(True)
        except Exception:
            pass
        cv = self.window.contentView()
        cv.setWantsLayer_(True)
        CTA_BLUE_CG = CGColorCreateGenericRGB(0.0, 0.47, 0.78, 1.0)
        cv.layer().setBackgroundColor_(CTA_BLUE_CG)
        cv.layer().setCornerRadius_(12.0)
        cv.layer().setShadowOpacity_(0.3)
        cv.layer().setShadowRadius_(8.0)
        cv.layer().setShadowOffset_((0.0, -1.0))
        self.window.setTitle_("CTA Departures")
        self.window.setLevel_(NSFloatingWindowLevel)  # always on top
        self.train_font = self._font_or_default("SFProDisplay-CondensedBold", 22, "HelveticaNeue-CondensedBold")
        self.train_label = NSTextField.alloc().initWithFrame_(((0, 0), (0, 0)))
        self.train_label.setEditable_(False)
        self.train_label.setBordered_(False)
        self.train_label.setBackgroundColor_(NSColor.clearColor())
        self.train_label.setFont_(self.train_font)
        self.train_label.setTextColor_(NSColor.whiteColor())
        self.train_label.setAlignment_(1)
        self.train_label.setStringValue_(TRAIN_PREFIX)
        # Train arrivals inline stack
        self.train_times_stack = NSStackView.alloc().init()
        self.train_times_stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
        self.train_times_stack.setAlignment_(NSLayoutAttributeCenterY)
        self.train_times_stack.setSpacing_(0.0)
        self.train_times_stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        placeholder = self._make_display_label("Loading…")
        self.train_times_stack.addArrangedSubview_(placeholder)
        # Bus section
        self.bus_prefix_label = NSTextField.alloc().initWithFrame_(((0, 0), (0, 0)))
        self.bus_prefix_label.setEditable_(False)
        self.bus_prefix_label.setBordered_(False)
        self.bus_prefix_label.setBackgroundColor_(NSColor.clearColor())
        self.bus_prefix_label.setFont_(self.train_font)
        self.bus_prefix_label.setTextColor_(NSColor.whiteColor())
        self.bus_prefix_label.setAlignment_(0)
        self.bus_prefix_label.setStringValue_(BUS_ICON)
        self.bus_route_label = NSTextField.alloc().initWithFrame_(((0, 0), (0, 0)))
        self.bus_route_label.setEditable_(False)
        self.bus_route_label.setBordered_(False)
        self.bus_route_label.setBackgroundColor_(NSColor.clearColor())
        self.bus_route_label.setFont_(self.train_font)
        self.bus_route_label.setTextColor_(NSColor.whiteColor())
        self.bus_route_label.setAlignment_(0)
        self.bus_route_label.setStringValue_(BUS_ROUTE)
        self.bus_route_label.setWantsLayer_(True)
        self.bus_times_stack = NSStackView.alloc().init()
        self.bus_times_stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
        self.bus_times_stack.setAlignment_(NSLayoutAttributeCenterY)
        self.bus_times_stack.setSpacing_(0.0)
        self.bus_times_stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        bus_placeholder = self._make_display_label("Loading…")
        self.bus_times_stack.addArrangedSubview_(bus_placeholder)
        # Scheduled badge container (badges added dynamically per scheduled run)
        self.badge_container = NSView.alloc().initWithFrame_(((0, 0), (0, 0)))
        self.badge_container.setWantsLayer_(False)
        self.badge_container.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.badge_container.setHidden_(True)
        icon_size = 18
        self.clock_icon_size = icon_size
        self.clock_icon_image = self._make_clock_icon(icon_size)
        self.radio_icon_image = self._make_radio_icon(icon_size)
        # Brown Line bullet (CTA Brown #62361B)
        self.bullet = NSView.alloc().initWithFrame_(((0, 0), (0, 0)))
        self.bullet.setWantsLayer_(True)
        BROWN_CG = CGColorCreateGenericRGB(0.384, 0.212, 0.106, 1.0)
        self.default_bullet_color = BROWN_CG
        self.bullet.layer().setBackgroundColor_(self.default_bullet_color)
        bullet_size = self.train_font.pointSize()
        self.bullet.layer().setCornerRadius_(bullet_size / 2.0)
        # Layout: center everything in the window
        self.train_label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.bullet.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.train_times_stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.bus_prefix_label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.bus_route_label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.bus_times_stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.row_stack = NSStackView.stackViewWithViews_([
            self.train_label,
            self.bullet,
            self.train_times_stack,
            self.bus_prefix_label,
            self.bus_route_label,
            self.bus_times_stack,
        ])
        self.row_stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
        self.row_stack.setAlignment_(NSLayoutAttributeCenterY)
        self.row_stack.setSpacing_(8.0)
        self.row_stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        try:
            # Remove padding between the train glyph and brown bullet for a tighter grouping.
            self.row_stack.setCustomSpacing_afterView_(0.0, self.train_label)
            self.row_stack.setCustomSpacing_afterView_(10.0, self.bullet)
            self.row_stack.setCustomSpacing_afterView_(12.0, self.train_times_stack)
            self.row_stack.setCustomSpacing_afterView_(4.0, self.bus_prefix_label)
            self.row_stack.setCustomSpacing_afterView_(8.0, self.bus_route_label)
        except Exception:
            pass
        self.root_stack = NSStackView.stackViewWithViews_([self.row_stack, self.badge_container])
        self.root_stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        self.root_stack.setAlignment_(NSLayoutAttributeCenterX)
        self.root_stack.setSpacing_(6.0)
        self.root_stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        cv.addSubview_(self.root_stack)
        self.bullet.widthAnchor().constraintEqualToConstant_(bullet_size).setActive_(True)
        self.bullet.heightAnchor().constraintEqualToConstant_(bullet_size).setActive_(True)
        self.root_stack.centerXAnchor().constraintEqualToAnchor_(cv.centerXAnchor()).setActive_(True)
        self.root_stack.topAnchor().constraintEqualToAnchor_constant_(cv.topAnchor(), 4.0).setActive_(True)
        self.train_minute_views = []
        self.bus_minute_views = []
        self.train_pulse_ready = True
        self.bus_pulse_ready = True
        self.train_pulse_view = None
        self.bus_pulse_view = None
        self.pulse_timers = {}
        self.status_icons = []
        self.status_constraints = []
        self.window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)
        # Start the repeating timer
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            UPDATE_SECONDS, self, 'tick:', None, True
        )
        # Kick off immediately
        self.tick_(None)

    def _make_display_label(self, text):
        label = NSTextField.alloc().initWithFrame_(((0, 0), (0, 0)))
        label.setEditable_(False)
        label.setBordered_(False)
        label.setBackgroundColor_(NSColor.clearColor())
        label.setFont_(self.train_font)
        label.setTextColor_(NSColor.whiteColor())
        label.setAlignment_(0)
        label.setStringValue_(text)
        label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        label.setWantsLayer_(True)
        return label

    def _make_clock_icon(self, size):
        image = NSImage.alloc().initWithSize_((size, size))
        image.lockFocus()
        NSColor.clearColor().set()
        NSBezierPath.bezierPathWithRect_(((0, 0), (size, size))).fill()
        NSColor.whiteColor().set()
        line_width = 1.8
        inset = 1.5
        circle = NSBezierPath.bezierPathWithOvalInRect_(((inset, inset), (size - inset * 2, size - inset * 2)))
        circle.setLineWidth_(line_width)
        circle.stroke()

        minute_hand = NSBezierPath.bezierPath()
        minute_hand.moveToPoint_((size / 2.0, size / 2.0))
        minute_hand.lineToPoint_((size / 2.0, size * 0.78))
        minute_hand.setLineWidth_(line_width)
        minute_hand.stroke()

        hour_hand = NSBezierPath.bezierPath()
        hour_hand.moveToPoint_((size / 2.0, size / 2.0))
        hour_hand.lineToPoint_((size * 0.72, size / 2.0))
        hour_hand.setLineWidth_(line_width)
        hour_hand.stroke()

        image.unlockFocus()
        image.setTemplate_(True)
        return image

    def _make_radio_icon(self, size):
        image = NSImage.alloc().initWithSize_((size, size))
        image.lockFocus()
        NSColor.clearColor().set()
        NSBezierPath.bezierPathWithRect_(((0, 0), (size, size))).fill()
        NSColor.whiteColor().set()
        center = (size * 0.35, size * 0.35)
        line_width = max(size * 0.12, 1.5)
        radii = (size * 0.18, size * 0.32, size * 0.46)
        for radius in radii:
            arc = NSBezierPath.bezierPath()
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                center, radius, 0.0, 90.0, False
            )
            arc.setLineWidth_(line_width)
            arc.stroke()
        image.unlockFocus()
        image.setTemplate_(True)
        return image

    def _set_train_bullet_color(self, route):
        color = self.default_bullet_color
        if route:
            key = str(route).strip().upper()
            lookup_keys = [key]
            if len(key) > 3:
                lookup_keys.append(key[:3])
            for k in lookup_keys:
                if k in ROUTE_COLORS:
                    rgba = ROUTE_COLORS[k]
                    color = CGColorCreateGenericRGB(rgba[0], rgba[1], rgba[2], rgba[3])
                    break
        self.bullet.layer().setBackgroundColor_(color)

    def _coerce_minutes(self, raw, text):
        if isinstance(raw, (int, float)):
            try:
                return int(raw)
            except Exception:
                return None
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.isdigit():
                return int(raw)
        if isinstance(text, str):
            stripped = text.strip().upper()
            if stripped == "DUE":
                return 0
            if stripped.endswith("M"):
                val = stripped[:-1]
                if val.isdigit():
                    return int(val)
        return None

    def _clear_train_times_views(self):
        for view in list(self.train_times_stack.arrangedSubviews()):
            self.train_times_stack.removeArrangedSubview_(view)
            view.removeFromSuperview()

    def _update_train_times_views(self, items):
        self._clear_train_times_views()
        self.train_minute_views = []
        if not items:
            self.train_times_stack.addArrangedSubview_(self._make_display_label("n/a"))
            return
        for idx, item in enumerate(items):
            text = str(item.get("text", "—"))
            if idx < len(items) - 1:
                text += " "
            minute_label = self._make_display_label(text)
            self.train_times_stack.addArrangedSubview_(minute_label)
            self.train_minute_views.append((minute_label, item))

    def _clear_bus_times_views(self):
        for view in list(self.bus_times_stack.arrangedSubviews()):
            self.bus_times_stack.removeArrangedSubview_(view)
            view.removeFromSuperview()

    def _update_bus_times_views(self, items, fallback_text):
        self._clear_bus_times_views()
        self.bus_minute_views = []
        if not items:
            text = fallback_text if fallback_text else "n/a"
            self.bus_times_stack.addArrangedSubview_(self._make_display_label(text))
            return
        for idx, item in enumerate(items):
            text = str(item.get("text", "—"))
            if idx < len(items) - 1:
                text += " "
            minute_label = self._make_display_label(text)
            self.bus_times_stack.addArrangedSubview_(minute_label)
            self.bus_minute_views.append((minute_label, item))

    def _make_status_icon(self, icon_type):
        image = self.clock_icon_image if icon_type == "clock" else self.radio_icon_image
        view = NSImageView.alloc().initWithFrame_(((0, 0), (self.clock_icon_size, self.clock_icon_size)))
        view.setImage_(image)
        view.setImageScaling_(NSImageScaleProportionallyDown)
        view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        view.setHidden_(False)
        return view

    def _clear_status_badges(self):
        self.badge_container.setHidden_(True)
        for constraint in self.status_constraints:
            constraint.setActive_(False)
        self.status_constraints = []
        for icon in self.status_icons:
            icon.removeFromSuperview()
        self.status_icons = []

    def _update_status_badges(self, status_items):
        self._clear_status_badges()
        if not status_items:
            return
        container = self.badge_container
        container.setHidden_(False)
        for entry in status_items:
            view = entry.get("view")
            icon_type = entry.get("icon")
            if view is None:
                continue
            badge = self._make_status_icon(icon_type)
            container.addSubview_(badge)
            center = badge.centerXAnchor().constraintEqualToAnchor_(view.centerXAnchor())
            top = badge.topAnchor().constraintEqualToAnchor_(container.topAnchor())
            bottom = badge.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor())
            for constraint in (center, top, bottom):
                constraint.setActive_(True)
                self.status_constraints.append(constraint)
            self.status_icons.append(badge)

    def _start_timer_pulse(self, view, key):
        if view is None:
            return
        if not view.wantsLayer():
            view.setWantsLayer_(True)
        layer = view.layer()
        if layer is not None:
            layer.setOpacity_(1.0)
        timer = self.pulse_timers.get(key)
        if timer is not None:
            info = timer.userInfo()
            if isinstance(info, dict):
                info["view"] = view
            return
        info = {"view": view, "direction": -0.04, "value": 1.0, "key": key, "pulses": 0, "max_pulses": 5}
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.05, self, 'pulseTick:', info, True)
        self.pulse_timers[key] = timer

    def _stop_timer_pulse(self, key, reset=True, automatic=False):
        timer = self.pulse_timers.pop(key, None)
        if timer is None:
            return
        timer.invalidate()
        info = timer.userInfo()
        if reset and isinstance(info, dict):
            view = info.get("view")
            if view is not None and view.wantsLayer():
                layer = view.layer()
                if layer is not None:
                    layer.setOpacity_(1.0)
        self._mark_pulse_finished(key, automatic)

    def pulseTick_(self, timer):
        info = timer.userInfo()
        if not isinstance(info, dict):
            timer.invalidate()
            return
        view = info.get("view")
        key = info.get("key")
        if view is None or not view.wantsLayer():
            timer.invalidate()
            if key is not None:
                self.pulse_timers.pop(key, None)
                self._mark_pulse_finished(key, True)
            return
        layer = view.layer()
        if layer is None:
            timer.invalidate()
            if key is not None:
                self.pulse_timers.pop(key, None)
                self._mark_pulse_finished(key, True)
            return
        value = info.get("value", 1.0) + info.get("direction", -0.04)
        direction = info.get("direction", -0.04)
        if value <= 0.3 or value >= 1.0:
            direction = -direction
            value = max(min(value, 1.0), 0.3)
            if value == 0.3:
                pulses = int(info.get("pulses", 0)) + 1
                info["pulses"] = pulses
                max_pulses = int(info.get("max_pulses", 5))
                if pulses >= max_pulses:
                    layer.setOpacity_(1.0)
                    if key is not None:
                        self._stop_timer_pulse(key, reset=False, automatic=True)
                    else:
                        timer.invalidate()
                    return
        info["value"] = value
        info["direction"] = direction
        layer.setOpacity_(value)

    def _mark_pulse_finished(self, key, automatic):
        if key == "pulse_train_label":
            self.train_pulse_view = None
        if key == "pulse_bus_label":
            self.bus_pulse_view = None

    def _apply_pulse(self, view, should_pulse, key):
        if view is None:
            self._stop_timer_pulse(key)
            return
        if CABasicAnimation is not None:
            self._stop_timer_pulse(key, reset=False)
            if not view.wantsLayer():
                view.setWantsLayer_(True)
            layer = view.layer()
            if layer is None:
                return
            if not should_pulse:
                layer.removeAnimationForKey_(key)
                layer.setOpacity_(1.0)
                return
            existing = layer.animationForKey_(key)
            if existing is not None:
                return
            layer.setOpacity_(1.0)
            anim = CABasicAnimation.animationWithKeyPath_("opacity")
            anim.setFromValue_(1.0)
            anim.setToValue_(0.3)
            anim.setDuration_(0.8)
            anim.setAutoreverses_(True)
            anim.setRepeatCount_(5)
            anim.setRemovedOnCompletion_(True)
            layer.addAnimation_forKey_(anim, key)
            return
        # Fallback: manual NSTimer pulse
        if should_pulse:
            self._start_timer_pulse(view, key)
        else:
            self._stop_timer_pulse(key)

    def _update_pulses(self):
        train_first_view = None
        train_first_minutes = None
        for view, item in self.train_minute_views:
            minutes = self._coerce_minutes(item.get("minutes"), item.get("text"))
            if minutes is not None:
                train_first_view = view
                train_first_minutes = minutes
                break
        train_threshold = 5
        within_train = train_first_minutes is not None and train_first_minutes <= train_threshold
        if within_train:
            if self.train_pulse_ready:
                self.train_pulse_view = train_first_view
                self._apply_pulse(self.bullet, True, "pulse_train")
                if train_first_view is not None:
                    self._apply_pulse(train_first_view, True, "pulse_train_label")
                self.train_pulse_ready = False
        else:
            if not self.train_pulse_ready:
                self._apply_pulse(self.bullet, False, "pulse_train")
                if self.train_pulse_view is not None:
                    self._apply_pulse(self.train_pulse_view, False, "pulse_train_label")
            self.train_pulse_ready = True
            self.train_pulse_view = None

        bus_threshold = 10
        bus_first_view = None
        bus_first_minutes = None
        for view, item in self.bus_minute_views:
            minutes = self._coerce_minutes(item.get("minutes"), item.get("text"))
            if minutes is not None:
                bus_first_view = view
                bus_first_minutes = minutes
                break
        within_bus = bus_first_minutes is not None and bus_first_minutes <= bus_threshold
        if within_bus:
            if self.bus_pulse_ready:
                self.bus_pulse_view = bus_first_view
                self._apply_pulse(self.bus_route_label, True, "pulse_bus_route")
                if bus_first_view is not None:
                    self._apply_pulse(bus_first_view, True, "pulse_bus_label")
                self.bus_pulse_ready = False
        else:
            if not self.bus_pulse_ready:
                self._apply_pulse(self.bus_route_label, False, "pulse_bus_route")
                if self.bus_pulse_view is not None:
                    self._apply_pulse(self.bus_pulse_view, False, "pulse_bus_label")
            self.bus_pulse_ready = True
            self.bus_pulse_view = None

    def tick_(self, _):
        # Fetch in a background thread to keep UI responsive
        threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self):
        t = fetch(TRAIN)
        b = fetch(BUS)

        if not t.get("ok"):
            tmins = []
            tcount = 0
            ttext = "ERR"
        else:
            tjson = t.get("json") or {}
            tmins = next_two_mins(tjson)
            tcount = len(tjson.get("data", [])) if isinstance(tjson, dict) else 0
            ttext = fmt_list(tmins)

        if not b.get("ok"):
            bmins = []
            bcount = 0
            btext = "ERR"
        else:
            bjson = b.get("json") or {}
            bmins = next_two_mins(bjson)
            bcount = len(bjson.get("data", [])) if isinstance(bjson, dict) else 0
            btext = fmt_list(bmins)

        train_items = []
        if tjson := t.get("json"):
            data = tjson.get("data", [])
            if isinstance(data, list) and tmins:
                used = set()
                for minute in tmins:
                    scheduled_flag = False
                    route_code = None
                    for idx, item in enumerate(data):
                        if idx in used:
                            continue
                        if not isinstance(item, dict):
                            continue
                        raw_minutes = item.get("minutes")
                        if isinstance(raw_minutes, str):
                            if raw_minutes.isdigit():
                                raw_minutes = int(raw_minutes)
                            else:
                                try:
                                    raw_minutes = int(float(raw_minutes))
                                except Exception:
                                    continue
                        if raw_minutes == minute:
                            scheduled_flag = bool(item.get("is_scheduled"))
                            route_code = item.get("route")
                            used.add(idx)
                            break
                    train_items.append({
                        "text": fmt(minute),
                        "scheduled": scheduled_flag,
                        "minutes": minute,
                        "route": route_code,
                    })
        train_scheduled = any(item.get("scheduled") for item in train_items)
        if train_items:
            train_text = ", ".join(item["text"] for item in train_items)
        else:
            train_items = [{"text": ttext, "scheduled": False, "minutes": None, "route": None}]
            train_text = ttext
        bus_items = [{"text": fmt(m), "minutes": m, "scheduled": False} for m in bmins] if bmins else []
        bus_times_text = btext
        log_text = f"{TRAIN_PREFIX} {train_text}   {BUS_PREFIX} {bus_times_text}"
        print("desk_widget:", log_text, "| train items:", tcount, "bus items:", bcount, "| sch:", train_scheduled)
        # Update UI on main thread
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            'updateLabel:', {
                "train_items": train_items,
                "bus_text": bus_times_text,
                "bus_items": bus_items,
                "train_is_scheduled": train_scheduled,
            }, False
        )

    def updateLabel_(self, payload):
        if isinstance(payload, dict):
            train_items = payload.get("train_items") or []
            bus_items = payload.get("bus_items") or []
            bus_times_text = payload.get("bus_text", "")
        else:
            train_items = [{"text": str(payload), "scheduled": False, "minutes": None, "route": None}]
            bus_items = []
            bus_times_text = ""
        bus_times_text = str(bus_times_text or "")
        self._update_train_times_views(train_items)
        first_route = None
        if train_items:
            first_item = train_items[0]
            if isinstance(first_item, dict):
                first_route = first_item.get("route")
        self._set_train_bullet_color(first_route)
        self._update_bus_times_views(bus_items, bus_times_text)
        status_items = []
        for view, item in self.train_minute_views:
            if not isinstance(item, dict):
                continue
            if item.get("minutes") is None:
                continue
            icon = "clock" if item.get("scheduled") else "radio"
            status_items.append({"view": view, "icon": icon})
        for view, item in self.bus_minute_views:
            if not isinstance(item, dict):
                continue
            if item.get("minutes") is None:
                continue
            status_items.append({"view": view, "icon": "radio"})
        self._update_status_badges(status_items)
        self._update_pulses()


if __name__ == "__main__":
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
    print("desk_widget: launching")
    delegate = Controller.alloc().init()
    app.setDelegate_(delegate)
    NSApp().run()
