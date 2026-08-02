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
        self.hint = ""
        return self

    def drawRect_(self, rect):
        b = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.04, 0.94).setFill()
        radius = 16 if self.recording else b.size.height / 2
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, radius, radius).fill()

        if not self.recording:
            # idle pill: amber dot + hotkey hint
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
            self._frame(False),
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
        if flow_bar:
            self._idle()

    def _frame(self, recording):
        screen = NSScreen.mainScreen().frame()
        w, h = (REC_W, REC_H) if recording else (IDLE_W, IDLE_H)
        return (((screen.size.width - w) / 2, 110), (w, h))

    def _idle(self):
        self.view.recording = False
        self.panel.setFrame_display_(self._frame(False), True)
        self.view.setNeedsDisplay_(True)
        self.panel.orderFrontRegardless()

    def set_flow_bar(self, on):
        self.flow_bar = on
        if not self.view.recording:
            self._idle() if on else self.panel.orderOut_(None)

    def _tick(self, _timer):
        self.view.levels.append(self.level_fn())
        self.view.setNeedsDisplay_(True)

    def show(self):
        self.view.levels.extend([0.02] * BAR_COUNT)
        self.view.recording = True
        self.panel.setFrame_display_(self._frame(True), True)
        self.panel.orderFrontRegardless()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1 / 30.0, True, self._tick
        )

    def hide(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self.flow_bar:
            self._idle()
        else:
            self.panel.orderOut_(None)
