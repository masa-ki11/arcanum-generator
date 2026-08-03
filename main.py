"""アプリの起動口. `python main.py` で画面が開く.

`--selftest [報告先ファイル]` を付けると、画面を組み立てて結果を返して終了する。
配布用にビルドした実行ファイルが本当に起動するかを確かめるために使う。
"""

import sys

from arcanum_generator.gui import main, selftest

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        position = sys.argv.index("--selftest")
        report = sys.argv[position + 1] if len(sys.argv) > position + 1 else None
        raise SystemExit(selftest(report))
    main()
