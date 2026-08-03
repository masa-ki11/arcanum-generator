"""奥義割り振りツールのデスクトップGUI (Tkinter)."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .allocator import AllocationError, AllocationResult, allocate, validate
from .models import (
    ATTEND_BEST,
    ATTEND_NO,
    ATTENDANCE_LEVELS,
    BATTLE_COUNT,
    BATTLE_LABELS,
    CATEGORY_NAMES,
    DEFAULT_ATTENDANCE,
    DEFAULT_CATEGORY,
    DEFAULT_REQUIRED,
    Arcanum,
    Member,
    Roster,
    category_order,
    get_category,
)
from .storage import (
    autosave_path,
    export_csv,
    format_result_text,
    load_roster,
    project_dir,
    save_roster,
)

APP_TITLE = "戦国炎舞 奥義割り振り"
STRATEGIST_MARK = "軍"
FIRST_HALF_MARK = "★"
JSON_FILETYPES = [("割り振り設定", "*.json"), ("すべてのファイル", "*.*")]
CSV_FILETYPES = [("CSVファイル", "*.csv"), ("すべてのファイル", "*.*")]

# 日本語が出る前提で、環境にある中から先に見つかったものを使う。
FONT_CANDIDATES = {
    "darwin": ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "YuGothic", "Osaka"],
    "win32": ["Meiryo UI", "Yu Gothic UI", "MS UI Gothic"],
}
FONT_SIZES = {"darwin": 13, "win32": 10}
ROW_HEIGHTS = {"darwin": 26, "win32": 24}


class BulkAddDialog(tk.Toplevel):
    """改行区切りでメンバーをまとめて追加するダイアログ."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("メンバーをまとめて追加")
        self.transient(parent)
        self.resizable(True, True)
        self.names: list[str] | None = None

        ttk.Label(
            self, text="1行に1人ずつ名前を貼り付けてください。"
        ).pack(anchor="w", padx=12, pady=(12, 4))

        self._text = tk.Text(self, width=32, height=16)
        self._text.pack(fill="both", expand=True, padx=12)
        self._text.focus_set()

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=12)
        ttk.Button(buttons, text="追加", command=self._on_ok).pack(side="right")
        ttk.Button(buttons, text="キャンセル", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        self.grab_set()
        self.wait_window(self)

    def _on_ok(self) -> None:
        raw = self._text.get("1.0", "end").splitlines()
        self.names = [line.strip() for line in raw if line.strip()]
        self.destroy()


class ArcanumApp(tk.Tk):
    """メインウィンドウ."""

    def __init__(self, autosave_file: Path | None = None) -> None:
        super().__init__()
        self.roster = Roster()
        self.result: AllocationResult | None = None
        self.current_path: Path | None = None
        # テストから別の場所を指せるようにしておく(既定はプロジェクトフォルダ)。
        self.autosave_file = autosave_file or autosave_path()
        # 復元が終わるまでは書き戻さない(読み込み失敗時に空で上書きしないため)。
        self._autosave_enabled = False

        self.title(APP_TITLE)
        self.geometry("1280x760")
        self.minsize(1040, 640)
        self._setup_style()

        self.slots_var = tk.IntVar(value=self.roster.slots_per_member)
        self.status_var = tk.StringVar(value="奥義と連合員を入力してください。")

        # ウィンドウが縮んでもステータスバーと操作部が消えないよう、
        # 端に貼り付ける部品を先に pack してから中央のパネルを広げる。
        self._build_toolbar()
        self._build_statusbar()
        self._build_panels()

        self._restore_autosave()
        self._autosave_enabled = True
        self._refresh_all()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 自動保存
    # ------------------------------------------------------------------
    def _restore_autosave(self) -> None:
        """前回の内容をプロジェクトフォルダから読み戻す."""
        if not self.autosave_file.exists():
            return
        try:
            self.roster = load_roster(self.autosave_file)
        except (OSError, ValueError, KeyError) as exc:
            # 壊れたファイルを空の内容で上書きしないよう、退避してから空で始める。
            broken = self.autosave_file.with_name(self.autosave_file.name + ".broken")
            try:
                self.autosave_file.replace(broken)
            except OSError:
                broken = self.autosave_file
            messagebox.showerror(
                APP_TITLE,
                f"前回の内容を読み込めませんでした。\n{exc}\n\n"
                f"壊れたファイルは次の名前で残してあります:\n{broken}",
                parent=self,
            )
            self.roster = Roster()
            return
        self.slots_var.set(self.roster.slots_per_member)

    def _autosave(self) -> None:
        """プロジェクトフォルダへ書き戻す. 失敗しても操作は止めない."""
        if not self._autosave_enabled:
            return
        try:
            save_roster(self.roster, self.autosave_file)
        except OSError as exc:
            self._autosave_enabled = False  # 毎操作で警告を出し続けない。
            messagebox.showerror(
                APP_TITLE,
                f"自動保存に失敗しました。以降は自動保存を止めます。\n"
                f"{self.autosave_file}\n{exc}\n\n"
                "「別名で保存」で書ける場所に保存してください。",
                parent=self,
            )

    def _changed(self) -> None:
        """入力が変わったときの共通処理: 結果を捨てて自動保存する."""
        self._invalidate_result()
        self._autosave()

    def _on_close(self) -> None:
        self._on_slots_changed()
        self._autosave()
        self.destroy()

    # ------------------------------------------------------------------
    # 画面の組み立て
    # ------------------------------------------------------------------
    def _setup_style(self) -> None:
        family = self._pick_font_family()
        size = FONT_SIZES.get(sys.platform, 10)
        self.option_add("*Font", (family, size))
        style = ttk.Style(self)
        style.configure("Treeview", rowheight=ROW_HEIGHTS.get(sys.platform, 24))
        style.configure("Treeview.Heading", font=(family, size, "bold"))
        style.configure("Short.TLabel", foreground="#c0392b")

    def _pick_font_family(self) -> str:
        """この環境に実在する日本語フォントを選ぶ.

        候補が1つも無ければ Tk の既定フォントに任せる(名前を決め打ちすると
        macOS で Meiryo UI が無く、代替に化けるため)。
        """
        available = set(tkfont.families(self))
        for candidate in FONT_CANDIDATES.get(sys.platform, []):
            if candidate in available:
                return candidate
        return tkfont.nametofont("TkDefaultFont").cget("family")

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(fill="x")

        ttk.Button(bar, text="新規", command=self.new_roster).pack(side="left")
        ttk.Button(bar, text="読み込み", command=self.open_roster).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(bar, text="別名で保存", command=self.save_roster_as).pack(
            side="left", padx=(6, 0)
        )

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Label(bar, text="1人あたりの奥義枠:").pack(side="left")
        spin = ttk.Spinbox(
            bar,
            from_=1,
            to=20,
            width=4,
            textvariable=self.slots_var,
            command=self._on_slots_changed,
        )
        spin.pack(side="left", padx=(6, 0))
        spin.bind("<FocusOut>", lambda _e: self._on_slots_changed())
        spin.bind("<Return>", lambda _e: self._on_slots_changed())

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Button(bar, text="割り振る", command=self.run_allocation).pack(side="left")
        ttk.Button(bar, text="結果をコピー", command=self.copy_result).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(bar, text="CSV出力", command=self.export_result_csv).pack(
            side="left", padx=(6, 0)
        )

    def _build_panels(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        paned.add(self._build_arcana_panel(paned), weight=3)
        paned.add(self._build_member_panel(paned), weight=2)
        paned.add(self._build_result_panel(paned), weight=4)

    def _build_arcana_panel(self, parent: tk.Misc) -> ttk.Widget:
        frame = ttk.LabelFrame(parent, text="合戦で設定する奥義", padding=8)

        arcana_box = ttk.Frame(frame)
        self.arcana_tree = ttk.Treeview(
            arcana_box,
            columns=("first_half", "name", "category", "required"),
            show="headings",
            selectmode="browse",
        )
        self.arcana_tree.heading("first_half", text="前半")
        self.arcana_tree.heading("name", text="奥義名")
        self.arcana_tree.heading("category", text="種類")
        self.arcana_tree.heading("required", text="必要人数")
        # 幅を固定しておかないと、パネルが狭いとき「必要人数」列が隠れたまま
        # 横スクロールもできず辿り着けなくなる。
        self.arcana_tree.column("first_half", width=38, anchor="center", stretch=False)
        self.arcana_tree.column("name", width=110, anchor="w", stretch=False)
        self.arcana_tree.column("category", width=104, anchor="w", stretch=False)
        # 最後の列は伸ばして余白を埋めつつ、minwidth を下回ると横スクロールに回す。
        self.arcana_tree.column(
            "required", width=66, minwidth=66, anchor="center", stretch=True
        )
        self.arcana_tree.bind("<Double-1>", self._on_arcanum_double_click)
        self.arcana_tree.bind("<<TreeviewSelect>>", self._on_arcanum_selected)

        self.arcanum_name_var = tk.StringVar()
        self.arcanum_required_var = tk.IntVar(value=DEFAULT_REQUIRED)
        self.arcanum_category_var = tk.StringVar(value=DEFAULT_CATEGORY)
        self.arcanum_first_half_var = tk.BooleanVar(value=False)

        editor = ttk.Frame(frame)
        entry = ttk.Entry(editor, textvariable=self.arcanum_name_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.add_arcanum())
        self.required_spin = ttk.Spinbox(
            editor, from_=1, to=99, width=4, textvariable=self.arcanum_required_var
        )
        self.required_spin.pack(side="left", padx=(6, 0))
        ttk.Button(editor, text="追加", command=self.add_arcanum).pack(
            side="left", padx=(6, 0)
        )

        category_row = ttk.Frame(frame)
        ttk.Label(category_row, text="種類:").pack(side="left")
        ttk.Combobox(
            category_row,
            textvariable=self.arcanum_category_var,
            values=list(CATEGORY_NAMES),
            state="readonly",
            width=16,
        ).pack(side="left", padx=(6, 0))
        self.category_hint = ttk.Label(category_row, text="", foreground="#666666")
        self.category_hint.pack(side="left", padx=(8, 0))
        self.arcanum_category_var.trace_add("write", lambda *_: self._update_category_hint())

        first_half_row = ttk.Frame(frame)
        ttk.Checkbutton(
            first_half_row,
            text=f"{FIRST_HALF_MARK} 前半必須(◎の人を優先して充てる)",
            variable=self.arcanum_first_half_var,
        ).pack(side="left")

        buttons = ttk.Frame(frame)
        ttk.Button(buttons, text="選択中を更新", command=self.update_arcanum).pack(
            side="left"
        )
        ttk.Button(buttons, text="削除", command=self.delete_arcanum).pack(
            side="left", padx=(6, 0)
        )

        buttons2 = ttk.Frame(frame)
        ttk.Button(buttons2, text="↑", width=3, command=lambda: self.move_arcanum(-1)).pack(
            side="left"
        )
        ttk.Button(buttons2, text="↓", width=3, command=lambda: self.move_arcanum(1)).pack(
            side="left", padx=(2, 0)
        )
        ttk.Button(
            buttons2, text="種類ごとにまとめる", command=self.sort_arcana_by_category
        ).pack(side="left", padx=(6, 0))

        # 下端の操作部から先に場所を確保し、残りを一覧に使わせる。
        buttons2.pack(side="bottom", fill="x", pady=(4, 0))
        buttons.pack(side="bottom", fill="x", pady=(6, 0))
        first_half_row.pack(side="bottom", fill="x", pady=(4, 0))
        category_row.pack(side="bottom", fill="x", pady=(6, 0))
        editor.pack(side="bottom", fill="x", pady=(8, 0))
        self._add_scrollbars(arcana_box, self.arcana_tree, horizontal=True)
        arcana_box.pack(fill="both", expand=True)
        self._update_category_hint()
        return frame

    def _build_member_panel(self, parent: tk.Misc) -> ttk.Widget:
        frame = ttk.LabelFrame(parent, text="連合員 / 本日の参戦", padding=8)

        member_box = ttk.Frame(frame)
        battle_columns = tuple(f"b{i}" for i in range(BATTLE_COUNT))
        self.member_tree = ttk.Treeview(
            member_box,
            columns=battle_columns + ("strategist", "name"),
            show="headings",
            selectmode="extended",
        )
        for i, column in enumerate(battle_columns):
            self.member_tree.heading(column, text=f"{i + 1}戦")
            self.member_tree.column(column, width=38, anchor="center", stretch=False)
        self.member_tree.heading("strategist", text="軍師")
        self.member_tree.heading("name", text="名前")
        self.member_tree.column("strategist", width=42, anchor="center", stretch=False)
        self.member_tree.column("name", width=110, minwidth=110, anchor="w", stretch=True)
        self.member_tree.tag_configure("absent", foreground="#909090")
        self.member_tree.tag_configure("strategist", foreground="#8e44ad")
        self.member_tree.bind("<Double-1>", self._on_member_double_click)
        self.member_tree.bind("<space>", lambda _e: self.cycle_attendance())

        # wraplength を付けて、長いヒント文がパネル幅を押し広げないようにする。
        hint = ttk.Label(
            frame,
            text="戦の列をダブルクリックでその戦を ◎→〇→△→×→－ と切り替え",
            foreground="#666666",
            wraplength=210,
            justify="left",
        )

        editor = ttk.Frame(frame)
        self.member_name_var = tk.StringVar()
        entry = ttk.Entry(editor, textvariable=self.member_name_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.add_member())
        ttk.Button(editor, text="追加", command=self.add_member).pack(
            side="left", padx=(6, 0)
        )

        buttons = ttk.Frame(frame)
        ttk.Button(buttons, text="一括追加", command=self.bulk_add_members).pack(
            side="left"
        )
        ttk.Button(buttons, text="削除", command=self.delete_members).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="軍師", command=self.toggle_strategist).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            buttons, text="↑", width=3, command=lambda: self.move_members(-1)
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            buttons, text="↓", width=3, command=lambda: self.move_members(1)
        ).pack(side="left", padx=(2, 0))

        # ◎〇△×－ ボタンとスペースキーが、どの戦に効くかをここで決める。
        target_row = ttk.Frame(frame)
        ttk.Label(target_row, text="対象:").pack(side="left")
        self.target_battle_var = tk.IntVar(value=-1)  # -1 は全戦
        ttk.Radiobutton(
            target_row, text="全戦", value=-1, variable=self.target_battle_var
        ).pack(side="left", padx=(4, 0))
        for i in range(BATTLE_COUNT):
            ttk.Radiobutton(
                target_row,
                text=f"{i + 1}戦",
                value=i,
                variable=self.target_battle_var,
            ).pack(side="left", padx=(4, 0))

        # 選択中のメンバーの参戦状況を直接指定する。30人を1件ずつ回すのは手間なので、
        # 循環トグルとは別に一発で決められる口を用意しておく。
        set_row = ttk.Frame(frame)
        ttk.Label(set_row, text="選択→").pack(side="left")
        for level in ATTENDANCE_LEVELS:
            ttk.Button(
                set_row,
                text=level,
                width=3,
                command=lambda lv=level: self.set_attendance(lv),
            ).pack(side="left", padx=(2, 0))

        buttons2 = ttk.Frame(frame)
        ttk.Button(
            buttons2, text="全員◎", command=lambda: self.set_all_attendance(ATTEND_BEST)
        ).pack(side="left")
        ttk.Button(
            buttons2, text="全員×", command=lambda: self.set_all_attendance(ATTEND_NO)
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            buttons2,
            text=f"全員{DEFAULT_ATTENDANCE}",
            command=lambda: self.set_all_attendance(DEFAULT_ATTENDANCE),
        ).pack(side="left", padx=(6, 0))

        buttons2.pack(side="bottom", fill="x", pady=(4, 0))
        set_row.pack(side="bottom", fill="x", pady=(4, 0))
        target_row.pack(side="bottom", fill="x", pady=(4, 0))
        buttons.pack(side="bottom", fill="x", pady=(6, 0))
        editor.pack(side="bottom", fill="x", pady=(8, 0))
        hint.pack(side="bottom", anchor="w", pady=(4, 0))
        self._add_scrollbars(member_box, self.member_tree)
        member_box.pack(fill="both", expand=True)
        return frame

    def _build_result_panel(self, parent: tk.Misc) -> ttk.Widget:
        frame = ttk.LabelFrame(parent, text="割り振り結果", padding=8)

        ttk.Label(frame, text="奥義別").pack(anchor="w")
        result_box = ttk.Frame(frame)
        self.result_tree = ttk.Treeview(
            result_box,
            columns=(
                "category",
                "first_half",
                "arcanum",
                "required",
                "per_battle",
                "members",
            ),
            show="headings",
            height=12,
        )
        self.result_tree.heading("category", text="種類")
        self.result_tree.heading("first_half", text="前半")
        self.result_tree.heading("arcanum", text="奥義")
        self.result_tree.heading("required", text="人数")
        # 数字=確実に出せる人数(◎〇)、△=△頼み、×=誰も出せない
        self.result_tree.heading("per_battle", text="確実 1/2/3戦")
        self.result_tree.heading("members", text="担当者")
        self.result_tree.column("category", width=100, anchor="w", stretch=False)
        self.result_tree.column("first_half", width=40, anchor="center", stretch=False)
        self.result_tree.column("arcanum", width=110, anchor="w", stretch=False)
        self.result_tree.column("required", width=48, anchor="center", stretch=False)
        self.result_tree.column("per_battle", width=84, anchor="center", stretch=False)
        # 担当者が多いと入り切らない。minwidth を確保して横スクロールで読ませる。
        self.result_tree.column(
            "members", width=420, minwidth=420, anchor="w", stretch=True
        )
        self.result_tree.tag_configure("short", foreground="#c0392b")
        self.result_tree.tag_configure("nobest", foreground="#c47f00")
        self.result_tree.tag_configure("fill", foreground="#1f6f8b")
        self._add_scrollbars(result_box, self.result_tree, horizontal=True)
        result_box.pack(fill="both", expand=True, pady=(2, 8))

        ttk.Label(frame, text="メンバー別").pack(anchor="w")
        load_box = ttk.Frame(frame)
        self.load_tree = ttk.Treeview(
            load_box,
            columns=tuple(f"lb{i}" for i in range(BATTLE_COUNT))
            + ("name", "count", "arcana"),
            show="headings",
            height=10,
        )
        for i in range(BATTLE_COUNT):
            self.load_tree.heading(f"lb{i}", text=f"{i + 1}戦")
            self.load_tree.column(f"lb{i}", width=38, anchor="center", stretch=False)
        self.load_tree.heading("name", text="メンバー")
        self.load_tree.heading("count", text="担当数")
        self.load_tree.heading("arcana", text="担当奥義")
        self.load_tree.column("name", width=110, anchor="w", stretch=False)
        self.load_tree.column("count", width=52, anchor="center", stretch=False)
        self.load_tree.column(
            "arcana", width=360, minwidth=360, anchor="w", stretch=True
        )
        self.load_tree.tag_configure("idle", foreground="#909090")
        self._add_scrollbars(load_box, self.load_tree, horizontal=True)
        load_box.pack(fill="both", expand=True, pady=(2, 0))
        return frame

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status_var).pack(anchor="w")
        self.battle_status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.battle_status_var).pack(anchor="w")
        ttk.Label(
            bar,
            text=f"自動保存: {self.autosave_file}",
            foreground="#666666",
        ).pack(anchor="w")

    # ------------------------------------------------------------------
    # 奥義の操作
    # ------------------------------------------------------------------
    def add_arcanum(self) -> None:
        name = self.arcanum_name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "奥義名を入力してください。", parent=self)
            return
        if any(a.name == name for a in self.roster.arcana):
            messagebox.showwarning(
                APP_TITLE, f"奥義「{name}」はすでに登録されています。", parent=self
            )
            return
        required = self._read_spin(self.arcanum_required_var, DEFAULT_REQUIRED)
        self.roster.arcana.append(
            Arcanum(
                name=name,
                required=required,
                category=self.arcanum_category_var.get(),
                first_half=self.arcanum_first_half_var.get(),
            )
        )
        self.arcanum_name_var.set("")
        self._changed()
        self._refresh_arcana()

    def update_arcanum(self) -> None:
        index = self._selected_index(self.arcana_tree)
        if index is None:
            return
        name = self.arcanum_name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "奥義名を入力してください。", parent=self)
            return
        if any(a.name == name for i, a in enumerate(self.roster.arcana) if i != index):
            messagebox.showwarning(
                APP_TITLE, f"奥義「{name}」はすでに登録されています。", parent=self
            )
            return
        self.roster.arcana[index] = Arcanum(
            name=name,
            required=self._read_spin(self.arcanum_required_var, DEFAULT_REQUIRED),
            category=self.arcanum_category_var.get(),
            first_half=self.arcanum_first_half_var.get(),
        )
        self._changed()
        self._refresh_arcana(select=index)

    def delete_arcanum(self) -> None:
        index = self._selected_index(self.arcana_tree)
        if index is None:
            return
        del self.roster.arcana[index]
        self._changed()
        self._refresh_arcana()

    def move_arcanum(self, delta: int) -> None:
        index = self._selected_index(self.arcana_tree)
        if index is None:
            return
        target = index + delta
        if not 0 <= target < len(self.roster.arcana):
            return
        arcana = self.roster.arcana
        arcana[index], arcana[target] = arcana[target], arcana[index]
        self._changed()
        self._refresh_arcana(select=target)

    def _on_arcanum_selected(self, _event: tk.Event | None = None) -> None:
        index = self._selected_index(self.arcana_tree)
        if index is None:
            return
        arcanum = self.roster.arcana[index]
        self.arcanum_name_var.set(arcanum.name)
        self.arcanum_required_var.set(arcanum.required)
        self.arcanum_category_var.set(arcanum.category)
        self.arcanum_first_half_var.set(arcanum.first_half)

    def sort_arcana_by_category(self) -> None:
        """奥義一覧を種類ごとにまとめ直す(種類の中の並びは今の順を保つ)."""
        self.roster.arcana.sort(key=lambda a: category_order(a.category))
        self._changed()
        self._refresh_arcana()

    def _update_category_hint(self) -> None:
        """選んだ種類が割り振りでどう扱われるかを添える."""
        category = get_category(self.arcanum_category_var.get())
        self.category_hint.configure(text=category.note)
        # 「瞬時(何度も)」は必要人数を確保しないので、入力させない。
        self.required_spin.configure(
            state="disabled" if category.fills_leftover else "normal"
        )

    def _on_arcanum_double_click(self, event: tk.Event) -> None:
        row = self.arcana_tree.identify_row(event.y)
        if row:
            self.arcana_tree.selection_set(row)
            self._on_arcanum_selected()

    # ------------------------------------------------------------------
    # メンバーの操作
    # ------------------------------------------------------------------
    def add_member(self) -> None:
        name = self.member_name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "メンバー名を入力してください。", parent=self)
            return
        if any(m.name == name for m in self.roster.members):
            messagebox.showwarning(
                APP_TITLE, f"メンバー「{name}」はすでに登録されています。", parent=self
            )
            return
        self.roster.members.append(Member(name=name))
        self.member_name_var.set("")
        self._changed()
        self._refresh_members()

    def bulk_add_members(self) -> None:
        dialog = BulkAddDialog(self)
        if not dialog.names:
            return
        existing = {m.name for m in self.roster.members}
        added, skipped = 0, []
        for name in dialog.names:
            if name in existing:
                skipped.append(name)
                continue
            existing.add(name)
            self.roster.members.append(Member(name=name))
            added += 1
        self._changed()
        self._refresh_members()
        if skipped:
            messagebox.showinfo(
                APP_TITLE,
                f"{added}人を追加しました。\n名前が重複していた{len(skipped)}人はスキップしました: "
                + "、".join(skipped),
                parent=self,
            )

    def delete_members(self) -> None:
        indexes = self._selected_indexes(self.member_tree)
        if not indexes:
            return
        for index in sorted(indexes, reverse=True):
            del self.roster.members[index]
        self._changed()
        self._refresh_members()

    def _target_battles(self) -> list[int]:
        """「対象」で選ばれている戦. 全戦なら3戦すべて."""
        target = self.target_battle_var.get()
        return list(range(BATTLE_COUNT)) if target < 0 else [target]

    @staticmethod
    def _next_level(level: str) -> str:
        try:
            position = ATTENDANCE_LEVELS.index(level)
        except ValueError:
            position = -1
        return ATTENDANCE_LEVELS[(position + 1) % len(ATTENDANCE_LEVELS)]

    def cycle_attendance(self, battle: int | None = None) -> None:
        """参戦状況を ◎→〇→△→×→－→◎ と送る.

        battle を渡すとその戦だけ、省略すると「対象」で選ばれた戦を送る。
        """
        indexes = self._selected_indexes(self.member_tree)
        if not indexes:
            return
        battles = [battle] if battle is not None else self._target_battles()
        for index in indexes:
            member = self.roster.members[index]
            for target in battles:
                member.attendance[target] = self._next_level(member.attendance[target])
        self._changed()
        self._refresh_members(select=indexes)

    def set_attendance(self, level: str) -> None:
        indexes = self._selected_indexes(self.member_tree)
        if not indexes:
            return
        for index in indexes:
            for battle in self._target_battles():
                self.roster.members[index].attendance[battle] = level
        self._changed()
        self._refresh_members(select=indexes)

    def set_all_attendance(self, level: str) -> None:
        """全員の、対象に選ばれている戦をまとめて指定する."""
        for member in self.roster.members:
            for battle in self._target_battles():
                member.attendance[battle] = level
        self._changed()
        self._refresh_members()

    def move_members(self, delta: int) -> None:
        """選択中のメンバーを1つ上/下へ動かす.

        複数選択していても、まとまりを保ったまま動く。端に着いた行や、
        隣も選択中で動かしようがない行はその場に残す。
        """
        selected = set(self._selected_indexes(self.member_tree))
        if not selected:
            return
        members = self.roster.members
        # 上へ動かすときは上の行から、下へ動かすときは下の行から処理する。
        order = sorted(selected, reverse=delta > 0)
        for index in order:
            target = index + delta
            if not 0 <= target < len(members) or target in selected:
                continue
            members[index], members[target] = members[target], members[index]
            selected.discard(index)
            selected.add(target)
        self._changed()
        self._refresh_members(select=sorted(selected))

    def toggle_strategist(self) -> None:
        """選択中のメンバーの軍師フラグを切り替える. 軍師には奥義を割り当てない."""
        indexes = self._selected_indexes(self.member_tree)
        if not indexes:
            return
        # 混在選択のときは「軍師にする」に揃える。
        make_strategist = not all(
            self.roster.members[i].is_strategist for i in indexes
        )
        for index in indexes:
            self.roster.members[index].is_strategist = make_strategist
        self._changed()
        self._refresh_members(select=indexes)

    def _on_member_double_click(self, event: tk.Event) -> None:
        row = self.member_tree.identify_row(event.y)
        if not row:
            return
        self.member_tree.selection_set(row)
        # 戦の列を叩いたときはその戦だけ、それ以外の列なら「対象」に従う。
        column = self.member_tree.identify_column(event.x)
        try:
            position = int(column.lstrip("#")) - 1
        except ValueError:
            position = -1
        battle = position if 0 <= position < BATTLE_COUNT else None
        self.cycle_attendance(battle)

    # ------------------------------------------------------------------
    # 割り振り
    # ------------------------------------------------------------------
    def _on_slots_changed(self) -> None:
        slots = self._read_spin(self.slots_var, self.roster.slots_per_member)
        if slots == self.roster.slots_per_member:
            return
        self.roster.slots_per_member = slots
        self._changed()
        self._update_status()

    def run_allocation(self) -> None:
        self._on_slots_changed()
        try:
            self.result = allocate(self.roster)
        except AllocationError as exc:
            self.result = None
            self._refresh_result()
            messagebox.showerror(
                APP_TITLE, "割り振りできませんでした。\n\n・" + "\n・".join(exc.errors), parent=self
            )
            self._update_status()
            return
        self._refresh_result()
        self._update_status()
        if self.result.warnings:
            messagebox.showinfo(
                APP_TITLE, "\n".join(self.result.warnings), parent=self
            )

    def copy_result(self) -> None:
        if not self.result:
            messagebox.showinfo(APP_TITLE, "先に「割り振る」を押してください。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(format_result_text(self.result))
        self.status_var.set("割り振り結果をクリップボードにコピーしました。")

    def export_result_csv(self) -> None:
        if not self.result:
            messagebox.showinfo(APP_TITLE, "先に「割り振る」を押してください。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="CSVに書き出す",
            defaultextension=".csv",
            filetypes=CSV_FILETYPES,
        )
        if not path:
            return
        try:
            export_csv(self.result, path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"書き出しに失敗しました。\n{exc}", parent=self)
            return
        self.status_var.set(f"CSVに書き出しました: {path}")

    def _invalidate_result(self) -> None:
        """入力が変わったので、表示中の結果は古いものとして捨てる."""
        if self.result is not None:
            self.result = None
            self._refresh_result()

    # ------------------------------------------------------------------
    # ファイル
    # ------------------------------------------------------------------
    def new_roster(self) -> None:
        if self.roster.arcana or self.roster.members:
            if not messagebox.askyesno(
                APP_TITLE, "入力内容を破棄して新規作成しますか?", parent=self
            ):
                return
        self.roster = Roster()
        self.current_path = None
        self.result = None
        self.slots_var.set(self.roster.slots_per_member)
        self._autosave()
        self._refresh_all()

    def open_roster(self) -> None:
        """別のファイルを読み込んで、以降の作業内容にする."""
        path = filedialog.askopenfilename(
            parent=self,
            title="設定を読み込む",
            filetypes=JSON_FILETYPES,
            initialdir=str(project_dir()),
        )
        if not path:
            return
        try:
            self.roster = load_roster(path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, f"読み込みに失敗しました。\n{exc}", parent=self)
            return
        self.current_path = Path(path)
        self.result = None
        self.slots_var.set(self.roster.slots_per_member)
        self._autosave()  # 読み込んだ内容を以降の自動保存先にも反映する。
        self._refresh_all()
        self.status_var.set(f"読み込みました: {path}")

    def save_roster_as(self) -> None:
        """自動保存とは別に、控えを好きな場所へ書き出す."""
        self._on_slots_changed()
        path = filedialog.asksaveasfilename(
            parent=self,
            title="別名で保存(控え)",
            defaultextension=".json",
            filetypes=JSON_FILETYPES,
            initialdir=str(project_dir()),
            initialfile=self.current_path.name if self.current_path else "kassen_backup.json",
        )
        if not path:
            return
        if Path(path).resolve() == self.autosave_file.resolve():
            messagebox.showwarning(
                APP_TITLE,
                "自動保存ファイルと同じ名前です。別の名前を付けてください。\n"
                f"({self.autosave_file.name} は自動保存が随時上書きします)",
                parent=self,
            )
            return
        try:
            save_roster(self.roster, path)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"保存に失敗しました。\n{exc}", parent=self)
            return
        self.current_path = Path(path)
        self._update_title()
        self.status_var.set(f"控えを保存しました: {path}")

    # ------------------------------------------------------------------
    # 表示の更新
    # ------------------------------------------------------------------
    def _refresh_all(self) -> None:
        self._refresh_arcana()
        self._refresh_members()
        self._refresh_result()
        self._update_title()

    def _refresh_arcana(self, select: int | None = None) -> None:
        self.arcana_tree.delete(*self.arcana_tree.get_children())
        for index, arcanum in enumerate(self.roster.arcana):
            self.arcana_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    FIRST_HALF_MARK if arcanum.first_half else "",
                    arcanum.name,
                    arcanum.category,
                    # 余り埋め専用の種類は必要人数を持たない。
                    "—" if arcanum.fills_leftover else arcanum.required,
                ),
            )
        if select is not None and 0 <= select < len(self.roster.arcana):
            self.arcana_tree.selection_set(str(select))
            self.arcana_tree.see(str(select))
        self._update_status()

    def _refresh_members(self, select: list[int] | None = None) -> None:
        self.member_tree.delete(*self.member_tree.get_children())
        for index, member in enumerate(self.roster.members):
            if member.is_strategist:
                tags: tuple[str, ...] = ("strategist",)
            elif not member.is_assignable():
                # 3戦とも×か－ なら、必要人数の確保には使われない。
                tags = ("absent",)
            else:
                tags = ()
            self.member_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    *member.attendance,
                    STRATEGIST_MARK if member.is_strategist else "",
                    member.name,
                ),
                tags=tags,
            )
        if select:
            valid = [str(i) for i in select if 0 <= i < len(self.roster.members)]
            if valid:
                self.member_tree.selection_set(valid)
                self.member_tree.see(valid[0])
        self._update_status()

    def _refresh_result(self) -> None:
        self.result_tree.delete(*self.result_tree.get_children())
        self.load_tree.delete(*self.load_tree.get_children())
        if not self.result:
            return

        # 種類ごとにまとめて見せる。
        ordered = sorted(
            enumerate(self.result.assignments),
            key=lambda pair: (category_order(pair[1].category), pair[0]),
        )
        for row, (_, assignment) in enumerate(ordered):
            if assignment.is_short or assignment.thin_battles:
                tags: tuple[str, ...] = ("short",)
            elif assignment.unsure_battles or assignment.uncovered_first_half:
                tags = ("nobest",)
            elif assignment.extra:
                tags = ("fill",)
            else:
                tags = ()
            self.result_tree.insert(
                "",
                "end",
                iid=str(row),
                values=(
                    assignment.category,
                    FIRST_HALF_MARK if assignment.first_half else "",
                    assignment.arcanum,
                    (
                        f"{len(assignment.members)}"
                        if assignment.required == 0
                        else f"{len(assignment.members)}/{assignment.required}"
                    ),
                    "/".join(assignment.battle_marks()),
                    "、".join(assignment.members),
                ),
                tags=tags,
            )

        # load は連合員の並び順で作ってあるので、そのまま出す。
        for index, (name, arcana) in enumerate(self.result.load.items()):
            self.load_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    *self.result.attendance.get(name, [""] * BATTLE_COUNT),
                    name,
                    len(arcana),
                    "、".join(arcana),
                ),
                tags=() if arcana else ("idle",),
            )

    def _update_title(self) -> None:
        suffix = f" - {self.current_path.name}" if self.current_path else ""
        self.title(APP_TITLE + suffix)

    def _update_status(self) -> None:
        strategists = len(self.roster.strategists())
        base = (
            f"奥義 {len(self.roster.arcana)}件 / 必要担当 {self.roster.demand()}枠 ・ "
            f"軍師{strategists}人 ・ "
            f"割当可 {len(self.roster.assignable_members())}人 × "
            f"{self.roster.slots_per_member}枠 = {self.roster.capacity()}枠"
        )
        battle_parts = []
        for battle in range(BATTLE_COUNT):
            counts = self.roster.count_by_attendance(battle)
            inner = " ".join(f"{lv}{counts[lv]}" for lv in ATTENDANCE_LEVELS)
            battle_parts.append(f"{BATTLE_LABELS[battle]} {inner}")
        self.battle_status_var.set("参戦: " + " ／ ".join(battle_parts))
        errors = validate(self.roster)
        if errors:
            self.status_var.set(base + "  ⚠ " + errors[0])
        elif self.result:
            detail = f"  ✓ 割り振り済み(使用 {self.result.used_slots}枠"
            if self.result.extra_slots:
                detail += f" / うち余り埋め {self.result.extra_slots}枠"
            short = len(self.result.reduced_arcana)
            if short:
                detail += f" / 必要人数に届かず {short}件"
            detail += ")"
            self.status_var.set(base + detail)
        else:
            self.status_var.set(base + "  → 「割り振る」を押してください")

    # ------------------------------------------------------------------
    # 小物
    # ------------------------------------------------------------------
    @staticmethod
    def _add_scrollbars(
        container: ttk.Frame, tree: ttk.Treeview, horizontal: bool = False
    ) -> None:
        """一覧にスクロールバーを付ける.

        連合員が30人いても画面外の行に届くようにするため、縦は必ず付ける。
        担当者名が長くなる結果表は横も付ける。
        """
        vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        if horizontal:
            hsb = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
            tree.configure(xscrollcommand=hsb.set)
            hsb.grid(row=1, column=0, sticky="ew")

    @staticmethod
    def _read_spin(var: tk.IntVar, fallback: int) -> int:
        """Spinboxに数字以外が打たれていても落ちないように読む."""
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            var.set(fallback)
            return fallback

    @staticmethod
    def _selected_index(tree: ttk.Treeview) -> int | None:
        selection = tree.selection()
        return int(selection[0]) if selection else None

    @staticmethod
    def _selected_indexes(tree: ttk.Treeview) -> list[int]:
        return sorted(int(iid) for iid in tree.selection())


def main() -> None:
    ArcanumApp().mainloop()
