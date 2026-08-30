"""DroneVisualizer launcher - a small Start/Stop window for Windows 11.

Double-click ``DroneVisualizer.bat`` (or this file) to open it. No console
window; uses only the Python standard library (Tkinter).

    [ Start ]  [ Stop ]  [ Open in browser ]      status: running :8750
    ------------------------------------------------------------------
    <live server log>

First run: if dependencies are missing it offers to install them into
``.venv`` for you.
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8750

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


# --------------------------------------------------------------------------- helpers
def venv_python() -> Path | None:
    for name in ("python.exe", "python"):
        p = ROOT / ".venv" / "Scripts" / name
        if p.exists():
            return p
    return None


def read_port() -> int:
    cfg = ROOT / "config.yaml"
    if not cfg.exists():
        cfg = ROOT / "config.example.yaml"
    try:
        in_server = False
        for line in cfg.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("server:"):
                in_server = True
                continue
            if in_server:
                if s.startswith("port:"):
                    return int(s.split(":", 1)[1].split("#")[0].strip())
                if s and not line.startswith((" ", "\t")):
                    break
    except Exception:
        pass
    return DEFAULT_PORT


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_alive(pid: int) -> bool:
    """True if a process with this PID exists. Windows-safe (os.kill would
    TerminateProcess for sig 0 on Windows, so use OpenProcess)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not h:
            return False
        still = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(still))
        ctypes.windll.kernel32.CloseHandle(h)
        return still.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def deps_ok(py: Path) -> bool:
    try:
        r = subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, httpx, selectolax, apscheduler"],
            cwd=ROOT, creationflags=CREATE_NO_WINDOW, capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


# --------------------------------------------------------------------------- app
class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.log_q: queue.Queue[str] = queue.Queue()
        self.port = read_port()
        self.py = venv_python() or Path(sys.executable)

        root.title("DroneVisualizer")
        root.geometry("760x460")
        root.minsize(560, 320)
        try:
            root.iconbitmap(default="")  # no icon file; ignore failure
        except Exception:
            pass

        bar = tk.Frame(root, padx=10, pady=8)
        bar.pack(fill="x")

        self.start_btn = tk.Button(bar, text="▶  Start", width=12,
                                   command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(bar, text="■  Stop", width=12,
                                  command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))
        self.open_btn = tk.Button(bar, text="Open in browser", width=16,
                                  command=self.open_browser)
        self.open_btn.pack(side="left", padx=(6, 0))

        self.status = tk.Label(bar, text="stopped", fg="#a33", anchor="e")
        self.status.pack(side="right")

        self.log = scrolledtext.ScrolledText(
            root, wrap="word", bg="#10141b", fg="#dfe6f0",
            insertbackground="#dfe6f0", font=("Consolas", 9), padx=8, pady=6,
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log.configure(state="disabled")

        self._log(f"project: {ROOT}")
        self._log(f"python : {self.py}")
        if port_is_open(self.port):
            self._log(f"note: something is already listening on :{self.port}")
            self._set_running(external=True)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(150, self._drain_log)
        root.after(1000, self._watch)

    # -- logging --------------------------------------------------------------
    def _log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_log(self) -> None:
        try:
            while True:
                self._log(self.log_q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._drain_log)

    # -- state --------------------------------------------------------------
    def _set_running(self, external: bool = False) -> None:
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled" if external else "normal")
        txt = f"running :{self.port}" + ("  (external)" if external else "")
        self.status.configure(text=txt, fg="#2a9d8f")

    def _set_stopped(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="stopped", fg="#a33")

    # -- single-instance guard ---------------------------------------------
    def _lock_path(self):
        return ROOT / "data" / ".server.pid"

    def _server_pid(self) -> int | None:
        """PID from the lock file, but only if that process is still alive."""
        try:
            pid = int(self._lock_path().read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if pid_alive(pid) else None

    def _take_lock(self, pid: int) -> None:
        p = self._lock_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(pid))

    def _release_lock(self) -> None:
        try:
            self._lock_path().unlink()
        except OSError:
            pass

    # -- actions --------------------------------------------------------------
    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        other = self._server_pid()
        if other is not None or port_is_open(self.port):
            self._log(f"server already running (pid {other or '?'}) - opening browser")
            self._set_running(external=True)
            self.open_browser()
            return
        if not deps_ok(self.py):
            if messagebox.askyesno(
                "Install dependencies",
                "Python packages are missing.\n\nInstall them into .venv now?\n"
                "(one-time, needs internet)",
            ):
                self._install_deps()
            return
        self._spawn()

    def _install_deps(self) -> None:
        self.start_btn.configure(state="disabled")
        self.status.configure(text="installing…", fg="#e9a")

        def work() -> None:
            try:
                p = subprocess.Popen(
                    [str(self.py), "-m", "pip", "install", "-r", "requirements.txt"],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                for line in p.stdout:            # type: ignore[union-attr]
                    self.log_q.put(line)
                p.wait()
                self.log_q.put(f"pip finished ({p.returncode})")
            except Exception as exc:             # noqa: BLE001
                self.log_q.put(f"pip failed: {exc}")
            finally:
                self.root.after(0, self._set_stopped)

        threading.Thread(target=work, daemon=True).start()

    def _spawn(self) -> None:
        self._log("starting server…")
        try:
            self.proc = subprocess.Popen(
                [str(self.py), "-m", "dronevis", "run"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:                 # noqa: BLE001
            messagebox.showerror("DroneVisualizer", f"Could not start:\n{exc}")
            return

        self._take_lock(self.proc.pid)
        threading.Thread(target=self._pump, daemon=True).start()
        self._set_running()
        self.root.after(1500, lambda: self._log("UI: http://127.0.0.1:%d" % self.port))

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.log_q.put(line)
        rc = self.proc.wait()
        self._release_lock()
        self.log_q.put(f"server exited ({rc})")
        self.root.after(0, self._set_stopped)

    def stop(self) -> None:
        p = self.proc
        if not p or p.poll() is not None:
            self._set_stopped()
            return
        self._log("stopping…")
        try:
            import signal

            p.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            pass
        try:
            p.wait(timeout=6)
        except subprocess.TimeoutExpired:
            p.kill()
        self._set_stopped()

    def open_browser(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}")

    # -- lifecycle --------------------------------------------------------
    def _watch(self) -> None:
        if self.proc and self.proc.poll() is None:
            self._set_running()
        elif not port_is_open(self.port):
            self._set_stopped()
        self.root.after(1500, self._watch)

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            ans = messagebox.askyesnocancel(
                "DroneVisualizer",
                "The server is running.\n\nYes = stop it and quit\n"
                "No = leave it running and quit\nCancel = keep this window",
            )
            if ans is None:
                return
            if ans:
                self.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
