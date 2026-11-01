"""构建桌面版 IM Bridge 控制台。

**为什么用 --onedir 而不是 --onefile**
onefile 每次启动都要把 ~40MB 解压到 %TEMP%，冷启动 2–3 秒且期间没有任何反馈，
用户会以为没点开而重复点击图标，于是弹出多个窗口——这正是「先闪一堆小窗口」的成因之一。
onedir 只有一个进程、秒开，代价是产物从一个 exe 变成一个文件夹。

用法（项目根目录）：
    python launcher/build_desktop.py

产物：
    app/IMBridgeConsole/IMBridgeConsole.exe   ← 快捷方式指向这里
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import importlib.util
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "launcher"
BUILD = ROOT / ".build"
# 每次构建用独立目录：PyInstaller 在 onedir 模式下会先删掉同名产物目录，
# 而那会触发批量删除保护。换新目录就不用删任何东西了。
DIST = BUILD / "out" / time.strftime("%Y%m%d-%H%M%S")
APP_DIR = ROOT / "app"
APP_NAME = "IMBridgeConsole"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=("public", "personal"), default="public")
    args = parser.parse_args()
    from build_assets import prepare_assets
    assets = prepare_assets(LAUNCHER / "assets", DIST / "profile-assets", args.edition)
    DIST.mkdir(parents=True, exist_ok=True)
    icon = assets / "app.ico"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onedir", "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work-desktop"),
        "--specpath", str(BUILD / "spec"),
        "--paths", str(LAUNCHER),
        "--paths", str(ROOT / "src"),
        "--hidden-import", "PySide6.QtMultimedia",
        "--hidden-import", "config_store",
        "--hidden-import", "remote",
        "--hidden-import", "instance_config",
        "--hidden-import", "qt_theme",
        "--hidden-import", "widgets",
        "--add-data", f"{assets};assets",
        "--exclude-module", "tkinter",
        "--exclude-module", "PyInstaller",
    ]
    if icon.is_file():
        cmd += ["--icon", str(icon)]
    # Portable Python can hide Qt DLLs from PyInstaller's dependency search.
    # Bundle the runtime DLLs explicitly, including Shiboken's support library.
    for package in ("PySide6", "shiboken6"):
        spec = importlib.util.find_spec(package)
        if spec and spec.origin:
            for dll in Path(spec.origin).parent.glob("*.dll"):
                cmd += ["--add-binary", f"{dll};{package}"]
    for dll in Path(sys.executable).parent.glob("vcruntime*.dll"):
        cmd += ["--add-binary", f"{dll};."]
    cmd.append(str(LAUNCHER / "desktop_qt.py"))

    print("$ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    built = DIST / APP_NAME
    if not built.is_dir():
        print(f"[!] 未找到构建目录：{built}")
        return 2

    # Qt 6.12 uses Windows' unversioned ICU shim. Dependency discovery may pick
    # up Git's ICU (version-suffixed exports) from PATH, which makes QtCore fail.
    wrong_icu = built / "_internal" / "icuuc.dll"
    if wrong_icu.is_file():
        wrong_icu.unlink()

    target = APP_DIR / (APP_NAME if args.edition == "personal" else APP_NAME + "-Public")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    stale_icu = target / "_internal" / "icuuc.dll"
    if stale_icu.is_file():
        stale_icu.unlink()
    try:
        # 直接覆盖写入，不先删目录：删目录会触发批量删除保护，
        # 而且旧版窗口开着时删不掉。多余的历史文件留着无妨。
        shutil.copytree(built, target, dirs_exist_ok=True)
    except PermissionError:
        print(f"[!] 目标目录被占用（旧版控制台可能还开着）：{target}")
        print(f"    新版本已生成：{built}")
        print("    关掉正在运行的旧版控制台后重新运行本脚本即可。")
        return 3

    exe = target / f"{APP_NAME}.exe"
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"已生成：{exe}（目录合计 {size:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
