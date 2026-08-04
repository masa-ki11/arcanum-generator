"""入力データの保存・読み込みと、割り振り結果の書き出し."""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

from .allocator import AllocationResult
from .models import (
    BATTLE_COUNT,
    BATTLE_LABELS,
    FILE_VERSION,
    Roster,
    category_order,
)

# 古い形式はすべて既定値で読めるようにしてあるので、1〜現行版を受け付ける。
# 個別に列挙すると項目を増やしたときに更新を忘れ、保存直後のファイルが
# 開けなくなる(実際に2回やらかした)。
SUPPORTED_VERSIONS = frozenset(range(1, FILE_VERSION + 1))

FIRST_HALF_MARK = "★"
VANGUARD_MARK = "前"
NEW_MARK = "＊"
"""引き継ぎ割り振りで、その奥義に今回から入った人に付ける印."""
CHANGE_MARK = "←変更"
"""引き継ぎ割り振りで、担当が増減した人の行に付ける印."""

AUTOSAVE_NAME = "kassen.json"
"""プロジェクトフォルダに置く自動保存ファイルの名前."""


def project_dir() -> Path:
    """プロジェクトフォルダ(main.py のある場所)を返す.

    カレントディレクトリは当てにしない。macOS で .command を Finder から
    ダブルクリックするとカレントがホームになり、保存先が散らばるため。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller などで固めた場合は実行ファイルの場所。
        exe_dir = Path(sys.executable).resolve().parent
        # macOS の .app は実体が Contents/MacOS/ の中にある。そこへ保存すると
        # アプリの内部に隠れてしまうので、.app と同じ階層に置く。
        for parent in exe_dir.parents:
            if parent.suffix == ".app":
                return parent.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent


def autosave_path() -> Path:
    """自動保存先のフルパス."""
    return project_dir() / AUTOSAVE_NAME


REPLACE_ATTEMPTS = 5
REPLACE_WAIT_SECONDS = 0.05


def save_roster(roster: Roster, path: str | Path) -> None:
    """入力一式をJSONで保存する.

    自動保存で頻繁に上書きするので、一時ファイルに書いてから置き換える。
    書いている途中で落ちても、前回の内容が壊れずに残る。

    Windows では、ウイルス対策ソフトなどが対象ファイルを開いている一瞬に
    置き換えが PermissionError になることがある。自動保存は操作のたびに
    走るので、その一瞬で失敗扱いにせず少し待って retry する。
    """
    path = Path(path)
    body = json.dumps(roster.to_dict(), ensure_ascii=False, indent=2)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(body, encoding="utf-8")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(REPLACE_WAIT_SECONDS)


def load_roster(path: str | Path) -> Roster:
    """JSONから入力一式を読み込む."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ファイルの形式が違います(JSONオブジェクトではありません)。")
    version = data.get("version", 1)
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"未対応のファイル形式です(version={version})。")
    return Roster.from_dict(data)


def export_csv(result: AllocationResult, path: str | Path) -> None:
    """割り振り結果をCSVで書き出す(Excelで開ける utf-8-sig).

    引き継ぎで割り振ったときは、メンバー別に「前回から」の増減列を足す。
    Excelで絞り込めば、担当が変わった人だけ抜き出せる。
    """
    carry = result.carryover
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["種類", "前半必須", "前衛向け", "奥義", "必要人数", "担当人数"]
            + [f"{label}に確実に出せる人数" for label in BATTLE_LABELS]
            + [f"{label}に出られる人数(△含む)" for label in BATTLE_LABELS]
            + ["担当者"]
        )
        for assignment in _by_category(result):
            writer.writerow(
                [
                    assignment.category,
                    FIRST_HALF_MARK if assignment.first_half else "",
                    VANGUARD_MARK if assignment.for_vanguard else "",
                    assignment.arcanum,
                    assignment.required,
                    len(assignment.members),
                    *assignment.sure_per_battle,
                    *assignment.per_battle,
                    _members_text(result, assignment),
                ]
            )
        writer.writerow([])
        # 前衛は出力に含めない(割り振りの入力としてだけ使う)。
        header = ["メンバー"] + list(BATTLE_LABELS) + ["担当数", "担当奥義"]
        if carry:
            header.append("前回から")
        writer.writerow(header)
        # load は連合員の並び順で作ってあるので、そのまま出す。
        for name, arcana in result.load.items():
            levels = result.attendance.get(name, [""] * BATTLE_COUNT)
            row = [name, *levels, len(arcana), "、".join(arcana)]
            if carry:
                row.append(carry.change_note(name))
            writer.writerow(row)


def _members_text(result: AllocationResult, assignment) -> str:
    """担当者名を並べる. 引き継ぎで今回その奥義に入った人には印を付ける."""
    carry = result.carryover
    return "、".join(
        name + NEW_MARK if carry and carry.is_new(assignment.arcanum, name) else name
        for name in assignment.members
    )


def _by_category(result: AllocationResult) -> list:
    """種類ごとにまとめた順で割り当てを返す(種類内は入力順のまま)."""
    # 同じ内容の Assignment があっても崩れないよう、元の位置を添えて並べ替える。
    indexed = sorted(
        enumerate(result.assignments),
        key=lambda pair: (category_order(pair[1].category), pair[0]),
    )
    return [assignment for _, assignment in indexed]


def format_result_text(result: AllocationResult) -> str:
    """連合チャットにそのまま貼れる形に整形する.

    引き継ぎで割り振ったときは、前回から変わったところに印を付ける。
    2日目以降は「自分の担当が変わったかどうか」だけ見れば済むようにするため。
    ゼロから割り振ったときは全員が変更扱いになって意味がないので付けない。
    """
    carry = result.carryover
    lines: list[str] = []
    current_category = None
    for assignment in _by_category(result):
        if assignment.category != current_category:
            current_category = assignment.category
            if lines:
                lines.append("")
            lines.append(f"【{current_category}】")
        members = _members_text(result, assignment) or "(未割当)"
        mark = (FIRST_HALF_MARK if assignment.first_half else "") + (
            VANGUARD_MARK if assignment.for_vanguard else ""
        )
        marks = "/".join(assignment.battle_marks())
        lines.append(
            f"{mark}{assignment.arcanum}({len(assignment.members)}人 "
            f"各戦{marks}): {members}"
        )

    lines.append("")
    lines.append(
        f"※各戦の数字=確実に出せる人数 / △=△頼み / ×=誰も出せない"
    )
    # 印が1つも付いていないのに凡例だけ出ると、探させてしまうので出さない。
    if carry and any(carry.member_added.values()):
        lines.append(f"※{NEW_MARK}=前回から新しくその奥義の担当になった人")
    lines.append("")
    lines.append(
        f"【メンバー別】 {FIRST_HALF_MARK}=前半必須 {VANGUARD_MARK}=前衛向け / "
        "参戦は1戦目→3戦目の順"
    )
    if carry:
        changed = len(carry.changed_members())
        lines.append(
            f"※前回から担当が変わったのは{changed}人。"
            f"その行の末尾に {CHANGE_MARK} を付けています"
            if changed
            else "※前回から担当が変わった人はいません"
        )
    # 前衛は出力に含めない(割り振りの入力としてだけ使う)。
    # load は連合員の並び順で作ってあるので、そのまま出す。
    for name, arcana in result.load.items():
        levels = "".join(result.attendance.get(name, []))
        line = f"{levels} {name}: {'、'.join(arcana) if arcana else '-'}"
        note = carry.change_note(name) if carry else ""
        if note:
            line += f" {CHANGE_MARK}({note})"
        lines.append(line)

    return "\n".join(lines)
