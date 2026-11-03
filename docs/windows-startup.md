# Windows startup and WMI

ReqDock initializes its platform compatibility policy in `api_post/__init__.py`,
before the desktop, service or CLI imports third-party dependencies.

CPython 3.12+ can synchronously query WMI when `platform.system()` computes its
cached OS information. Keyring dependencies can trigger this during import. If
the Windows WMI provider stalls, the client may hold its single-instance lock
without ever creating a window or listening on the local port.

On Windows, ReqDock disables CPython's optional `platform._wmi` probe when that
attribute exists. Python retains its built-in Windows API, registry and
environment fallbacks. This is process-local and inherited by neither other
applications nor the machine-wide Python installation; each ReqDock process
applies the same bootstrap independently. No polling worker or WMI timeout
thread is introduced. The private CPython hook is guarded for runtimes where it
is absent; compatibility must be checked when upgrading Python.

The Windows Credential Vault, DPAPI, workspace paths, local access policy, TLS
verification, SQL connection policies and saved API/SQL data are unchanged.
Do not fix this problem by resetting WMI, removing credentials or changing
Windows services.

`tests/test_windows_bootstrap.py` starts a fresh interpreter with an injected
WMI implementation that raises on every query. It checks platform detection,
entry-point imports and selection of the native Windows Vault backend within
a bounded subprocess. It removes inherited `PYTHONPATH` to ensure a temporary
`sitecustomize.py` cannot hide a regression.
