"""Create a Desktop shortcut that launches quota-hub."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "static" / "quota-hub.ico"
PYTHON = ROOT / ".venv" / "Scripts" / "pythonw.exe"
LAUNCH = ROOT / "launch.py"


def desktop_dir() -> Path:
    import ctypes
    from ctypes import wintypes

    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
    path = Path(buf.value)
    if path.exists():
        return path
    home = Path.home()
    for candidate in (home / "Desktop", home / "OneDrive" / "Desktop"):
        if candidate.exists():
            return candidate
    return path


def ps_single(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    if not PYTHON.exists():
        raise SystemExit(f"windowless Python not found: {PYTHON}")
    desktop = desktop_dir()
    if not desktop.exists():
        raise SystemExit(f"desktop not found: {desktop}")
    lnk = desktop / "额度看板.lnk"
    ps1 = ROOT / "scripts" / "_make_lnk.ps1"
    body = "\n".join(
        [
            "$s = New-Object -ComObject WScript.Shell",
            f"$l = $s.CreateShortcut({ps_single(str(lnk))})",
            f"$l.TargetPath = {ps_single(str(PYTHON))}",
            f"$l.Arguments = {ps_single(str(LAUNCH))}",
            f"$l.WorkingDirectory = {ps_single(str(ROOT))}",
            f"$l.IconLocation = {ps_single(str(ICO) + ',0')}",
            "$l.WindowStyle = 1",
            f"$l.Description = {ps_single('quota-hub')}",
            "$l.Save()",
            f"Write-Output {ps_single(str(lnk))}",
            "",
        ]
    )
    ps1.write_text(body, encoding="utf-8-sig")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
        ],
        check=True,
    )
    ps1.unlink(missing_ok=True)
    if not lnk.exists():
        raise SystemExit("shortcut was not created")
    print(lnk)


if __name__ == "__main__":
    main()
