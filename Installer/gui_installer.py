"""
Eternal2x GUI Installer
One-click installer for buyers. Compiled to .exe/.app via PyInstaller.

If system Python is not found, automatically downloads and sets up a
private portable Python inside the plugin folder — works on both
Windows (official embeddable package) and macOS (python-build-standalone).
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# Python download sources
# ---------------------------------------------------------------------------

PYTHON_VERSION = "3.11.9"

# Windows: official embeddable zip from python.org
PYTHON_WIN_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

# macOS / Linux: portable builds from python-build-standalone
_PBS_TAG = "20240814"
_PBS_BASE = f"https://github.com/indygreg/python-build-standalone/releases/download/{_PBS_TAG}"
PYTHON_MAC_ARM64_URL = f"{_PBS_BASE}/cpython-{PYTHON_VERSION}+{_PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_MAC_X86_URL = f"{_PBS_BASE}/cpython-{PYTHON_VERSION}+{_PBS_TAG}-x86_64-apple-darwin-install_only.tar.gz"
PYTHON_LINUX_URL = f"{_PBS_BASE}/cpython-{PYTHON_VERSION}+{_PBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

DEFAULT_UPDATE_URL = (
    "https://raw.githubusercontent.com/"
    "Alitleis123/Eternal2x.com/main/update/latest.json"
)

# ---------------------------------------------------------------------------
# UI colors
# ---------------------------------------------------------------------------

BG = "#0b0b0f"
BG_CARD = "#121216"
FG = "#e2e2ea"
FG_DIM = "#6e6e82"
ACCENT = "#7c6fef"
BTN_BG = "#16161e"
SUCCESS = "#4ade80"
ERROR = "#ef5350"
BORDER = "#1e1e26"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_comp_dir() -> Path:
    if sys.platform.startswith("win"):
        programdata = os.environ.get("PROGRAMDATA", "C:\\ProgramData")
        return (
            Path(programdata)
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Fusion"
            / "Scripts"
            / "Comp"
            / "Eternal2x"
        )
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Fusion"
            / "Scripts"
            / "Comp"
            / "Eternal2x"
        )
    return (
        Path.home()
        / ".local"
        / "share"
        / "DaVinciResolve"
        / "Fusion"
        / "Scripts"
        / "Comp"
        / "Eternal2x"
    )


def _find_repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _read_version(repo_root: Path) -> str:
    vf = repo_root / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return "?"


def _check_python(path: str) -> bool:
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _find_system_python() -> str | None:
    candidates = (
        ["python", "python3"] if sys.platform.startswith("win")
        else ["python3", "python"]
    )
    for name in candidates:
        path = shutil.which(name)
        if path and _check_python(path):
            return path
    return None


def _embedded_python_exe(repo_root: Path) -> Path:
    if sys.platform.startswith("win"):
        return repo_root / "python" / "python.exe"
    return repo_root / "python" / "bin" / "python3"


def _find_embedded_python(repo_root: Path) -> str | None:
    exe = _embedded_python_exe(repo_root)
    if exe.exists() and _check_python(str(exe)):
        return str(exe)
    return None


def _python_version_str(python_path: str) -> str:
    try:
        r = subprocess.run(
            [python_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return (r.stdout + r.stderr).strip()
    except Exception:
        return python_path


def _get_python_download_url() -> str | None:
    if sys.platform.startswith("win"):
        return PYTHON_WIN_EMBED_URL
    if sys.platform == "darwin":
        arch = platform.machine().lower()
        if arch in ("arm64", "aarch64"):
            return PYTHON_MAC_ARM64_URL
        return PYTHON_MAC_X86_URL
    if sys.platform.startswith("linux"):
        return PYTHON_LINUX_URL
    return None


def _download_with_progress(url: str, callback=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Eternal2x-Installer/1.0"})
    resp = urllib.request.urlopen(req, timeout=120)
    total = int(resp.headers.get("Content-Length", 0))
    data = io.BytesIO()
    downloaded = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        data.write(chunk)
        downloaded += len(chunk)
        if callback and total > 0:
            callback(downloaded, total)
    return data.getvalue()


# ---------------------------------------------------------------------------
# Installer app
# ---------------------------------------------------------------------------

class InstallerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Install Eternal2x")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        w, h = 520, 580
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        self.repo_root = _find_repo_root()
        self.version = _read_version(self.repo_root)
        self.installing = False
        self.install_done = False
        self.python_path: str | None = None

        self._build_ui()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self):
        font_family = "Segoe UI" if sys.platform.startswith("win") else "Helvetica Neue"

        tk.Label(
            self.root, text="Eternal2x", font=(font_family, 26, "bold"),
            bg=BG, fg=FG,
        ).pack(pady=(24, 0))

        tk.Label(
            self.root,
            text=f"Smart Upscale for DaVinci Resolve  \u00b7  v{self.version}",
            font=(font_family, 10), bg=BG, fg=FG_DIM,
        ).pack(pady=(2, 18))

        log_outer = tk.Frame(self.root, bg=BORDER, padx=1, pady=1)
        log_outer.pack(padx=28, fill="x")
        log_inner = tk.Frame(log_outer, bg=BG_CARD, padx=14, pady=10)
        log_inner.pack(fill="both", expand=True)

        mono = "Consolas" if sys.platform.startswith("win") else "Menlo"
        self.log_text = tk.Text(
            log_inner, height=13, bg=BG_CARD, fg=FG_DIM,
            font=(mono, 10), wrap="word",
            borderwidth=0, highlightthickness=0,
            insertbackground=BG_CARD, cursor="arrow",
            state="disabled", selectbackground="#2b4a73",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("ok", foreground=SUCCESS)
        self.log_text.tag_configure("err", foreground=ERROR)
        self.log_text.tag_configure("accent", foreground=ACCENT)
        self.log_text.tag_configure("dim", foreground=FG_DIM)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=ACCENT, troughcolor=BG_CARD,
            bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT,
        )

        self.progress = ttk.Progressbar(
            self.root, length=464, mode="determinate",
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.pack(pady=(14, 0), padx=28)

        self.progress_label = tk.Label(
            self.root, text="", font=(font_family, 8), bg=BG, fg=FG_DIM,
        )
        self.progress_label.pack(pady=(4, 0))

        info_frame = tk.Frame(self.root, bg=BG)
        info_frame.pack(fill="x", padx=32, pady=(8, 0))

        tk.Label(
            info_frame,
            text=f"Plugin folder:  {self._short_path(self.repo_root, 58)}",
            font=(font_family, 8), bg=BG, fg=FG_DIM, anchor="w",
        ).pack(fill="x")

        try:
            dest = str(_resolve_comp_dir())
        except Exception:
            dest = "(could not detect)"
        tk.Label(
            info_frame,
            text=f"Install to:  {self._short_path(Path(dest), 58)}",
            font=(font_family, 8), bg=BG, fg=FG_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(pady=(18, 16))

        self.install_btn = tk.Button(
            btn_frame, text="Install", font=(font_family, 13, "bold"),
            bg=ACCENT, fg="#0b0b0f", activebackground="#9b90f5",
            activeforeground="#0b0b0f", borderwidth=0, relief="flat",
            padx=44, pady=10, cursor="hand2",
            command=self._start_install,
        )
        self.install_btn.pack(side="left", padx=8)

        self.close_btn = tk.Button(
            btn_frame, text="Close", font=(font_family, 11),
            bg=BTN_BG, fg=FG, activebackground="#1e1e28",
            activeforeground=FG, borderwidth=0, relief="flat",
            padx=26, pady=10, cursor="hand2",
            command=self.root.destroy,
        )
        self.close_btn.pack(side="left", padx=8)

        self._log("Ready to install Eternal2x.", "accent")
        self._log("Click Install to begin.\n", "dim")

    # ---- Logging / progress -----------------------------------------------

    @staticmethod
    def _short_path(path: Path, limit: int = 56) -> str:
        s = str(path)
        if len(s) <= limit:
            return s
        return "..." + s[-(limit - 3):]

    def _log(self, msg: str, tag: str | None = None):
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert("end", msg + "\n", tag)
        else:
            self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_progress(self, value: float, label: str = ""):
        self.progress["value"] = value
        self.progress_label.configure(text=label)
        self.root.update_idletasks()

    # ---- Install thread management ----------------------------------------

    def _start_install(self):
        if self.installing or self.install_done:
            return
        self.installing = True
        self.install_btn.configure(state="disabled", bg="#555555")
        threading.Thread(target=self._run_install, daemon=True).start()

    def _run_install(self):
        try:
            self._do_install()
        except Exception as exc:
            self.root.after(0, self._log, f"\nFatal error: {exc}", "err")
        finally:
            self.root.after(0, self._on_finish)

    def _on_finish(self):
        self.installing = False
        if not self.install_done:
            self.install_btn.configure(state="normal", bg=ACCENT)

    # ---- Embedded Python: Windows -----------------------------------------

    def _setup_embedded_python_windows(self) -> str | None:
        python_dir = self.repo_root / "python"

        self.root.after(0, self._log, "  Downloading Python for Windows...", "accent")

        def on_progress(downloaded, total):
            pct = downloaded / total * 100
            mb = downloaded / (1024 * 1024)
            mb_t = total / (1024 * 1024)
            self.root.after(0, self._set_progress, 15 + pct * 0.10,
                            f"Downloading Python... {mb:.1f} / {mb_t:.1f} MB")

        try:
            zip_data = _download_with_progress(PYTHON_WIN_EMBED_URL, on_progress)
        except Exception as exc:
            self.root.after(0, self._log, f"  Download failed: {exc}", "err")
            return None

        self.root.after(0, self._log, "  Extracting...")

        if python_dir.exists():
            shutil.rmtree(python_dir)
        python_dir.mkdir(parents=True)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(python_dir)

        for pth in python_dir.glob("python*._pth"):
            text = pth.read_text(encoding="utf-8")
            text = text.replace("#import site", "import site")
            if "import site" not in text:
                text += "\nimport site\n"
            pth.write_text(text, encoding="utf-8")

        python_exe = str(python_dir / "python.exe")
        if not Path(python_exe).exists():
            self.root.after(0, self._log, "  python.exe not found after extraction.", "err")
            return None

        self.root.after(0, self._log, "  Installing pip...", "dim")
        self.root.after(0, self._set_progress, 28, "Installing pip...")

        try:
            pip_data = _download_with_progress(GET_PIP_URL)
        except Exception as exc:
            self.root.after(0, self._log, f"  Failed to download pip: {exc}", "err")
            return None

        get_pip_path = python_dir / "get-pip.py"
        get_pip_path.write_bytes(pip_data)

        try:
            result = subprocess.run(
                [python_exe, str(get_pip_path), "--no-warn-script-location"],
                capture_output=True, text=True, timeout=120,
                cwd=str(python_dir),
            )
            if result.returncode != 0:
                self.root.after(0, self._log,
                                f"  pip setup failed: {result.stderr.strip()[:200]}", "err")
                return None
        except Exception as exc:
            self.root.after(0, self._log, f"  pip setup error: {exc}", "err")
            return None

        get_pip_path.unlink(missing_ok=True)
        self.root.after(0, self._log, "  Python ready.", "ok")
        return python_exe

    # ---- Embedded Python: macOS / Linux -----------------------------------

    def _setup_standalone_python(self) -> str | None:
        python_dir = self.repo_root / "python"
        url = _get_python_download_url()

        if not url:
            self.root.after(0, self._log,
                            "  No portable Python available for this platform.", "err")
            return None

        os_label = "macOS" if sys.platform == "darwin" else "Linux"
        self.root.after(0, self._log, f"  Downloading Python for {os_label}...", "accent")

        def on_progress(downloaded, total):
            pct = downloaded / total * 100
            mb = downloaded / (1024 * 1024)
            mb_t = total / (1024 * 1024)
            self.root.after(0, self._set_progress, 15 + pct * 0.10,
                            f"Downloading Python... {mb:.1f} / {mb_t:.1f} MB")

        try:
            tar_data = _download_with_progress(url, on_progress)
        except Exception as exc:
            self.root.after(0, self._log, f"  Download failed: {exc}", "err")
            return None

        self.root.after(0, self._log, "  Extracting (this may take a moment)...")
        self.root.after(0, self._set_progress, 26, "Extracting Python...")

        if python_dir.exists():
            shutil.rmtree(python_dir)

        temp_extract = self.repo_root / "_python_extract_tmp"
        if temp_extract.exists():
            shutil.rmtree(temp_extract)
        temp_extract.mkdir(parents=True)

        try:
            with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:gz") as tf:
                tf.extractall(temp_extract)
        except Exception as exc:
            self.root.after(0, self._log, f"  Extraction failed: {exc}", "err")
            shutil.rmtree(temp_extract, ignore_errors=True)
            return None

        extracted_python = temp_extract / "python"
        if not extracted_python.exists():
            children = [c for c in temp_extract.iterdir() if c.is_dir()]
            if len(children) == 1:
                extracted_python = children[0]

        shutil.move(str(extracted_python), str(python_dir))
        shutil.rmtree(temp_extract, ignore_errors=True)

        python_exe = str(python_dir / "bin" / "python3")
        if not Path(python_exe).exists():
            alt = python_dir / "bin" / "python"
            if alt.exists():
                python_exe = str(alt)
            else:
                self.root.after(0, self._log, "  python3 not found after extraction.", "err")
                return None

        os.chmod(python_exe, 0o755)

        self.root.after(0, self._set_progress, 30, "Verifying Python...")

        if not _check_python(python_exe):
            self.root.after(0, self._log, "  Extracted Python is not working.", "err")
            return None

        self.root.after(0, self._log, "  Python ready.", "ok")
        return python_exe

    # ---- Unified embedded setup -------------------------------------------

    def _setup_embedded_python(self) -> str | None:
        if sys.platform.startswith("win"):
            return self._setup_embedded_python_windows()
        return self._setup_standalone_python()

    # ---- Package installation ---------------------------------------------

    def _install_packages(self, python_path: str) -> bool:
        req_file = self.repo_root / "requirements.txt"
        if req_file.exists():
            pip_cmd = [python_path, "-m", "pip", "install",
                       "-r", str(req_file), "--no-warn-script-location", "--quiet"]
        else:
            pip_cmd = [python_path, "-m", "pip", "install",
                       "numpy", "opencv-python", "--no-warn-script-location", "--quiet"]

        try:
            result = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                self.root.after(0, self._log, f"  Failed: {stderr[:200]}", "err")
                return False
        except subprocess.TimeoutExpired:
            self.root.after(0, self._log, "  Timed out after 10 minutes.", "err")
            return False
        except FileNotFoundError:
            self.root.after(0, self._log, "  Could not run pip.", "err")
            return False
        return True

    # ---- Main install flow ------------------------------------------------

    def _do_install(self):
        # --- Step 1: Verify plugin files ---
        self.root.after(0, self._log, "Checking plugin files...")
        self.root.after(0, self._set_progress, 5, "Checking files...")

        required = [
            self.repo_root / "Installer" / "Eternal2xLauncher.lua",
            self.repo_root / "Installer" / "Eternal2x.lua",
            self.repo_root / "Stages" / "resolve_detect_markers.py",
            self.repo_root / "Pipeline" / "config.py",
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            for m in missing:
                self.root.after(0, self._log, f"  Missing: {m}", "err")
            self.root.after(0, self._log,
                            "\nPlugin files are incomplete. Re-download the plugin.", "err")
            return

        self.root.after(0, self._set_progress, 10, "")
        self.root.after(0, self._log, "  All files present.", "ok")

        # --- Step 2: Find or install Python ---
        self.root.after(0, self._log, "\nLooking for Python...")
        self.root.after(0, self._set_progress, 12, "Looking for Python...")

        python_path = _find_embedded_python(self.repo_root)
        python_source = "embedded"

        if python_path:
            self.root.after(0, self._log,
                            f"  Using bundled Python: {_python_version_str(python_path)}", "ok")
        else:
            python_path = _find_system_python()
            python_source = "system"

        if python_path and python_source == "system":
            self.root.after(0, self._log,
                            f"  Found: {_python_version_str(python_path)}", "ok")

        if not python_path:
            self.root.after(0, self._log, "  Python not found on this system.", "dim")
            self.root.after(0, self._log,
                            "  Setting up a private Python for the plugin...", "accent")
            python_path = self._setup_embedded_python()
            python_source = "embedded"
            if not python_path:
                return

        self.root.after(0, self._set_progress, 35, "")

        # --- Step 3: Install packages ---
        self.root.after(0, self._log, "\nInstalling Python packages...")
        self.root.after(0, self._log,
                        "  (numpy, opencv-python \u2014 may take 1-2 minutes)", "dim")
        self.root.after(0, self._set_progress, 40,
                        "Installing packages (this takes a bit)...")

        if not self._install_packages(python_path):
            return

        self.root.after(0, self._set_progress, 75, "")
        self.root.after(0, self._log, "  Packages installed.", "ok")

        # --- Step 4: Copy launcher into Resolve ---
        self.root.after(0, self._log, "\nInstalling plugin into DaVinci Resolve...")
        self.root.after(0, self._set_progress, 80, "Copying plugin files...")

        try:
            dest_dir = _resolve_comp_dir()
        except Exception as exc:
            self.root.after(0, self._log,
                            f"  Could not find Resolve scripts folder: {exc}", "err")
            return

        dest_dir.mkdir(parents=True, exist_ok=True)

        src_launcher = self.repo_root / "Installer" / "Eternal2xLauncher.lua"
        dest_lua = dest_dir / "Eternal2x.lua"
        shutil.copy2(src_launcher, dest_lua)

        self.root.after(0, self._set_progress, 90, "Writing config...")
        self.root.after(0, self._log, f"  Launcher: {dest_lua}", "ok")

        # --- Step 5: Write config ---
        conf_path = dest_dir / "Eternal2x.conf"
        conf_path.write_text(
            f"repo_root={self.repo_root}\n"
            f"python={python_path}\n"
            f"update_url={DEFAULT_UPDATE_URL}\n"
            "auto_update=true\n",
            encoding="utf-8",
        )
        self.root.after(0, self._log, f"  Config:   {conf_path}", "ok")

        # --- Done ---
        self.root.after(0, self._set_progress, 100, "Done!")
        self.root.after(0, self._log, "")
        self.root.after(0, self._log, "\u2714  Installation complete!", "accent")
        self.root.after(0, self._log, "")
        self.root.after(0, self._log, "Restart DaVinci Resolve, then open:", "ok")
        self.root.after(0, self._log, "  Workspace \u2192 Scripts \u2192 Eternal2x", "ok")

        if python_source == "embedded":
            self.root.after(0, self._log, "")
            self.root.after(0, self._log,
                            "Note: A private Python was installed in the plugin", "dim")
            self.root.after(0, self._log,
                            "folder. Do not delete the 'python' subfolder.", "dim")

        self.install_done = True
        self.root.after(0, lambda: self.install_btn.configure(
            text="\u2713  Installed", state="disabled", bg=SUCCESS, fg="#fff",
        ))

    def run(self):
        self.root.mainloop()


def main():
    app = InstallerApp()
    app.run()


if __name__ == "__main__":
    main()
