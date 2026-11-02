import builtins

from api_post.webview_memory import IdleMemoryPolicy, attach


def test_idle_policy_restores_without_suspending_or_losing_state():
    applied = []
    scheduled = []
    cancelled = []
    p = IdleMemoryPolicy(
        applied.append, scheduled.append, lambda: cancelled.append(True)
    )
    p.activity(True, 10)
    assert scheduled == [10] and applied == []
    p.tick()
    assert applied == ["low"]
    p.activity(True)
    assert scheduled == [10]  # no recurring cleanup timer
    p.activity(False)
    assert applied == ["low", "normal"]
    p.tick()
    assert applied == ["low", "normal"]  # stale tick cannot lower an active view
    p.activity(True)
    p.close()
    count = len(cancelled)
    p.tick()
    p.close()
    p.activity(False)
    assert len(cancelled) == count and applied == ["low", "normal"]


def test_idle_policy_cancels_debounce_and_tolerates_unsupported_runtime(monkeypatch):
    scheduled = []

    def unavailable(target):
        raise RuntimeError("fixture unsupported API")

    p = IdleMemoryPolicy(unavailable, scheduled.append, lambda: None)
    p.activity(True)
    p.activity(False)
    p.tick()
    assert not p.disabled
    p.activity(True)
    p.tick()
    assert p.disabled
    p.activity(True)
    assert scheduled == [30, 30]
    original = builtins.__import__

    def imports(name, *a, **kw):
        if name == "System":
            raise ImportError("fixture no CLR")
        return original(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", imports)
    assert attach(object()) is None
