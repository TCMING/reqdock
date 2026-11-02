"""Native WebView2 benchmark child; only operates its disposable QA window."""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


def write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source / "src"))
    import webview

    from api_post import desktop

    root = args.workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.name.startswith("memory-bench-"):
        raise ValueError("Disposable memory-bench workspace required")
    (root / "access.json").write_text(
        '{"schema_version":1,"mode":"local"}', encoding="utf-8"
    )
    _job = desktop.ChildJob()
    service = desktop.OwnedService(root, args.port)
    try:
        service.start()
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "ReqDock Memory QA",
            "http://127.0.0.1:" + str(args.port) + "/",
            width=1360,
            height=920,
            min_size=(900, 650),
            background_color="#f4f6f7",
            text_select=True,
            focus=False,
        )
        initialized = threading.Event()
        policy = []

        def loaded():
            if initialized.is_set():
                return
            attach = getattr(desktop, "attach_memory_policy", None)
            if attach:
                policy.append(attach(window))
            initialized.set()

        window.events.loaded += loaded

        def native(action):
            from System import Func, Object

            return window.native.Invoke(Func[Object](action))

        def control():
            initialized.wait(30)
            write(
                root / "probe-ready.json",
                {
                    "pid": os.getpid(),
                    "policy_available": bool(policy and policy[0] is not None),
                },
            )
            previous = None
            while True:
                path = root / "probe-command.json"
                if not path.exists():
                    time.sleep(0.1)
                    continue
                try:
                    value = json.loads(path.read_text("utf-8"))
                except ValueError:
                    time.sleep(0.1)
                    continue
                if value.get("seq") == previous:
                    time.sleep(0.1)
                    continue
                previous = value.get("seq")
                command = value.get("command")
                try:
                    if command == "history":
                        result = window.evaluate_js(
                            "(()=>{const b=Array.from(document.querySelectorAll('.nav-item')).find(b=>b.textContent.includes('请求记录'));if(b)b.click();return !!b;})()"
                        )
                    elif command == "activate":
                        native(lambda: window.native.Activate())
                        result = True
                    elif command == "minimize":
                        window.minimize()
                        result = True
                    elif command == "restore":
                        window.restore()
                        native(lambda: window.native.Activate())
                        result = True
                    elif command == "state":
                        result = {
                            "target": str(
                                native(
                                    lambda: (
                                        window.native.webview.CoreWebView2.MemoryUsageTargetLevel
                                    )
                                )
                            ),
                            "minimized": bool(
                                native(
                                    lambda: (
                                        str(window.native.WindowState) == "Minimized"
                                    )
                                )
                            ),
                        }
                    elif command == "page":
                        result = window.evaluate_js(
                            "({title:document.querySelector('h1')?.textContent,rows:document.querySelectorAll('.history-panel tbody tr').length})"
                        )
                    elif command == "open_record":
                        result = window.evaluate_js(
                            "(()=>{document.querySelector('.history-panel tbody button')?.click();return true;})()"
                        )
                    elif command == "response":
                        result = window.evaluate_js(
                            "({history:!!document.querySelector('.history-request-toolbar'),body:!!document.querySelector('.response-content pre')?.textContent.includes('memory-payload')})"
                        )
                    elif command == "close":
                        write(
                            root / "probe-result.json",
                            {"seq": previous, "result": True},
                        )
                        window.destroy()
                        return
                    else:
                        raise ValueError("Unknown probe command")
                    write(
                        root / "probe-result.json", {"seq": previous, "result": result}
                    )
                except Exception as exc:  # noqa: BLE001 - report dynamic CLR probe failures without data.
                    write(
                        root / "probe-result.json",
                        {"seq": previous, "error": type(exc).__name__},
                    )

        webview.start(
            control,
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(root / "profile"),
            icon=str(args.source / "assets/api-lab.ico"),
        )
    finally:
        service.close()
        from api_post.security import Vault

        vault = Vault(root)
        profiles = root / "sql/connections.json"
        for value in (
            json.loads(profiles.read_text("utf-8")) if profiles.exists() else []
        ):
            vault.delete("sql-password:" + value["id"])
        vault.delete("local_master")


if __name__ == "__main__":
    main()
