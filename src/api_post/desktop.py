"""On-demand WebView2 client. The UI owns its local service lifetime."""

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
import sys
import threading
import time
import msvcrt
import psutil
from .__main__ import call_local


class ChildJob:
    """Non-inherited job handle: a client crash also terminates its descendants."""

    def __init__(self):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("active", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in [
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                ]
            ]

        class Limits(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            w.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            w.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000
        if (
            not self.handle
            or not kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            or not kernel.AssignProcessToJobObject(self.handle, w.HANDLE(-1))
        ):
            raise OSError("Unable to establish desktop process lifetime")
        # Keep the only job handle until this process exits; do not close it early.


class OwnedService:
    def __init__(self, root, port=18765):
        self.root = root
        self.port = port
        self.process = None

    def start(self):
        try:
            call_local(self.root, self.port, "/api/auth/session")
        except Exception:
            pass
        else:
            call_local(self.root, self.port, "/api/service/stop", "POST")
            for _ in range(50):
                try:
                    call_local(self.root, self.port, "/api/auth/session")
                except Exception:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Existing service did not stop")
        python = Path(sys.executable).with_name("pythonw.exe")
        self.process = subprocess.Popen(
            [
                str(python),
                "-m",
                "api_post",
                "serve",
                "--workspace",
                str(self.root),
                "--port",
                str(self.port),
            ],
            cwd=Path(__file__).resolve().parents[2],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(75):
            if self.process.poll() is not None:
                raise RuntimeError("Service startup failed")
            try:
                call_local(self.root, self.port, "/api/auth/session")
                return
            except Exception:
                time.sleep(0.2)
        self.close()
        raise RuntimeError("Service startup timed out")

    def close(self):
        if not self.process:
            return
        try:
            call_local(self.root, self.port, "/api/service/stop", "POST")
        except Exception:
            pass
        try:
            self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            try:
                parent = psutil.Process(self.process.pid)
                for child in parent.children(recursive=True):
                    child.terminate()
                parent.terminate()
            except psutil.NoSuchProcess:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    icon_path = project / "assets" / "api-lab.ico"
    if not icon_path.is_file():
        raise FileNotFoundError("Application icon missing")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    if shell.SetCurrentProcessExplicitAppUserModelID("ReqDock.LocalClient") < 0:
        raise OSError("Unable to set desktop application identity")
    root = project / "workspace"
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.WARNING,
        handlers=[
            RotatingFileHandler(
                runtime / "desktop.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=2,
                encoding="utf-8",
            )
        ],
    )
    lock = (runtime / "desktop.lock").open("a+b")
    lock.write(b"1")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return
    _job = ChildJob()
    service = OwnedService(root)
    try:
        service.start()
        import webview

        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "ReqDock · 本地客户端",
            "http://127.0.0.1:18765/",
            width=1360,
            height=920,
            min_size=(900, 650),
            background_color="#f4f6f7",
            text_select=True,
            hidden=args.smoke_test,
        )
        initialized = threading.Event()
        paired = False

        def loaded():
            nonlocal paired
            if paired:
                return
            if window.get_current_url().rstrip("/") != "http://127.0.0.1:18765":
                return
            paired = True
            access = call_local(root, 18765, "/api/auth/session")
            if access.get("access_mode") == "local":
                initialized.set()
                return
            code = call_local(root, 18765, "/api/auth/code", "POST")["code"]
            js = (
                "(async()=>{const r=await fetch('/api/auth/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:"
                + json.dumps(code)
                + "})});if(r.ok)location.reload();})()"
            )
            window.run_js(js)
            initialized.set()

        window.events.loaded += loaded

        def smoke():
            if not args.smoke_test:
                return
            try:
                initialized.wait(20)
                time.sleep(2)
                proof = {
                    "initialized": initialized.is_set(),
                    "title": window.evaluate_js(
                        "document.querySelector('h1')?.textContent"
                    ),
                    "service_owned": service.process.poll() is None,
                    "desktop_process_private_mib": round(
                        psutil.Process().memory_info().private / 1024**2, 2
                    ),
                }
                from System.Drawing import Icon
                from System.Drawing.Imaging import ImageFormat
                from System.IO import MemoryStream

                def icon_png(value):
                    bitmap, stream = value.ToBitmap(), MemoryStream()
                    try:
                        bitmap.Save(stream, ImageFormat.Png)
                        return bytes(stream.ToArray())
                    finally:
                        bitmap.Dispose()
                        stream.Dispose()

                expected_icon = Icon(str(icon_path))
                try:
                    proof["window_icon_matches_asset"] = icon_png(window.native.Icon) == icon_png(expected_icon)
                    proof["window_icon_size"] = [window.native.Icon.Width, window.native.Icon.Height]
                    proof["shown_in_taskbar"] = bool(window.native.ShowInTaskbar)
                finally:
                    expected_icon.Dispose()
                app_id = ctypes.c_wchar_p()
                shell.GetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.POINTER(ctypes.c_wchar_p)]
                shell.GetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
                if shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(app_id)) == 0:
                    proof["app_user_model_id"] = app_id.value
                    ole = ctypes.WinDLL("ole32")
                    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
                    ole.CoTaskMemFree(ctypes.cast(app_id, ctypes.c_void_p))
                processes = [psutil.Process(), *psutil.Process().children(recursive=True)]
                proof["all_processes_private_mib"] = round(
                    sum(p.memory_info().private for p in processes if p.is_running())
                    / 1024**2,
                    2,
                )
                proof["process_count"] = len(processes)
                (root / "qa").mkdir(exist_ok=True)
                (root / "qa/desktop-smoke.json").write_text(
                    json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                logging.exception("Desktop smoke verification failed")
            finally:
                window.destroy()

        webview.start(
            smoke,
            gui="edgechromium",
            icon=str(icon_path),
            debug=False,
            private_mode=False,
            storage_path=str(runtime / "desktop-profile"),
        )
    finally:
        service.close()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.error("Desktop startup failed: %s", type(exc).__name__)
        ctypes.windll.user32.MessageBoxW(
            None,
            "客户端启动失败，请查看 workspace/runtime/desktop.log。",
            "ReqDock",
            0x10,
        )
        raise SystemExit(1)
