"""Event-driven WebView2 idle targets; never suspend JS or trim process working sets."""

import logging

logger = logging.getLogger(__name__)


class IdleMemoryPolicy:
    def __init__(self, apply, schedule, cancel):
        self.apply, self.schedule, self.cancel = apply, schedule, cancel
        self.inactive = False
        self.target = "normal"
        self.closed = False
        self.disabled = False

    def activity(self, inactive, delay=30):
        if self.closed or self.disabled:
            return
        self.inactive = inactive
        self.cancel()
        if inactive:
            if self.target != "low":
                self.schedule(delay)
        elif self.target != "normal":
            self.set_target("normal")

    def set_target(self, target):
        try:
            self.apply(target)
            self.target = target
        except Exception:  # noqa: BLE001 - optional CLR APIs differ across installed runtimes.
            # Unsupported/old runtimes keep their default behavior. No security flags.
            self.disabled = True
            self.cancel()
            logger.warning(
                "WebView2 idle memory target unavailable; keeping default behavior"
            )

    def tick(self):
        if self.closed:
            return
        self.cancel()
        if not self.closed and not self.disabled and self.inactive:
            self.set_target("low")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel()


def attach(window):
    """Attach once on the native UI thread after CoreWebView2 initialization."""
    try:
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2MemoryUsageTargetLevel as Level,
        )
        from System import Func, Object
        from System.Windows.Forms import FormWindowState, Timer
    except Exception:  # noqa: BLE001 - optional CLR APIs differ across installed runtimes.
        logger.warning(
            "WebView2 idle memory API unavailable; default behavior retained"
        )
        return None

    def setup():
        form = window.native
        core = form.webview.CoreWebView2
        if core is None or not hasattr(core, "MemoryUsageTargetLevel"):
            return None
        timer = Timer()
        state = {"active": bool(form.ContainsFocus)}

        def schedule(seconds):
            timer.Interval = int(seconds * 1000)
            timer.Start()

        def apply(target):
            core.MemoryUsageTargetLevel = Level.Low if target == "low" else Level.Normal

        policy = IdleMemoryPolicy(apply, schedule, timer.Stop)

        def assess(*_):
            hidden = not form.Visible or form.WindowState == FormWindowState.Minimized
            policy.activity(hidden or not state["active"], 10 if hidden else 30)

        def activated(*_):
            state["active"] = True
            assess()

        def deactivated(*_):
            state["active"] = False
            assess()

        def tick(*_):
            policy.tick()

        handlers = [
            (form, "Activated", activated),
            (form, "Deactivate", deactivated),
            (form, "Resize", assess),
            (form, "VisibleChanged", assess),
            (timer, "Tick", tick),
        ]

        def close(*_):
            policy.close()
            for obj, name, callback in handlers:
                getattr(obj, name).__isub__(callback)
            form.FormClosed -= close
            timer.Dispose()

        for obj, name, callback in handlers:
            getattr(obj, name).__iadd__(callback)
        form.FormClosed += close
        assess()
        # The existing form events own policy lifetime; no worker thread is created.
        return policy

    try:
        return window.native.Invoke(Func[Object](setup))
    except Exception:  # noqa: BLE001 - optional CLR APIs differ across installed runtimes.
        logger.warning(
            "WebView2 idle memory integration unavailable; default behavior retained"
        )
        return None
