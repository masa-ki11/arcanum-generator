"""合戦の奥義割り振りで扱うデータモデル."""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_REQUIRED = 2
"""奥義1つあたりの既定の担当人数. 1人落ちても穴が空かないように2人."""

DEFAULT_SLOTS_PER_MEMBER = 4
"""連合員1人が担当できる奥義数の既定値."""


FILE_VERSION = 6
"""保存形式のバージョン. 項目を増やしたらここだけ上げる.

読み込み側は 1〜この値 を受け付ける(storage.SUPPORTED_VERSIONS)。
二重管理にすると更新漏れでファイルが開けなくなるので、定義はここ1か所。
"""

BATTLE_COUNT = 3
"""1日の合戦の数. 参戦状況は戦ごとに持つが、奥義は1日通して変えない."""

BATTLE_LABELS: tuple[str, ...] = ("1戦目", "2戦目", "3戦目")


# --------------------------------------------------------------------------
# 参戦状況
# --------------------------------------------------------------------------
ATTEND_BEST = "◎"
ATTEND_YES = "〇"
ATTEND_MAYBE = "△"
ATTEND_NO = "×"
ATTEND_UNKNOWN = "－"

ATTENDANCE_LEVELS: tuple[str, ...] = (
    ATTEND_BEST,
    ATTEND_YES,
    ATTEND_MAYBE,
    ATTEND_NO,
    ATTEND_UNKNOWN,
)

ATTENDANCE_NOTES: dict[str, str] = {
    ATTEND_BEST: "前半から確実に参戦",
    ATTEND_YES: "参戦",
    ATTEND_MAYBE: "参戦できるか不明",
    ATTEND_NO: "不参戦",
    ATTEND_UNKNOWN: "未回答",
}

PRIMARY_ATTENDANCE: tuple[str, ...] = (ATTEND_BEST, ATTEND_YES)
"""必要人数の確保に優先して使う参戦状況."""

FALLBACK_ATTENDANCE: tuple[str, ...] = (ATTEND_MAYBE,)
"""◎〇だけでは足りないときにかぎって使う参戦状況."""

REQUIRED_ATTENDANCE: tuple[str, ...] = PRIMARY_ATTENDANCE + FALLBACK_ATTENDANCE
"""必要人数の確保に使える参戦状況(×と－は対象外)."""

DEFAULT_ATTENDANCE = ATTEND_UNKNOWN


def attendance_rank(level: str) -> int:
    """◎を0とした優先順位. 未知の値は末尾."""
    try:
        return ATTENDANCE_LEVELS.index(level)
    except ValueError:
        return len(ATTENDANCE_LEVELS)


# --------------------------------------------------------------------------
# 奥義の種類
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Category:
    """奥義の種類. 種類ごとに割り振りの扱いが変わる."""

    name: str
    fills_leftover: bool
    """True なら、必要人数を確保したあとの余り枠も埋めていく種類."""
    note: str


CATEGORIES: tuple[Category, ...] = (
    Category("絆", False, "必要人数ちょうど"),
    Category("一定", False, "必要人数ちょうど"),
    Category("瞬時(一度のみ)", False, "必要人数ちょうど"),
    Category("瞬時(何度も)", True, "必要人数＋余り枠も埋める"),
)

CATEGORY_NAMES: tuple[str, ...] = tuple(c.name for c in CATEGORIES)
DEFAULT_CATEGORY = CATEGORIES[0].name

_CATEGORY_BY_NAME = {c.name: c for c in CATEGORIES}
_UNKNOWN_CATEGORY = Category("", False, "未知の種類")


def get_category(name: str) -> Category:
    """種類名から定義を引く. 未知の名前は「必要人数ちょうど」として扱う."""
    return _CATEGORY_BY_NAME.get(name, _UNKNOWN_CATEGORY)


def category_order(name: str) -> int:
    """表示や集計で使う種類の並び順. 未知の種類は末尾."""
    try:
        return CATEGORY_NAMES.index(name)
    except ValueError:
        return len(CATEGORY_NAMES)


# --------------------------------------------------------------------------
# 奥義・連合員・入力一式
# --------------------------------------------------------------------------
@dataclass
class Arcanum:
    """合戦で設定する奥義1つ."""

    name: str
    required: int = DEFAULT_REQUIRED
    category: str = DEFAULT_CATEGORY
    first_half: bool = False
    """前半に無いと困る奥義. 担当には◎の人を優先して充てる."""
    for_vanguard: bool = False
    """前衛に持たせる奥義. どの戦にも前衛の担当が1人以上いるようにする."""

    @property
    def fills_leftover(self) -> bool:
        """余り枠を吸収する種類かどうか."""
        return get_category(self.category).fills_leftover

    @property
    def effective_required(self) -> int:
        """割り振りで実際に確保する人数.

        「瞬時(何度も)」は必ず入れなければならない奥義ではなく、余った枠に
        入れるだけなので0。確保のための枠を先取りしない。
        """
        return 0 if self.fills_leftover else self.required

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required": self.required,
            "category": self.category,
            "first_half": self.first_half,
            "for_vanguard": self.for_vanguard,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Arcanum":
        return cls(
            name=str(data["name"]),
            required=int(data.get("required", DEFAULT_REQUIRED)),
            # 種類・前半必須を持たない古い形式のファイルは既定値で読む。
            category=str(data.get("category", DEFAULT_CATEGORY)),
            first_half=bool(data.get("first_half", False)),
            for_vanguard=bool(data.get("for_vanguard", False)),
        )


def default_attendance() -> list[str]:
    return [DEFAULT_ATTENDANCE] * BATTLE_COUNT


def default_vanguard() -> list[bool]:
    return [False] * BATTLE_COUNT


def normalize_vanguard(value) -> list[bool]:
    """前衛設定を戦数ぶんのリストに揃える. 単一の真偽値は全戦に広げる."""
    if isinstance(value, bool):
        flags = [value] * BATTLE_COUNT
    else:
        flags = [bool(v) for v in value]
    if len(flags) < BATTLE_COUNT:
        flags += [False] * (BATTLE_COUNT - len(flags))
    return flags[:BATTLE_COUNT]


def normalize_attendance(value) -> list[str]:
    """保存形式のゆらぎを 戦数ぶんのリスト に揃える.

    単一の文字列(3戦に分かれる前の形式)は全戦に同じ値を入れる。
    """
    if isinstance(value, str):
        levels = [value] * BATTLE_COUNT
    else:
        levels = [str(v) for v in value]
    if len(levels) < BATTLE_COUNT:
        levels += [DEFAULT_ATTENDANCE] * (BATTLE_COUNT - len(levels))
    return levels[:BATTLE_COUNT]


@dataclass
class Member:
    """連合員1人. 参戦状況は戦ごとに持つ."""

    name: str
    attendance: list[str] = field(default_factory=default_attendance)
    is_strategist: bool = False
    """軍師. 奥義の割り当て対象から外す."""
    vanguard: list[bool] = field(default_factory=default_vanguard)
    """戦ごとの前衛設定. 参戦状況と同じく戦ごとに変わる."""

    def __post_init__(self) -> None:
        self.attendance = normalize_attendance(self.attendance)
        self.vanguard = normalize_vanguard(self.vanguard)

    def is_vanguard(self, battle: int) -> bool:
        return self.vanguard[battle]

    def vanguard_battles(self) -> set[int]:
        return {i for i, v in enumerate(self.vanguard) if v}

    def can_be_vanguard(self) -> bool:
        """前衛として出る戦が1つでもあるか.

        前衛に設定されていても、その戦に参戦しないなら前衛としては数えない。
        """
        return any(
            self.is_vanguard(battle) and self.joins(battle)
            for battle in range(BATTLE_COUNT)
        )

    def level(self, battle: int) -> str:
        return self.attendance[battle]

    def best_battles(self) -> set[int]:
        """◎(前半から確実に参戦)を出している戦."""
        return {i for i, lv in enumerate(self.attendance) if lv == ATTEND_BEST}

    def joins(self, battle: int) -> bool:
        """その戦に出る見込みがあるか(◎〇△). △は来ないこともある."""
        return self.attendance[battle] in REQUIRED_ATTENDANCE

    def is_sure(self, battle: int) -> bool:
        """その戦に確実に出るか(◎〇). 担当を任せられるのはこの人たち."""
        return self.attendance[battle] in PRIMARY_ATTENDANCE

    def is_primary(self) -> bool:
        """どこか1戦でも◎〇なら、優先して割り当てる相手とみなす."""
        return any(lv in PRIMARY_ATTENDANCE for lv in self.attendance)

    def is_assignable(self) -> bool:
        """どこか1戦でも◎〇△なら、必要人数の確保に使える."""
        return any(lv in REQUIRED_ATTENDANCE for lv in self.attendance)

    def priority_score(self) -> int:
        """3戦ぶんの参戦確度. 小さいほど確実に出る(◎◎◎なら0)."""
        return sum(attendance_rank(lv) for lv in self.attendance)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "attendance": list(self.attendance),
            "is_strategist": self.is_strategist,
            "vanguard": list(self.vanguard),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Member":
        if "attendance" in data:
            attendance = normalize_attendance(data["attendance"])
        else:
            # 参戦を True/False で持っていた古い形式からの読み替え。
            level = ATTEND_YES if data.get("attending", True) else ATTEND_NO
            attendance = [level] * BATTLE_COUNT
        return cls(
            name=str(data["name"]),
            attendance=attendance,
            is_strategist=bool(data.get("is_strategist", False)),
            # 前衛を持たない古い形式は全戦とも後衛として読む。
            vanguard=normalize_vanguard(data.get("vanguard", False)),
        )


@dataclass
class Roster:
    """合戦1回分の入力一式."""

    arcana: list[Arcanum] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    slots_per_member: int = DEFAULT_SLOTS_PER_MEMBER

    # -- メンバーの分類 ----------------------------------------------------
    def fill_members(self) -> list[Member]:
        """奥義を持たせうるメンバー全員(軍師だけ除く).

        「瞬時(何度も)」の余り埋めは、×や－の人にも配る。
        """
        return [m for m in self.members if not m.is_strategist]

    def assignable_members(self) -> list[Member]:
        """必要人数の確保に使えるメンバー(軍師を除き、どこか1戦でも◎〇△)."""
        return [m for m in self.fill_members() if m.is_assignable()]

    def primary_members(self) -> list[Member]:
        """優先して使うメンバー(軍師を除き、どこか1戦でも◎〇)."""
        return [m for m in self.fill_members() if m.is_primary()]

    def strategists(self) -> list[Member]:
        return [m for m in self.members if m.is_strategist]

    def count_by_attendance(self, battle: int) -> dict[str, int]:
        """指定した戦の、軍師を除いた参戦状況ごとの人数."""
        counts = {level: 0 for level in ATTENDANCE_LEVELS}
        for member in self.fill_members():
            level = member.level(battle)
            if level in counts:
                counts[level] += 1
        return counts

    def joiners(self, battle: int) -> list[Member]:
        """その戦に出る見込みのあるメンバー(軍師を除く)."""
        return [m for m in self.fill_members() if m.joins(battle)]

    def vanguards(self, battle: int) -> list[Member]:
        """その戦に前衛として出るメンバー(軍師を除き、出る見込みのある人だけ)."""
        return [m for m in self.joiners(battle) if m.is_vanguard(battle)]

    # -- 枠の計算 ----------------------------------------------------------
    def demand(self) -> int:
        """必ず確保しなければならない担当枠の総数.

        「瞬時(何度も)」は余り枠に入れるだけなので数えない。
        """
        return sum(a.effective_required for a in self.arcana)

    def required_arcana(self) -> list[Arcanum]:
        """必要人数を確保する対象の奥義(「瞬時(何度も)」以外)."""
        return [a for a in self.arcana if not a.fills_leftover]

    def capacity(self) -> int:
        """必要人数の確保に使える担当枠の総数(◎〇△ × 1人あたりの枠)."""
        return len(self.assignable_members()) * self.slots_per_member

    def fill_capacity(self) -> int:
        """余り埋めまで含めた担当枠の総数(軍師以外の全員 × 1人あたりの枠)."""
        return len(self.fill_members()) * self.slots_per_member

    def fill_arcana(self) -> list[Arcanum]:
        """余り枠を吸収する種類の奥義."""
        return [a for a in self.arcana if a.fills_leftover]

    # -- 保存 --------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": FILE_VERSION,
            "battle_count": BATTLE_COUNT,
            "slots_per_member": self.slots_per_member,
            "arcana": [a.to_dict() for a in self.arcana],
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Roster":
        return cls(
            arcana=[Arcanum.from_dict(d) for d in data.get("arcana", [])],
            members=[Member.from_dict(d) for d in data.get("members", [])],
            slots_per_member=int(
                data.get("slots_per_member", DEFAULT_SLOTS_PER_MEMBER)
            ),
        )
