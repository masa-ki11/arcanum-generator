"""奥義を参戦メンバーへ割り振るロジック."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .models import (
    ATTENDANCE_LEVELS,
    BATTLE_COUNT,
    BATTLE_LABELS,
    CATEGORY_NAMES,
    PAIR_PER_BATTLE,
    Arcanum,
    Member,
    Roster,
)


def _constraint_order(arcanum: Arcanum) -> tuple:
    """制約のきつい奥義から順に処理するための並び順.

    担当を選べる相手が限られる奥義(前半必須・前衛向け)、人数を多く要る奥義
    (各戦2人・必要人数の多いもの)ほど先。あとから埋め直すのが難しい。
    """
    return (
        not arcanum.first_half,
        not arcanum.for_vanguard,
        not arcanum.two_per_battle,
        -arcanum.required,
        arcanum.name,
    )


def sure_quota(arcanum: Arcanum) -> int:
    """その奥義が1戦あたりに確保したい「確実に出せる担当(◎〇)」の人数.

    通常は1人。「各戦2人」を付けた奥義だけ2人。
    """
    return PAIR_PER_BATTLE if arcanum.two_per_battle else 1


class AllocationError(Exception):
    """割り振り不能な入力だったときに投げる."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(" / ".join(errors))
        self.errors = errors


@dataclass
class Assignment:
    """奥義1つと、その担当に割り当てられたメンバー."""

    arcanum: str
    required: int
    members: list[str] = field(default_factory=list)
    category: str = ""
    first_half: bool = False
    for_vanguard: bool = False
    two_per_battle: bool = False
    vanguard_battles: list[bool] = field(
        default_factory=lambda: [False] * BATTLE_COUNT
    )
    """戦ごとに、その戦に参戦している前衛の担当がいるか."""
    per_battle: list[int] = field(default_factory=lambda: [0] * BATTLE_COUNT)
    """戦ごとの「出る見込みのある担当者(◎〇△)」の人数."""
    sure_per_battle: list[int] = field(default_factory=lambda: [0] * BATTLE_COUNT)
    """戦ごとの「確実に出る担当者(◎〇)」の人数."""
    best_battles: list[bool] = field(default_factory=lambda: [False] * BATTLE_COUNT)
    """戦ごとに◎の担当者がいるか(前半必須の充足判定に使う)."""

    @property
    def thin_battles(self) -> list[int]:
        """担当者が1人も出られない戦."""
        return [i for i, count in enumerate(self.per_battle) if count == 0]

    @property
    def unsure_battles(self) -> list[int]:
        """確実に出る担当がおらず、△頼みになっている戦."""
        return [
            i
            for i, sure in enumerate(self.sure_per_battle)
            if sure == 0 and self.per_battle[i] > 0
        ]

    def battle_marks(self) -> list[str]:
        """戦ごとの状態を1文字で表す. 確実な人数、△頼みなら△、誰もいなければ×."""
        marks = []
        for battle, sure in enumerate(self.sure_per_battle):
            if sure:
                marks.append(str(sure))
            elif self.per_battle[battle]:
                marks.append("△")
            else:
                marks.append("×")
        return marks

    @property
    def short_pair_battles(self) -> list[int]:
        """「各戦2人」なのに、確実に出せる担当が2人に届かなかった戦.

        1人もいない戦は unsure_battles(△頼み)や thin_battles(×)として別に
        報告するので、ここでは1人しかいない戦だけを挙げる。同じ戦を二重に
        並べても読み手には区別が付かない。
        """
        if not self.two_per_battle:
            return []
        return [
            i
            for i, sure in enumerate(self.sure_per_battle)
            if 0 < sure < PAIR_PER_BATTLE
        ]

    @property
    def uncovered_first_half(self) -> list[int]:
        """前半必須なのに◎の担当がいない戦."""
        if not self.first_half:
            return []
        return [i for i, covered in enumerate(self.best_battles) if not covered]

    @property
    def uncovered_vanguard(self) -> list[int]:
        """前衛向けなのに前衛の担当がいない戦."""
        if not self.for_vanguard:
            return []
        return [i for i, covered in enumerate(self.vanguard_battles) if not covered]

    @property
    def is_short(self) -> bool:
        """必要人数に届かなかった奥義かどうか."""
        return len(self.members) < self.required

    @property
    def is_uncovered(self) -> bool:
        """担当が1人も付けられなかった奥義かどうか."""
        return not self.members

    @property
    def extra(self) -> int:
        """必要人数を超えて上乗せされた人数(余り枠を吸収した分)."""
        return max(0, len(self.members) - self.required)


@dataclass
class CarryoverReport:
    """前回の割り当てをどこまで引き継げたかの記録.

    引き継ぎは黙って選別すると「なぜ担当が変わったのか」が追えなくなるので、
    維持・除外・新規をすべて残して結果に載せる。
    """

    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    """引き継げなかった担当. 「奥義: 名前(理由)」の形で持つ."""
    added: list[str] = field(default_factory=list)
    """引き継ぎのあとで新しく足された担当."""
    member_added: dict[str, list[str]] = field(default_factory=dict)
    """メンバー名 -> 今回から新しく担当することになった奥義."""
    member_removed: dict[str, list[str]] = field(default_factory=dict)
    """メンバー名 -> 前回は担当していて今回は外れた奥義."""

    @property
    def source_total(self) -> int:
        """引き継ぎ元にあった担当の件数."""
        return len(self.kept) + len(self.dropped)

    def summary(self) -> str:
        return (
            f"前回の担当{self.source_total}件のうち{len(self.kept)}件をそのまま維持、"
            f"{len(self.dropped)}件を外し、{len(self.added)}件を新しく足しました。"
        )

    def changed_members(self) -> set[str]:
        """担当が1つでも増減したメンバーの名前."""
        return {name for name, v in self.member_added.items() if v} | {
            name for name, v in self.member_removed.items() if v
        }

    def is_new(self, arcanum: str, member: str) -> bool:
        """その人がその奥義に今回から入ったかどうか."""
        return arcanum in self.member_added.get(member, ())

    def change_note(self, member: str) -> str:
        """その人の担当の増減を1行で表す. 変わっていなければ空文字."""
        parts = []
        if self.member_added.get(member):
            parts.append("追加 " + "、".join(self.member_added[member]))
        if self.member_removed.get(member):
            parts.append("外れ " + "、".join(self.member_removed[member]))
        return " / ".join(parts)


@dataclass
class AllocationResult:
    """割り振り結果一式."""

    assignments: list[Assignment]
    load: dict[str, list[str]]
    """メンバー名 -> 担当することになった奥義名のリスト."""
    demand: int
    capacity: int
    fill_capacity: int = 0
    """余り埋めまで含めた枠の総数(軍師以外の全員 × 1人あたりの枠)."""
    attendance: dict[str, str] = field(default_factory=dict)
    """メンバー名 -> 戦ごとの参戦状況."""
    vanguard: dict[str, list[bool]] = field(default_factory=dict)
    """メンバー名 -> 戦ごとの前衛設定."""
    carryover: CarryoverReport | None = None
    """引き継ぎ割り振りのときだけ入る. ゼロから割り振ったときは None."""
    warnings: list[str] = field(default_factory=list)
    """人手不足・△頼みなどの困りごと. 結果の末尾に出して連合に共有する."""
    notes: list[str] = field(default_factory=list)
    """引き継ぎの内訳や空き枠などの作業用の情報. 結果には出さない."""

    def to_carryover(self) -> dict[str, list[str]]:
        """次の日の起点として使える形(奥義名 -> 担当者名)に変換する."""
        return {a.arcanum: list(a.members) for a in self.assignments}

    @property
    def spare_slots(self) -> int:
        """どこにも使われずに残った担当枠の数."""
        return self.fill_capacity - self.used_slots

    @property
    def used_slots(self) -> int:
        """実際に埋まった担当枠の数."""
        return sum(len(arcana) for arcana in self.load.values())

    @property
    def extra_slots(self) -> int:
        """「瞬時(何度も)」系が余り枠から吸収した数."""
        return sum(a.extra for a in self.assignments)

    @property
    def reduced_arcana(self) -> list[Assignment]:
        """人手が足りず、必要人数に届かなかった奥義."""
        return [a for a in self.assignments if a.is_short]

    def idle_members(self) -> list[str]:
        """1つも奥義を担当しなかったメンバー."""
        return [name for name, arcana in self.load.items() if not arcana]


def validate(roster: Roster) -> list[str]:
    """割り振りが成り立たない入力を洗い出す. 空リストなら実行可能.

    人手が足りないケースはエラーにしない。必要人数を減らして成立させ、
    足りなかったことは結果の警告で伝える。
    """
    errors: list[str] = []

    if roster.slots_per_member < 1:
        errors.append("1人あたりの奥義枠は1以上にしてください。")
    if not roster.arcana:
        errors.append("奥義が1つも設定されていません。")
    if not roster.assignable_members():
        errors.append(
            "割り当てられるメンバーがいません(軍師を除いた◎〇△が0人です)。"
        )

    names = [a.name for a in roster.arcana]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"奥義名「{dup}」が重複しています。")

    member_names = [m.name for m in roster.members]
    for dup in sorted({n for n in member_names if member_names.count(n) > 1}):
        errors.append(f"メンバー名「{dup}」が重複しています。")

    for arcanum in roster.arcana:
        if arcanum.category not in CATEGORY_NAMES:
            errors.append(
                f"奥義「{arcanum.name}」の種類「{arcanum.category}」が不明です"
                f"(使えるのは {'、'.join(CATEGORY_NAMES)})。"
            )
        if arcanum.required < 1:
            errors.append(f"奥義「{arcanum.name}」の必要人数は1以上にしてください。")

    for member in roster.members:
        for battle, level in enumerate(member.attendance):
            if level not in ATTENDANCE_LEVELS:
                errors.append(
                    f"メンバー「{member.name}」の{BATTLE_LABELS[battle]}の参戦状況"
                    f"「{level}」が不明です(使えるのは {'、'.join(ATTENDANCE_LEVELS)})。"
                )

    return errors


class _Allocator:
    """割り振りの作業用. load と picked を持ち回る."""

    def __init__(
        self,
        roster: Roster,
        rng: random.Random,
        carryover: dict[str, list[str]] | None = None,
    ) -> None:
        self.roster = roster
        self.rng = rng
        fill_members = roster.fill_members()
        self.member: dict[str, Member] = {m.name: m for m in fill_members}
        self.arcanum: dict[str, Arcanum] = {a.name: a for a in roster.arcana}
        self.load: dict[str, list[str]] = {m.name: [] for m in fill_members}
        self.picked: dict[str, list[str]] = {a.name: [] for a in roster.arcana}
        self.primary = [m.name for m in roster.primary_members()]
        primary_set = set(self.primary)
        self.fallback = [
            m.name for m in roster.assignable_members() if m.name not in primary_set
        ]
        self.source = carryover or {}
        self.report = CarryoverReport() if carryover is not None else None

    # -- 引き継ぎ ----------------------------------------------------------
    def _reject_reason(self, arcanum: Arcanum, name: str) -> str:
        """その担当を引き継げない理由. 引き継げるなら空文字.

        参戦状況による剪定が本体。◎だった人が×になっても担当が残ると、
        枠を食い潰したうえに実際は誰も出ない戦ができてしまう。

        △だけに落ちた人は、代わりに入れる◎〇が実際にいるときにかぎって外す。
        残したまま頭数に数えると「必要2人」の中身が確実1人+△になり、
        1人落ちても穴が空かないという2人体制の意味が失われる。
        ただし代わりがいないのに外すと、結局は通常の段階が△から選び直すので
        顔ぶれが入れ替わるだけで質は変わらない。実測でも、枠に余裕のない
        連合では外しても確実な担当の数は1人も増えなかった。
        """
        who = self.member.get(name)
        if who is None:
            return "メンバーがいない(削除か軍師)"
        if name in self.picked[arcanum.name]:
            return "同じ奥義に重複"
        if len(self.load[name]) >= self.roster.slots_per_member:
            return "奥義枠がいっぱい"
        if not self.is_eligible(arcanum, name):
            return "前衛でなくなった"
        # 余り埋めの種類は×や－の人にも配る段階なので、参戦では切らない。
        if not arcanum.fills_leftover:
            if not who.is_assignable():
                return "今回は参戦しない"
            # _choose は空き枠と前衛の条件も見たうえで候補を返す。
            if not who.is_primary() and self._choose(arcanum, self.primary):
                return "△だけになり、確実に出せる人と交代"
        return ""

    def _seed_one(self, arcanum: Arcanum, name: str) -> None:
        label = f"{arcanum.name}: {name}"
        reason = self._reject_reason(arcanum, name)
        if reason:
            self.report.dropped.append(f"{label}({reason})")
            return
        self.load[name].append(arcanum.name)
        self.picked[arcanum.name].append(name)
        self.report.kept.append(label)

    def _seed_order(self, fill: bool) -> list[Arcanum]:
        """引き継ぐ順.

        枠が足りずに全部は引き継げないとき、あとから埋め直すのが難しい奥義
        (前半必須・前衛向け・各戦2人)を先に確保しておく。
        """
        return sorted(
            (a for a in self.roster.arcana if a.fills_leftover == fill),
            key=_constraint_order,
        )

    def seed_carryover(self, fill: bool) -> None:
        """前回ぶんを取り込む. fill=False は必須の奥義、True は余り埋めの種類.

        必須ぶんを先に入れて通常の段階を回し、余り埋めぶんは最後に入れる。
        余り埋めが先に枠を押さえてしまうと、本来そこに入るべき必須の奥義が
        入れなくなるため。
        """
        if self.report is None:
            return
        if not fill:
            # 今の奥義に無い名前は、以降どの段階でも触れられないのでここで報告する。
            known = {a.name for a in self.roster.arcana}
            for arcanum_name, names in self.source.items():
                if arcanum_name not in known:
                    for name in names:
                        self.report.dropped.append(
                            f"{arcanum_name}: {name}(奥義が無くなった)"
                        )
        for arcanum in self._seed_order(fill):
            for name in self.source.get(arcanum.name, []):
                self._seed_one(arcanum, name)

    def record_changes(self) -> None:
        """引き継ぎ前後の差分を洗い出す.

        メンバーごとの増減は、選別の過程ではなく前後の担当そのものを比べて出す。
        引き継ぎ元で重複していた担当や、いったん外れて別の段階で戻ってきた担当を
        「変更」に数えてしまわないため。
        """
        if self.report is None:
            return
        for arcanum in self.roster.arcana:
            before = set(self.source.get(arcanum.name, []))
            for name in self.picked[arcanum.name]:
                if name not in before:
                    self.report.added.append(f"{arcanum.name}: {name}")

        # 並び順は画面と同じ奥義順にする。今は無い奥義(前回だけの担当)は末尾。
        order = {a.name: i for i, a in enumerate(self.roster.arcana)}

        def in_screen_order(arcanum_name: str) -> tuple[int, str]:
            return (order.get(arcanum_name, len(order)), arcanum_name)

        before_by_member: dict[str, set[str]] = {}
        for arcanum_name, names in self.source.items():
            for name in names:
                before_by_member.setdefault(name, set()).add(arcanum_name)

        for name, arcana in self.load.items():
            was = before_by_member.get(name, set())
            now = set(arcana)
            self.report.member_added[name] = sorted(now - was, key=in_screen_order)
            self.report.member_removed[name] = sorted(was - now, key=in_screen_order)

    def uncovered_first_half(self, arcanum: Arcanum) -> set[int]:
        """前半必須の奥義で、まだ◎の担当がいない戦."""
        covered: set[int] = set()
        for name in self.picked[arcanum.name]:
            covered |= self.member[name].best_battles()
        return set(range(BATTLE_COUNT)) - covered

    def _best_gain(self, name: str, uncovered: set[int]) -> int:
        """その人を足すと、◎で新たに埋まる戦の数."""
        return len(self.member[name].best_battles() & uncovered)

    def thin_battles(self, arcanum: Arcanum) -> set[int]:
        """担当者が誰も出られない戦(△すらいない)."""
        covered = {
            battle
            for name in self.picked[arcanum.name]
            for battle in range(BATTLE_COUNT)
            if self.member[name].joins(battle)
        }
        return set(range(BATTLE_COUNT)) - covered

    def sure_counts(self, arcanum: Arcanum) -> list[int]:
        """戦ごとの、確実に出る担当(◎〇)の人数."""
        counts = [0] * BATTLE_COUNT
        for name in self.picked[arcanum.name]:
            for battle in range(BATTLE_COUNT):
                if self.member[name].is_sure(battle):
                    counts[battle] += 1
        return counts

    def unsure_battles(self, arcanum: Arcanum, needed: int = 1) -> set[int]:
        """確実に出る担当(◎〇)が needed 人に足りていない戦.

        △は来ないことがあるので、埋まったとはみなさない。
        needed は段階ごとに変える。まず全奥義を1人で埋め、そのあと
        「各戦2人」の奥義だけ2人まで足す。
        """
        return {
            battle
            for battle, count in enumerate(self.sure_counts(arcanum))
            if count < needed
        }

    def _join_gain(self, name: str, thin: set[int]) -> int:
        """その人を足すと、誰かしら出られるようになる戦の数."""
        return sum(1 for battle in thin if self.member[name].joins(battle))

    def _sure_gain(self, name: str, unsure: set[int]) -> int:
        """その人を足すと、確実に出せるようになる戦の数."""
        return sum(1 for battle in unsure if self.member[name].is_sure(battle))

    def uncovered_vanguard(self, arcanum: Arcanum) -> set[int]:
        """前衛の担当(その戦に参戦していて前衛)がいない戦."""
        covered = {
            battle
            for name in self.picked[arcanum.name]
            for battle in range(BATTLE_COUNT)
            if self.member[name].is_vanguard(battle)
            and self.member[name].joins(battle)
        }
        return set(range(BATTLE_COUNT)) - covered

    def is_eligible(self, arcanum: Arcanum, name: str) -> bool:
        """その奥義の担当になれる人か.

        前衛向けの奥義は、前衛として出る戦が1つでもある人にしか付けない。
        後衛の人に持たせても意味がないので、人数が足りなければ担当を減らす。
        """
        if not arcanum.for_vanguard:
            return True
        return self.member[name].can_be_vanguard()

    def _vanguard_gain(self, name: str, uncovered: set[int]) -> int:
        """その人を足すと、前衛の担当が付く戦の数."""
        who = self.member[name]
        return sum(
            1 for battle in uncovered if who.is_vanguard(battle) and who.joins(battle)
        )

    def _sort_key(
        self,
        arcanum: Arcanum,
        name: str,
        uncovered: set[int],
        rearguard: set[int],
        unsure: set[int],
        thin: set[int],
        need_best_gain: bool,
        need_vanguard_gain: bool,
        need_sure_gain: bool,
        need_join_gain: bool,
    ):
        """担当者を選ぶ優先順位.

        穴を埋めにいく段階では「埋まる戦の数」を最優先にして、最小の人数で塞ぐ。
        前半必須の奥義は通常時も◎であることを最優先にする(あとから◎を足す
        ことになると1人余計に使うため)。
        それ以外は担当数の均等を優先し、穴埋め効果は同着のタイブレークに
        とどめる。ここを主キーにすると全戦出られる人にばかり寄ってしまう。
        """
        load = len(self.load[name])
        score = self.member[name].priority_score()
        best_gain = -self._best_gain(name, uncovered)
        van_gain = -self._vanguard_gain(name, rearguard)
        sure_gain = -self._sure_gain(name, unsure)
        join_gain = -self._join_gain(name, thin)
        if need_best_gain:
            return (best_gain, van_gain, sure_gain, score, load)
        if need_vanguard_gain:
            return (van_gain, sure_gain, load, score)
        if need_sure_gain:
            return (sure_gain, load, score)
        if need_join_gain:
            return (join_gain, load, score)
        if arcanum.first_half:
            return (best_gain, van_gain, load, sure_gain, score)
        if arcanum.for_vanguard:
            return (van_gain, load, sure_gain, score)
        return (load, sure_gain, join_gain, score)

    def _choose(
        self,
        arcanum: Arcanum,
        pool: list[str],
        need_best_gain: bool = False,
        need_vanguard_gain: bool = False,
        need_sure_gain: bool = False,
        need_join_gain: bool = False,
        sure_needed: int = 1,
    ) -> str | None:
        """pool から担当を1人選ぶ. 選べなければ None."""
        held = set(self.picked[arcanum.name])
        candidates = [
            name
            for name in pool
            if name not in held
            and len(self.load[name]) < self.roster.slots_per_member
            and self.is_eligible(arcanum, name)
        ]
        if not candidates:
            return None
        # 同着はランダムに散らす。固定順だと同じ顔ぶれが繰り返し組まれる。
        self.rng.shuffle(candidates)

        uncovered = self.uncovered_first_half(arcanum) if arcanum.first_half else set()
        rearguard = self.uncovered_vanguard(arcanum) if arcanum.for_vanguard else set()
        unsure = self.unsure_battles(arcanum, sure_needed)
        thin = self.thin_battles(arcanum)

        if need_best_gain:
            if not arcanum.first_half:
                return None
            candidates = [n for n in candidates if self._best_gain(n, uncovered)]
        if need_vanguard_gain:
            if not arcanum.for_vanguard:
                return None
            candidates = [n for n in candidates if self._vanguard_gain(n, rearguard)]
        if need_sure_gain:
            candidates = [n for n in candidates if self._sure_gain(n, unsure)]
        if need_join_gain:
            candidates = [n for n in candidates if self._join_gain(n, thin)]
        if not candidates:
            return None

        return min(
            candidates,
            key=lambda n: self._sort_key(
                arcanum,
                n,
                uncovered,
                rearguard,
                unsure,
                thin,
                need_best_gain,
                need_vanguard_gain,
                need_sure_gain,
                need_join_gain,
            ),
        )

    def assign_one(
        self,
        arcanum: Arcanum,
        need_best_gain: bool = False,
        need_vanguard_gain: bool = False,
        need_sure_gain: bool = False,
        need_join_gain: bool = False,
        sure_needed: int = 1,
    ) -> bool:
        """◎〇から1人充てる. いなければ△から充てる. どちらも無理なら False."""
        for pool in (self.primary, self.fallback):
            name = self._choose(
                arcanum,
                pool,
                need_best_gain,
                need_vanguard_gain,
                need_sure_gain,
                need_join_gain,
                sure_needed,
            )
            if name is not None:
                self.load[name].append(arcanum.name)
                self.picked[arcanum.name].append(name)
                return True
        return False

    def cover_vanguard(self, arcanum: Arcanum) -> None:
        """前衛向けの奥義に、各戦の前衛担当が1人以上入るまで足す.

        前衛は戦ごとに変わるので、1人で3戦ぶんまかなえるとは限らない。
        必要人数を超えることがある。前衛が足りなければ諦めて警告に回す。
        """
        while self.uncovered_vanguard(arcanum):
            if not self.assign_one(arcanum, need_vanguard_gain=True):
                return

    def cover_battles(self, arcanum: Arcanum) -> None:
        """どの戦にも確実に出せる担当(◎〇)が1人以上いるまで足す.

        必要人数(既定2人)を超えることがある。2人いても両方が同じ戦を欠けば
        その戦は穴になるため、人数より穴を塞ぐことを優先する。
        △は来ないことがあるので埋まったとはみなさないが、◎〇で埋められない
        戦については、何も入れないよりましなので△でも入れておく。
        """
        while self.unsure_battles(arcanum):
            if not self.assign_one(arcanum, need_sure_gain=True):
                break
        while self.thin_battles(arcanum):
            if not self.assign_one(arcanum, need_join_gain=True):
                return

    def cover_battles_twice(self, arcanum: Arcanum) -> None:
        """「各戦2人」の奥義に、どの戦も◎〇が2人になるまで担当を足す.

        全奥義に1人ずつ配った直後に回す(段階1.5)。この印は「他の奥義を削って
        でも各戦2人にしたい」という指定なので、他の穴埋めより先に取る。
        あとに回すと枠が埋まったころには2人目を足せる相手が残っておらず、
        実測(連合20人×4枠=76枠、必須26奥義)では前衛向けの2件に印を付けた
        組み合わせで2人未達が出た。先に取ると未達は消え、他の奥義の穴も
        かえって減った(×が2割減)。動かせる人を先に押さえたほうが、残りを
        埋める段階で融通が利くため。

        2人目を足せる◎〇がいなければ、そこで諦めて警告に回す。△を足しても
        「確実に2人」にはならないので、頭数だけ増やすことはしない。
        """
        while self.unsure_battles(arcanum, PAIR_PER_BATTLE):
            if not self.assign_one(
                arcanum, need_sure_gain=True, sure_needed=PAIR_PER_BATTLE
            ):
                return

    def cover_first_half(self, arcanum: Arcanum) -> None:
        """前半必須の奥義に、各戦の◎が1人以上入るまで担当を足す.

        必要人数(既定2人)を超えることがある。各戦バラバラの◎しかいなければ
        戦の数だけ人が要るため。足せる相手がいなくなったら諦める。
        """
        while self.uncovered_first_half(arcanum):
            if not self.assign_one(arcanum, need_best_gain=True):
                return

    def _coverage(self, arcanum: Arcanum, names: list[str]) -> tuple:
        """その顔ぶれで塞げている穴の一覧.

        担当を1人外す前後でこれが変わらなければ、その人は居ても居なくても
        同じということなので落としてよい。

        確実な担当だけは有無ではなく人数で見る。「各戦2人」の奥義では
        2人目も塞いでいる穴のうちなので、有無で比べると2人目が
        「居なくても同じ」と判定されて落ちてしまう。必要な人数まで数えたら
        あとは何人いても同じなので、そこで頭打ちにする。
        """
        best: set[int] = set()
        sure = [0] * BATTLE_COUNT
        joins: set[int] = set()
        vanguard: set[int] = set()
        for name in names:
            who = self.member[name]
            best |= who.best_battles()
            for battle in range(BATTLE_COUNT):
                if who.is_sure(battle):
                    sure[battle] += 1
                if who.joins(battle):
                    joins.add(battle)
                    if who.is_vanguard(battle):
                        vanguard.add(battle)
        quota = sure_quota(arcanum)
        return (
            frozenset(best) if arcanum.first_half else frozenset(),
            frozenset(vanguard) if arcanum.for_vanguard else frozenset(),
            tuple(min(count, quota) for count in sure),
            frozenset(joins),
        )

    def _redundant_in(self, arcanum: Arcanum, name: str) -> bool:
        """その奥義から name を外しても、塞げている穴が減らないかどうか."""
        picked = self.picked[arcanum.name]
        rest = [n for n in picked if n != name]
        return self._coverage(arcanum, rest) == self._coverage(arcanum, picked)

    def _move(self, name: str, source: Arcanum, dest: Arcanum) -> None:
        """担当を1人、奥義から奥義へ移す."""
        self.picked[source.name].remove(name)
        self.load[name].remove(source.name)
        self.picked[dest.name].append(name)
        self.load[name].append(dest.name)

    def _refill(self, arcanum: Arcanum, before: tuple) -> None:
        """引き抜いたぶんの穴を、元と同じだけ塞げるまで埋め直す.

        通常の段階と同じ順で埋める。塞ぎきれなければ呼び出し側が取り消す。
        """
        quota = sure_quota(arcanum)
        while self._coverage(arcanum, self.picked[arcanum.name]) != before:
            if not self.assign_one(arcanum, need_sure_gain=True, sure_needed=quota):
                break
        if arcanum.first_half:
            self.cover_first_half(arcanum)
        if arcanum.for_vanguard:
            self.cover_vanguard(arcanum)
        while self._coverage(arcanum, self.picked[arcanum.name]) != before:
            if not self.assign_one(arcanum, need_join_gain=True):
                break
        while len(self.picked[arcanum.name]) < arcanum.required:
            if not self.assign_one(arcanum):
                break

    def _rollback(
        self, arcanum: Arcanum, snapshot: list[str], name: str, target: Arcanum
    ) -> None:
        """引き抜きを取り消して、手を付ける前の顔ぶれに戻す."""
        for added in [n for n in self.picked[arcanum.name] if n not in snapshot]:
            self.load[added].remove(arcanum.name)
        self.picked[arcanum.name] = list(snapshot)
        self.picked[target.name].remove(name)
        self.load[name].remove(target.name)
        # 埋め直しが本人を呼び戻していれば、担当はもう戻っている。
        if arcanum.name not in self.load[name]:
            self.load[name].append(arcanum.name)

    def _swap_in(self, target: Arcanum, battle: int, sure: bool = True) -> bool:
        """その戦を埋められる人を、他の奥義から引き抜いて移す.

        sure=True は確実に出せる担当(◎〇)だけを探す。sure=False は△も含めて
        「その戦に出られる人」を探す — 誰も出られない戦(×)を埋めるとき用。

        引き抜けるのは、抜けた跡を埋め直せる相手だけ。塞げていた穴も必要人数も
        引き抜く前と同じところまで戻せなければ、その手は使わず次の候補を試す。
        穴を別の奥義に移し替えるだけでは何も良くならない。

        余分な担当(外しても穴が減らない人)から先に試す。埋め直しが要らない
        ぶん、動く人数が少なくて済む。それが尽きたら、埋め直しの効く相手を
        試す — 「中国 = tetsu(◎◎×) + OMEGA(△◎◎)」のように2戦目では
        余っていても1戦目のカバーを担っている担当は、1戦目を確実に出られる
        空き枠の人と入れ替えれば引き抜ける。
        """
        held = set(self.picked[target.name])
        moves: list[tuple[str, Arcanum]] = []
        for name, who in self.member.items():
            if name in held:
                continue
            if not (who.is_sure(battle) if sure else who.joins(battle)):
                continue
            if not self.is_eligible(target, name):
                continue
            for other_name in self.load[name]:
                other = self.arcanum[other_name]
                # 「瞬時(何度も)」はこの段階ではまだ配っていない。念のため除く。
                if other_name == target.name or other.fills_leftover:
                    continue
                moves.append((name, other))
        self.rng.shuffle(moves)
        moves.sort(key=lambda move: not self._redundant_in(move[1], move[0]))

        for name, other in moves:
            snapshot = list(self.picked[other.name])
            before = self._coverage(other, snapshot)
            # 元から必要人数に足りていない奥義は、そこまで戻せれば十分。
            keep = min(other.required, len(snapshot))
            self._move(name, other, target)
            self._refill(other, before)
            picked = self.picked[other.name]
            if self._coverage(other, picked) == before and len(picked) >= keep:
                return True
            self._rollback(other, snapshot, name, target)
        return False

    def cover_by_swap(self, arcanum: Arcanum) -> None:
        """確実な担当(◎〇)が足りない戦を、他の奥義からの引き抜きで埋める.

        段階4までは空き枠のある人からしか担当を足せない。引き継ぎで全員の枠が
        埋まっていると、前回の顔ぶれがたまたま同じ戦に強い2人組だった奥義に
        余分な担当が居座り、その戦を埋められない奥義が△頼みのまま残る。
        実測(連合20人×4枠、必須26奥義)では、2戦目を確実に出られる8人の32枠の
        うち4枠がこの重複で死んでいた。ここで余分を引き抜いて回す。

        引き抜いた跡は埋め直せるときだけ動かすので、向こうの穴と引き換えに
        することはない。「各戦2人」の奥義は2人目まで引き抜いてよい
        (印の優先順位どおり)。

        この段階は必ず段階5.5(役目の終わった担当を落とす)より後に置く。
        段階2〜4が穴埋めのために増やした担当が残ったままだと枠が塞がっていて、
        引き抜いた跡に入れる代わりがいない。実データでは、先に落とすかどうかで
        11枠ぶんの差が出て、落としてからでないと羊の2戦目が埋まらなかった。
        """
        needed = sure_quota(arcanum)
        while self.unsure_battles(arcanum, needed):
            # 段階5.5で空いた枠があるなら、引き抜くまでもなく足せる。
            if self.assign_one(arcanum, need_sure_gain=True, sure_needed=needed):
                continue
            if not self._swap_any(arcanum, self.unsure_battles(arcanum, needed)):
                break

        # 確実な担当を回せなかった戦は、△でもいいから誰か回す。
        # 段階4は空き枠のある人からしか足せないので、引き継ぎで△の担当が
        # 「他の人で足りている奥義」に居座っていると、その人しか出られない戦が
        # ×のまま残る。実データでは、1戦目に出られる唯一の△が、確実な担当の
        # いる奥義に付いていたせいで猿の1戦目が×になっていた。
        while self.thin_battles(arcanum):
            if self.assign_one(arcanum, need_join_gain=True):
                continue
            if not self._swap_any(arcanum, self.thin_battles(arcanum), sure=False):
                return

    def _swap_any(
        self, arcanum: Arcanum, battles: set[int], sure: bool = True
    ) -> bool:
        """穴の空いた戦のどれか1つを引き抜きで埋める. 1人移せたら True.

        1人移すと複数の戦が同時に埋まることがあるので、呼び出し側で数え直す。
        """
        return any(self._swap_in(arcanum, battle, sure) for battle in sorted(battles))

    def _droppable(self, arcanum: Arcanum) -> str | None:
        """外しても塞げている穴が減らない担当のうち、いちばん外してよい人.

        まずこの回に足した人から外す。前回からいる人を先に落とすと、
        引き継ぎで担当を据え置いた意味がなくなる。
        そのうえで担当数の多い人から外す。枠が空けば他の奥義や余り埋めに回せる。
        """
        picked = self.picked[arcanum.name]
        candidates = [name for name in picked if self._redundant_in(arcanum, name)]
        if not candidates:
            return None
        carried = set(self.source.get(arcanum.name, ()))
        return max(
            candidates,
            key=lambda n: (
                n not in carried,
                len(self.load[n]),
                self.member[n].priority_score(),
            ),
        )

    def trim_excess(self, arcanum: Arcanum) -> None:
        """必要人数を超えた担当のうち、居なくても穴が空かない人を外す.

        段階2〜4は穴を塞ぐために必要人数を超えて足す。翌日その穴が別の担当で
        塞がるようになっても、足した人は残り続ける。引き継ぎを重ねると
        この「役目の終わった担当」が溜まり、必要2人のはずの奥義が3人4人に
        膨らんでいく。ここで落とす。
        """
        while len(self.picked[arcanum.name]) > arcanum.required:
            name = self._droppable(arcanum)
            if name is None:
                return
            self.picked[arcanum.name].remove(name)
            self.load[name].remove(arcanum.name)
            # 前回から引き継いだ人を落としたときだけ、外した扱いに直す。
            # この回に足して落とした人は、そもそも引き継ぎ元にいない。
            label = f"{arcanum.name}: {name}"
            if self.report is not None and label in self.report.kept:
                self.report.kept.remove(label)
                self.report.dropped.append(f"{label}(居なくても穴が空かない)")

    def fill_leftover(self) -> None:
        """余り枠を「瞬時(何度も)」系で埋める.

        この段階は×や－の人にも配る。1人が同じ奥義を2回持つことはできないので、
        奥義1つあたりの上限は軍師を除いた全員の人数。
        """
        fill_arcana = self.roster.fill_arcana()
        if not fill_arcana:
            return
        everyone = list(self.load)

        while True:
            free = [
                n for n in everyone
                if len(self.load[n]) < self.roster.slots_per_member
            ]
            if not free:
                return

            # 担当人数の少ない奥義から上乗せして、奥義間で偏らないようにする。
            self.rng.shuffle(fill_arcana)
            fill_arcana.sort(key=lambda a: len(self.picked[a.name]))

            for arcanum in fill_arcana:
                held = set(self.picked[arcanum.name])
                candidates = [
                    n
                    for n in free
                    if n not in held and self.is_eligible(arcanum, n)
                ]
                if not candidates:
                    continue
                self.rng.shuffle(candidates)
                # 担当数の少ない人を優先。同数なら参戦確度の高い人から。
                chosen = min(
                    candidates,
                    key=lambda n: (len(self.load[n]), self.member[n].priority_score()),
                )
                self.load[chosen].append(arcanum.name)
                self.picked[arcanum.name].append(chosen)
                break
            else:
                # どの奥義にも上乗せできる相手がいない。
                return


def allocate(
    roster: Roster,
    seed: int | None = None,
    carryover: dict[str, list[str]] | None = None,
) -> AllocationResult:
    """メンバーへ奥義を割り振る.

    奥義は1日通して変えない前提なので、割り振りは1回だけ行い、その結果を
    3戦すべてで使う。参戦状況だけが戦ごとに変わる。

    carryover(奥義名 -> 担当者名)を渡すと、前回の割り当てを起点にして
    足りないところだけ埋め直す。複数日にまたがる割り振りで、変える必要の
    ない人の担当をそのまま残すための入口。段階0と5.5が増えるだけで、
    段階1〜6の中身は変わらない — どの段階も「足りるまで足す」ループなので、
    埋まっている前提でそのまま動く。

    9段階で配る。「瞬時(何度も)」は必ず入れる奥義ではないので、
    段階1〜5.5では一切枠を取らず、段階7の余り埋めでだけ配る。

    0. (引き継ぎ時のみ)前回ぶんのうち、今回も有効な担当を取り込む。
       参戦しなくなった人、△だけに落ちて代わりがいる人はここで外す(剪定)。
    1. 「瞬時(何度も)」以外の全奥義に1人ずつ確保する(カバレッジ優先)。
       前半必須・前衛向けの奥義を先に処理する。
       担当は◎〇から選び、足りなければ△も使う。
    1.5「各戦2人」の奥義に、どの戦も確実な担当(◎〇)が2人になるまで足す。
       全奥義に1人ずつ配った直後、他のどの穴埋めよりも先に取る。
    2. 前半必須の奥義に、各戦の◎が1人以上入るまで担当を足す。
    3. 前衛向けの奥義に、各戦の前衛担当が1人以上入るまで担当を足す。
    4. どの奥義も、各戦に出られる担当が1人以上いるまで足す。
    5. 余裕のある奥義から必要人数(既定2人)まで増やす。ここで人手が尽きた奥義は
       1人担当のままになる — 足りない分だけ2人体制を撤回する。
    5.5 必要人数を超えた担当のうち、居なくても塞げている穴が減らない人を外す。
       「各戦2人」の奥義では、2人目も塞いでいる穴のうちなので落とさない。
    5.7 それでも確実な担当(◎〇)がいない戦は、他の奥義から担当を引き抜いて回す。
       枠が全部埋まっていても動かせる、最後の手段。段階5.5より後に置く —
       引き抜いた跡を代わりで埋めるには空き枠が要るため。
    5.9 引き抜きで必要人数を超えたぶんを、もう一度落とす。
    6. (引き継ぎ時のみ)前回ぶんの「瞬時(何度も)」を、枠が残っていれば戻す。
    7. 残った枠を「瞬時(何度も)」系の奥義で埋める。この段階だけは×や－の人にも配る。

    段階1.5〜4と5.2は必要人数を超えることがある。2人いても両方が同じ戦を欠けば
    その戦は穴になるので、人数を揃えるより穴を塞ぐほうを先に済ませる。
    ただし塞ぐ役目を別の担当が引き受けたら、超えた分は段階5.5で落とす。
    落とさないと、引き継ぎを重ねるたびに担当が溜まって膨らんでいく。

    seed を指定すると割り振りが再現可能になる。
    """
    errors = validate(roster)
    if errors:
        raise AllocationError(errors)

    work = _Allocator(roster, random.Random(seed), carryover)

    work.seed_carryover(fill=False)  # 段階0

    # 必ず確保する奥義だけを、制約のきついものから順に処理する。
    ordered = sorted(roster.required_arcana(), key=_constraint_order)

    for arcanum in ordered:  # 段階1
        # 引き継ぎで既に担当がいる奥義は飛ばす。ここで無条件に足すと、
        # 前回と同じ入力でも毎回1人ずつ増えてしまう。
        if not work.picked[arcanum.name]:
            work.assign_one(arcanum)

    for arcanum in ordered:  # 段階1.5
        if arcanum.two_per_battle:
            work.cover_battles_twice(arcanum)

    for arcanum in ordered:  # 段階2
        if arcanum.first_half:
            work.cover_first_half(arcanum)

    for arcanum in ordered:  # 段階3
        if arcanum.for_vanguard:
            work.cover_vanguard(arcanum)

    for arcanum in ordered:  # 段階4
        work.cover_battles(arcanum)

    for arcanum in ordered:  # 段階5
        while len(work.picked[arcanum.name]) < arcanum.required:
            if not work.assign_one(arcanum):
                break

    for arcanum in ordered:  # 段階5.5
        work.trim_excess(arcanum)

    for arcanum in ordered:  # 段階5.7
        work.cover_by_swap(arcanum)

    # 引き抜きで必要人数を超えたぶんを落とし直す。
    for arcanum in ordered:  # 段階5.9
        work.trim_excess(arcanum)

    work.seed_carryover(fill=True)  # 段階6
    work.fill_leftover()  # 段階7
    work.record_changes()

    # 画面に並んでいる順で結果を返す。
    assignments = [
        _build_assignment(a, work.picked[a.name], work.member) for a in roster.arcana
    ]

    result = AllocationResult(
        assignments=assignments,
        load=work.load,
        demand=roster.demand(),
        capacity=roster.capacity(),
        fill_capacity=roster.fill_capacity(),
        attendance={name: list(m.attendance) for name, m in work.member.items()},
        vanguard={name: list(m.vanguard) for name, m in work.member.items()},
        carryover=work.report,
    )
    result.warnings, result.notes = _build_warnings(roster, result)
    return result


def _build_assignment(
    arcanum: Arcanum, picked: list[str], member: dict[str, Member]
) -> Assignment:
    """戦ごとの出席人数と◎の有無を数えて Assignment を組み立てる."""
    per_battle = [0] * BATTLE_COUNT
    sure_per_battle = [0] * BATTLE_COUNT
    best = [False] * BATTLE_COUNT
    vanguard = [False] * BATTLE_COUNT
    for name in picked:
        who = member[name]
        for battle in range(BATTLE_COUNT):
            if who.joins(battle):
                per_battle[battle] += 1
                if who.is_vanguard(battle):
                    vanguard[battle] = True
            if who.is_sure(battle):
                sure_per_battle[battle] += 1
        for battle in who.best_battles():
            best[battle] = True
    return Assignment(
        arcanum=arcanum.name,
        # 「瞬時(何度も)」は0。何人入っても不足扱いにしない。
        required=arcanum.effective_required,
        members=picked,
        category=arcanum.category,
        first_half=arcanum.first_half,
        for_vanguard=arcanum.for_vanguard,
        # 「瞬時(何度も)」は必要人数を確保しない種類なので、印が付いていても効かない。
        two_per_battle=arcanum.two_per_battle and not arcanum.fills_leftover,
        vanguard_battles=vanguard,
        per_battle=per_battle,
        sure_per_battle=sure_per_battle,
        best_battles=best,
    )


def _build_warnings(
    roster: Roster, result: AllocationResult
) -> tuple[list[str], list[str]]:
    """(warnings, notes) を返す.

    warnings は人手が足りない・△頼みといった困りごと。結果の末尾に出して
    連合に共有する。notes は引き継ぎの内訳や空き枠などの作業用の情報で、
    チャットに貼っても読み手には関係がないので出さない。
    """
    warnings: list[str] = []

    # 「瞬時(何度も)」は余り枠に入れるだけなので、0人でも不足ではない。
    required_names = {a.name for a in roster.required_arcana()}
    mandatory = [a for a in result.assignments if a.arcanum in required_names]

    uncovered = [a.arcanum for a in mandatory if a.is_uncovered]
    if uncovered:
        warnings.append(
            "担当を付けられなかった奥義があります: " + "、".join(uncovered)
        )

    reduced = [a for a in result.reduced_arcana if not a.is_uncovered]
    if reduced:
        detail = "、".join(f"{a.arcanum}({len(a.members)}/{a.required}人)" for a in reduced)
        warnings.append(
            f"人手が足りず、{len(reduced)}件の奥義が必要人数に届きませんでした: {detail}"
        )

    uncovered_first_half = [
        f"{a.arcanum}({'、'.join(BATTLE_LABELS[b] for b in a.uncovered_first_half)})"
        for a in result.assignments
        if a.members and a.uncovered_first_half
    ]
    if uncovered_first_half:
        warnings.append(
            "前半必須なのに◎の担当がいない戦があります: "
            + "、".join(uncovered_first_half)
        )

    thin = [
        f"{a.arcanum}({'、'.join(BATTLE_LABELS[b] for b in a.thin_battles)})"
        for a in mandatory
        if a.members and a.thin_battles
    ]
    if thin:
        warnings.append(
            "担当者が誰も出られない戦がある奥義: " + "、".join(thin)
        )

    if any(a.for_vanguard for a in roster.arcana):
        available = [m.name for m in roster.fill_members() if m.can_be_vanguard()]
        short_vanguard = [
            f"{a.arcanum}({len(a.members)}/{a.required}人)"
            for a in mandatory
            if a.for_vanguard and a.is_short
        ]
        if short_vanguard:
            warnings.append(
                f"前衛として出られる人が{len(available)}人しかおらず、"
                "前衛向け奥義の担当が必要人数に届きませんでした: "
                + "、".join(short_vanguard)
            )

    no_vanguard = [
        f"{a.arcanum}({'、'.join(BATTLE_LABELS[b] for b in a.uncovered_vanguard)})"
        for a in mandatory
        if a.members and a.uncovered_vanguard
    ]
    if no_vanguard:
        warnings.append(
            "前衛向けなのに前衛の担当がいない戦があります: " + "、".join(no_vanguard)
        )

    unsure = [
        f"{a.arcanum}({'、'.join(BATTLE_LABELS[b] for b in a.unsure_battles)})"
        for a in mandatory
        if a.members and a.unsure_battles
    ]
    if unsure:
        warnings.append(
            "確実に出せる担当(◎〇)がおらず、△頼みになっている戦があります: "
            + "、".join(unsure)
        )

    short_pair = [
        f"{a.arcanum}({'、'.join(BATTLE_LABELS[b] for b in a.short_pair_battles)})"
        for a in mandatory
        if a.short_pair_battles
    ]
    if short_pair:
        warnings.append(
            f"各戦{PAIR_PER_BATTLE}人にしたい奥義で、確実に出せる担当が1人しか"
            "いない戦があります: " + "、".join(short_pair)
        )

    # ここから下は「困りごと」ではなく内訳。結果の末尾には出さない。
    notes: list[str] = []

    idle = result.idle_members()
    if idle:
        notes.append(
            f"奥義を担当しないメンバーが{len(idle)}人います: " + "、".join(idle)
        )

    spare = result.spare_slots
    if spare > 0 and not roster.fill_arcana():
        notes.append(
            f"{spare}枠が空いています。"
            "「瞬時(何度も)」の奥義を足すと、余った枠をそこで埋められます。"
        )

    if result.carryover is not None:
        notes.append("【引き継ぎ】" + result.carryover.summary())
        if result.carryover.dropped:
            notes.append(
                "引き継げなかった担当: " + "、".join(result.carryover.dropped)
            )
        # 役目の終わった担当は段階5.5で落とすが、それでも前回の顔ぶれを起点に
        # している以上、ゼロから組んだほうがうまく収まることはある。
        if reduced or uncovered:
            notes.append(
                "前回の担当を起点にしているぶん、枠が足りないと他の奥義に"
                "しわ寄せが出ることがあります。不足が気になる場合は"
                "「割り振る」でゼロから組み直してください。"
            )

    return warnings, notes
