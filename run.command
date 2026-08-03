#!/bin/bash
# macOS / Linux 用のランチャ。Finder からダブルクリックしても起動する。
# 初回だけ実行権限が要る:  chmod +x run.command
cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python3" ]; then
    PY=".venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "python3 が見つかりません。README の「環境構築」を実行してください。"
    read -r -p "Enterで閉じます..."
    exit 1
fi

if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo "tkinter が使えません。"
    echo "Homebrew の python を使っている場合は  brew install python-tk  が必要です。"
    read -r -p "Enterで閉じます..."
    exit 1
fi

exec "$PY" main.py
