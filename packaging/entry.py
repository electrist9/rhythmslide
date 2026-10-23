"""PyInstaller 用のトップレベル起動スクリプト。

spec の Analysis に渡すエントリポイントは、パッケージ内の `__main__.py`
（相対 import を持つ）を直接指定してはいけない。PyInstaller はこれを
パッケージ外のトップレベルスクリプトとして扱うため、`from .app import main`
が "attempted relative import with no known parent package" で失敗する。
そこで正規パッケージ `rhythmslide` を絶対 import する薄い起動子を用意する。
"""
import sys

from rhythmslide.app import main

if __name__ == "__main__":
    raise SystemExit(main())
