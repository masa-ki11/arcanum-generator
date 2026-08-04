"""奥義を参戦メンバーへ割り振るロジック."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .models import (
    ATTENDANCE_LEVELS,
    BATTLE_COUNT,
    BATTLE_LABELS,
    CATEGORY_NAMES,
    Arcanum,
    Member,
    Roster,
)


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

    @property
    def source_total(self) -> int:
        """引き継ぎ元にあった担当の件数."""
        return len(self.kept) + len(self.dropped)

    def summary(self) -> str:
        return (
            f"前回の担当{self.source_total}件のうち{len(self.kept)}件をそのまま維持、"
            f"{len(self.dropped)}件を外し、{len(self.added)}件を新しく足しました。"
        )


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

        「今回は参戦しない」が剪定の本体。◎だった人が×になっても担当が残ると、
        枠を食い潰したうえに実際は誰も出ない戦ができてしまう。
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
        if not arcanum.fills_leftover and not who.is_assignable():
            return "今回は参戦しない"
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
        (前半必須・前衛向け)を先に確保しておく。
        """
        return sorted(
            (a for a in self.roster.arcana if a.fills_leftover == fill),
            key=lambda a: (not a.first_half, not a.for_vanguard, -a.required, a.name),
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

    def record_added(self) -> None:
        """引き継ぎ後に足された担当を洗い出す."""
        if self.report is None:
            return
        for arcanum in self.roster.arcana:
            before = set(self.source.get(arcanum.name, []))
            for name in self.picked[arcanum.name]:
                if name not in before:
                    self.report.added.append(f"{arcanum.name}: {name}")

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

    def unsure_battles(self, arcanum: Arcanum) -> set[int]:
        """確実に出る担当(◎〇)がいない戦.

        △は来ないことがあるので、埋まったとはみなさない。
        """
        covered = {
            battle
            for name in self.picked[arcanum.name]
            for battle in range(BATTLE_COUNT)
            if self.member[name].is_sure(battle)
        }
        return set(range(BATTLE_COUNT)) - covered

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
        unsure = self.unsure_battles(arcanum)
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

    def cover_first_half(self, arcanum: Arcanum) -> None:
        """前半必須の奥義に、各戦の◎が1人以上入るまで担当を足す.

        必要人数(既定2人)を超えることがある。各戦バラバラの◎しかいなければ
        戦の数だけ人が要るため。足せる相手がいなくなったら諦める。
        """
        while self.uncovered_first_half(arcanum):
            if not self.assign_one(arcanum, need_best_gain=True):
                return

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

    6段階で配る。「瞬時(何度も)」は必ず入れる奥義ではないので、
    段階1〜5では一切枠を取らず、段階6の余り埋めでだけ配る。

    0. (引き継ぎ時のみ)前回ぶんのうち、今回も有効な担当を取り込む。
       参戦しなくなった人などはここで外す(剪定)。
    1. 「瞬時(何度も)」以外の全奥義に1人ずつ確保する(カバレッジ優先)。
       前半必須・前衛向けの奥義を先に処理する。
       担当は◎〇から選び、足りなければ△も使う。
    2. 前半必須の奥義に、各戦の◎が1人以上入るまで担当を足す。
    3. 前衛向けの奥義に、各戦の前衛担当が1人以上入るまで担当を足す。
    4. どの奥義も、各戦に出られる担当が1人以上いるまで足す。
    5. 余裕のある奥義から必要人数(既定2人)まで増やす。ここで人手が尽きた奥義は
       1人担当のままになる — 足りない分だけ2人体制を撤回する。
    5.5 (引き継ぎ時のみ)前回ぶんの「瞬時(何度も)」を、枠が残っていれば戻す。
    6. 残った枠を「瞬時(何度も)」系の奥義で埋める。この段階だけは×や－の人にも配る。

    段階2〜4は必要人数を超えることがある。2人いても両方が同じ戦を欠けば
    その戦は穴になるので、人数を揃えるより穴を塞ぐほうを先に済ませる。

    seed を指定すると割り振りが再現可能になる。
    """
    errors = validate(roster)
    if errors:
        raise AllocationError(errors)

    work = _Allocator(roster, random.Random(seed), carryover)

    work.seed_carryover(fill=False)  # 段階0

    # 必ず確保する奥義だけを、制約のきついものから順に処理する。
    ordered = sorted(
        roster.required_arcana(),
        key=lambda a: (not a.first_half, not a.for_vanguard, -a.required, a.name),
    )

    for arcanum in ordered:  # 段階1
        # 引き継ぎで既に担当がいる奥義は飛ばす。ここで無条件に足すと、
        # 前回と同じ入力でも毎回1人ずつ増えてしまう。
        if not work.picked[arcanum.name]:
            work.assign_one(arcanum)

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

    work.seed_carryover(fill=True)  # 段階5.5
    work.fill_leftover()  # 段階6
    work.record_added()

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
    result.warnings = _build_warnings(roster, result)
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
        vanguard_battles=vanguard,
        per_battle=per_battle,
        sure_per_battle=sure_per_battle,
        best_battles=best,
    )


def _build_warnings(roster: Roster, result: AllocationResult) -> list[str]:
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

    idle = result.idle_members()
    if idle:
        warnings.append(
            f"奥義を担当しないメンバーが{len(idle)}人います: " + "、".join(idle)
        )

    spare = result.spare_slots
    if spare > 0 and not roster.fill_arcana():
        warnings.append(
            f"{spare}枠が空いています。"
            "「瞬時(何度も)」の奥義を足すと、余った枠をそこで埋められます。"
        )

    if result.carryover is not None:
        warnings.append("【引き継ぎ】" + result.carryover.summary())
        if result.carryover.dropped:
            warnings.append(
                "引き継げなかった担当: " + "、".join(result.carryover.dropped)
            )
        # 引き継ぎは前回ぶんを減らさないので、必要人数より多い担当も残る。
        # 枠が窮屈なときは、それが他の奥義の不足として出てくることがある。
        if reduced or uncovered:
            warnings.append(
                "引き継ぎでは前回より担当を減らさないため、枠が足りないと"
                "他の奥義にしわ寄せが出ます。不足が気になる場合は"
                "「割り振る」でゼロから組み直してください。"
            )

    return warnings
