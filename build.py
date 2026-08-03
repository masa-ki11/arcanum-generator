"""配布用の実行ファイルを作る.

    python build.py            フォルダ形式(推奨)。起動が速い。zipも作る
    python build.py --onefile  単一ファイル形式。渡すのは楽だが起動が遅い

Windows なら .exe、macOS なら .app ができる。**動かすOSと同じOSでビルドすること**
(WindowsでMac用は作れないし、その逆もできない)。

PyInstaller は必要なときに入れる。プロジェクトの .venv を汚したくないので、
build/pyinstaller-env に専用の環境を作ってそこへ入れる。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import venv
import zipfile
from pathlib import Path

APP_NAME = "arcanum-generator"
ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
WORK = ROOT / "build"
BUILD_ENV = WORK / "pyinstaller-env"


def build_env_python() -> Path:
    """PyInstaller 専用の仮想環境を用意して、その python を返す."""
    exe = (
        BUILD_ENV / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else BUILD_ENV / "bin" / "python3"
    )
    if not exe.exists():
        print(f"ビルド用の環境を作ります: {BUILD_ENV}")
        venv.EnvBuilder(with_pip=True).create(BUILD_ENV)
        _wait_until_runnable(exe)
    if not _has_pyinstaller(exe):
        print("PyInstaller を入れます...")
        subprocess.run(
            [str(exe), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            check=True,
        )
        subprocess.run(
            [str(exe), "-m", "pip", "install", "--quiet", "pyinstaller"], check=True
        )
    return exe


def _wait_until_runnable(python: Path, attempts: int = 20) -> None:
    """作ったばかりの python が起動できるようになるまで待つ.

    Windows ではウイルス対策ソフトが直後の実行ファイルを掴んでいて、
    起動しようとすると PermissionError になることがある。
    """
    for attempt in range(attempts):
        try:
            subprocess.run(
                [str(python), "--version"], capture_output=True, check=True
            )
            return
        except (PermissionError, OSError, subprocess.CalledProcessError):
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


def _has_pyinstaller(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-m", "PyInstaller", "--version"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def run_pyinstaller(python: Path, onefile: bool) -> None:
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--onefile" if onefile else "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK / "work"),
        "--specpath",
        str(WORK),
        str(ROOT / "main.py"),
    ]
    print("$ " + " ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)


def built_path(onefile: bool) -> Path:
    """ビルド結果(単一ファイル / フォルダ / .app)の場所."""
    if sys.platform == "darwin" and not onefile:
        return DIST / f"{APP_NAME}.app"
    if onefile:
        return DIST / (f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME)
    return DIST / APP_NAME


def verify(target: Path) -> bool:
    """ビルドしたものが本当に起動するか、自己診断を走らせて確かめる.

    --windowed だと起動時のエラーが画面にも標準出力にも出ないので、
    終了コードと報告ファイルで判定する。
    """
    if target.is_dir() and target.suffix == ".app":
        exe = target / "Contents" / "MacOS" / APP_NAME
    elif target.is_dir():
        exe = target / (f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME)
    else:
        exe = target

    report = WORK / "selftest.txt"
    report.unlink(missing_ok=True)
    print(f"起動を確認します: {exe}")
    result = subprocess.run([str(exe), "--selftest", str(report)])
    text = report.read_text(encoding="utf-8") if report.exists() else "(報告なし)"
    print(text)
    return result.returncode == 0


def make_zip(target: Path) -> Path:
    """フォルダ形式をzipにまとめる. 相手はこれを解凍して中のexeを実行する."""
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    archive = DIST / f"{APP_NAME}-{system}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zf.write(path, target.name + "/" + str(path.relative_to(target)))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="単一ファイルにまとめる(起動が遅くなる)",
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="起動確認を省略する"
    )
    args = parser.parse_args()

    python = build_env_python()
    run_pyinstaller(python, args.onefile)

    target = built_path(args.onefile)
    if not target.exists():
        print(f"ビルド結果が見つかりません: {target}")
        return 1

    if not args.skip_verify and not verify(target):
        print("起動確認に失敗しました。配布しないでください。")
        return 1

    print(f"\nできました: {target}")
    if target.is_dir():
        archive = make_zip(target)
        size = archive.stat().st_size / 1024 / 1024
        print(f"配布用: {archive}  ({size:.1f} MB)")
        print("相手はこれを解凍して、中の実行ファイルを起動します。")
    else:
        size = target.stat().st_size / 1024 / 1024
        print(f"配布用: {target}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
