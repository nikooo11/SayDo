"""Floating dictation pill: a persistent idle bar with the hotkey hint
(optional, like a 'Flow Bar') that expands into a live amber waveform while
the push-to-talk chord is held. Pure AppKit via pyobjc."""
import collections

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMutableParagraphStyle,
    NSPanel,
    NSParagraphStyleAttributeName,
    NSScreen,
    NSTimer,
    NSView,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSString

BAR_COUNT = 24
REC_W, REC_H = 260, 74
IDLE_W, IDLE_H = 200, 34
DASH_W, DASH_H = 58, 8
BOTTOM_Y = 8  # gap above the usable screen bottom (clears the Dock when shown)
LABEL_H = 20  # bottom strip reserved for the label while recording

AMBER = (0.93, 0.65, 0.25)


def _centered(attrs_size, bold=True):
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(1)
    font = NSFont.boldSystemFontOfSize_(attrs_size) if bold \
        else NSFont.systemFontOfSize_(attrs_size)
    return {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: NSColor.whiteColor(),
        NSParagraphStyleAttributeName: style,
    }


class WaveView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.levels = collections.deque([0.02] * BAR_COUNT, maxlen=BAR_COUNT)
        self.recording = False
        self.dash = True   # collapsed idle state; expands on hover
        self.hint = ""
        self.on_click = None
        return self

    def mouseDown_(self, _event):
        # expanded idle pill acts as a button: open the dashboard
        if not self.recording and not self.dash and self.on_click is not None:
            self.on_click()

    def drawRect_(self, rect):
        b = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.04, 0.94).setFill()
        radius = 16 if self.recording else b.size.height / 2
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, radius, radius).fill()

        if not self.recording:
            if self.dash:
                # collapsed: slim dash with a faint amber dot, nearly invisible
                NSColor.colorWithCalibratedRed_green_blue_alpha_(*AMBER, 0.9).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    ((b.size.width / 2 - 2.5, b.size.height / 2 - 2.5), (5, 5))).fill()
                return
            # expanded idle pill: amber dot + hotkey hint
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*AMBER, 1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(((16, b.size.height / 2 - 3), (6, 6))).fill()
            NSString.stringWithString_(self.hint).drawInRect_withAttributes_(
                ((0, (b.size.height - 14) / 2 - 1), (b.size.width, 15)),
                _centered(11.5, bold=False),
            )
            return

        # recording: amber waveform above the label strip
        pad, gap = 16, 3
        bw = (b.size.width - 2 * pad - gap * (BAR_COUNT - 1)) / BAR_COUNT
        wave_h = b.size.height - LABEL_H
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*AMBER, 1.0).setFill()
        for i, lv in enumerate(self.levels):
            bh = max(4, min(1.0, lv * 10) * (wave_h - 18))
            x = pad + i * (bw + gap)
            y = LABEL_H + (wave_h - bh) / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((x, y), (bw, bh)), bw / 2, bw / 2
            ).fill()
        NSString.stringWithString_("SayDo").drawInRect_withAttributes_(
            ((0, 4), (b.size.width, 14)), _centered(11)
        )


class Overlay:
    """show()/hide() must be called on the main thread (use AppHelper.callAfter)."""

    def __init__(self, level_fn, hint="", flow_bar=False):
        self.level_fn = level_fn
        self.flow_bar = flow_bar
        self.hint = hint
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._frame("rec"),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(25)  # above normal windows (status level)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setCollectionBehavior_(1)  # visible on all Spaces
        self.view = WaveView.alloc().initWithFrame_(((0, 0), (REC_W, REC_H)))
        self.view.hint = hint
        self.panel.setContentView_(self.view)
        self._timer = None
        self._hover_timer = None
        if flow_bar:
            self._idle()

    def _frame(self, kind):
        screen = NSScreen.mainScreen()
        frame, visible = screen.frame(), screen.visibleFrame()
        w, h = {"rec": (REC_W, REC_H), "idle": (IDLE_W, IDLE_H),
                "dash": (DASH_W, DASH_H)}[kind]
        return (((frame.size.width - w) / 2, visible.origin.y + BOTTOM_Y), (w, h))

    def _animate_to(self, kind):
        # setFrame:display:animate: gives a short smooth grow/shrink
        self.panel.setFrame_display_animate_(self._frame(kind), True, True)

    def _idle(self, animate=False):
        self.view.recording = False
        self.view.dash = True
        self.view.setNeedsDisplay_(True)
        if animate:
            self._animate_to("dash")
        else:
            self.panel.setFrame_display_(self._frame("dash"), True)
        self.panel.orderFrontRegardless()
        self._start_hover_watch()

    def _start_hover_watch(self):
        if self._hover_timer is None:
            self._hover_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.12, True, self._hover_tick)

    def _stop_hover_watch(self):
        if self._hover_timer is not None:
            self._hover_timer.invalidate()
            self._hover_timer = None

    def _hover_tick(self, _timer):
        # the panel ignores mouse events so it never blocks clicks; hover is
        # detected by polling the global cursor position against its frame
        from AppKit import NSEvent
        loc = NSEvent.mouseLocation()
        f = self.panel.frame()
        pad = 10
        inside = (f.origin.x - pad <= loc.x <= f.origin.x + f.size.width + pad
                  and f.origin.y - pad <= loc.y <= f.origin.y + f.size.height + pad)
        if inside == (not self.view.dash):
            return
        self.view.dash = not inside
        # clickable only while expanded, so the dash never swallows clicks
        self.panel.setIgnoresMouseEvents_(self.view.dash)
        self.view.setNeedsDisplay_(True)
        self._animate_to("dash" if self.view.dash else "idle")

    def set_flow_bar(self, on):
        self.flow_bar = on
        if not self.view.recording:
            if on:
                self._idle()
            else:
                self._stop_hover_watch()
                self.panel.orderOut_(None)

    def _tick(self, _timer):
        self.view.levels.append(self.level_fn())
        self.view.setNeedsDisplay_(True)

    def set_on_click(self, fn):
        self.view.on_click = fn

    def show(self):
        self.panel.setIgnoresMouseEvents_(True)
        self._stop_hover_watch()
        self.view.levels.extend([0.02] * BAR_COUNT)
        self.view.recording = True
        self.view.dash = False
        self.panel.orderFrontRegardless()
        self._animate_to("rec")
        self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1 / 30.0, True, self._tick
        )

    def hide(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self.flow_bar:
            self._idle(animate=True)
        else:
            self.panel.orderOut_(None)
