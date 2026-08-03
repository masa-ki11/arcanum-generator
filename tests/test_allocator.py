"""割り振りロジックのテスト."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arcanum_generator.allocator import AllocationError, allocate, validate
from arcanum_generator.models import (
    ATTEND_BEST,
    ATTEND_MAYBE,
    ATTEND_NO,
    ATTEND_UNKNOWN,
    ATTEND_YES,
    BATTLE_COUNT,
    DEFAULT_SLOTS_PER_MEMBER,
    Arcanum,
    Member,
    Roster,
)
from arcanum_generator.storage import (
    AUTOSAVE_NAME,
    autosave_path,
    load_roster,
    project_dir,
    save_roster,
)

FILL = "瞬時(何度も)"
FIXED = "絆"


def arcana(*items) -> list[Arcanum]:
    """(名前, 必要人数[, 種類[, 前半必須]]) から奥義リストを作る."""
    built = []
    for item in items:
        name, required = item[0], item[1]
        category = item[2] if len(item) > 2 else FIXED
        first_half = item[3] if len(item) > 3 else False
        built.append(Arcanum(name, required, category, first_half))
    return built


def members(**by_level) -> list[Member]:
    """best=2, yes=3 のように参戦状況ごとの人数を渡してメンバーを作る.

    ここで作る人は3戦とも同じ参戦状況。戦ごとに変えたいテストは Member を直接使う。
    """
    levels = {
        "best": ATTEND_BEST,
        "yes": ATTEND_YES,
        "maybe": ATTEND_MAYBE,
        "no": ATTEND_NO,
        "unknown": ATTEND_UNKNOWN,
    }
    built = []
    for key, level in levels.items():
        for i in range(by_level.get(key, 0)):
            built.append(Member(f"{level}{i}", [level] * BATTLE_COUNT))
    for i in range(by_level.get("strategist", 0)):
        built.append(
            Member(f"軍師{i}", [ATTEND_BEST] * BATTLE_COUNT, is_strategist=True)
        )
    return built


class DefaultsTest(unittest.TestCase):
    def test_default_slots_per_member_is_four(self):
        self.assertEqual(DEFAULT_SLOTS_PER_MEMBER, 4)
        self.assertEqual(Roster().slots_per_member, 4)


class ValidateTest(unittest.TestCase):
    def test_healthy_roster_has_no_error(self):
        roster = Roster(arcana(("神楽", 2)), members(best=1, yes=2))
        self.assertEqual(validate(roster), [])

    def test_no_arcanum(self):
        roster = Roster([], members(yes=2))
        self.assertTrue(any("奥義が1つも" in e for e in validate(roster)))

    def test_no_assignable_member(self):
        # ×と－しかいない、あるいは全員軍師なら割り振れない。
        roster = Roster(arcana(("神楽", 2)), members(no=2, unknown=1))
        self.assertTrue(any("割り当てられるメンバーがいません" in e for e in validate(roster)))
        roster = Roster(arcana(("神楽", 2)), members(strategist=2))
        self.assertTrue(any("割り当てられるメンバーがいません" in e for e in validate(roster)))

    def test_capacity_shortage_is_not_an_error_anymore(self):
        # 人手が足りなくても止めない。2人体制を撤回して成立させる。
        roster = Roster(arcana(("A", 2), ("B", 2), ("C", 2)), members(yes=1), )
        roster.slots_per_member = 2
        self.assertEqual(validate(roster), [])

    def test_duplicate_names_are_rejected(self):
        roster = Roster(
            [Arcanum("神楽", 2), Arcanum("神楽", 2)],
            [Member("A", ATTEND_YES), Member("A", ATTEND_YES), Member("B", ATTEND_YES)],
        )
        errors = validate(roster)
        self.assertTrue(any("奥義名「神楽」が重複" in e for e in errors))
        self.assertTrue(any("メンバー名「A」が重複" in e for e in errors))

    def test_zero_slots(self):
        roster = Roster(arcana(("神楽", 2)), members(yes=2), slots_per_member=0)
        self.assertTrue(any("1以上" in e for e in validate(roster)))

    def test_unknown_category_is_rejected(self):
        roster = Roster(arcana(("謎", 2, "存在しない種類")), members(yes=3))
        self.assertTrue(any("種類" in e and "不明" in e for e in validate(roster)))

    def test_unknown_attendance_is_rejected(self):
        roster = Roster(arcana(("神楽", 2)), [Member("A", "参加")])
        self.assertTrue(any("参戦状況" in e and "不明" in e for e in validate(roster)))


class AttendanceTest(unittest.TestCase):
    def test_absent_and_unknown_never_get_fixed_arcana(self):
        roster = Roster(
            arcana(("絆技", 2, FIXED)), members(best=1, yes=1, no=3, unknown=3)
        )
        result = allocate(roster, seed=1)
        assigned = set(result.assignments[0].members)
        self.assertEqual(assigned, {"◎0", "〇0"})

    def test_strategist_is_never_assigned_anything(self):
        roster = Roster(
            arcana(("絆技", 2, FIXED), ("連打", 2, FILL)),
            members(best=2, yes=2, strategist=1),
        )
        result = allocate(roster, seed=2)
        self.assertNotIn("軍師0", result.load)
        for assignment in result.assignments:
            self.assertNotIn("軍師0", assignment.members)

    def test_maybe_is_kept_in_reserve_when_primary_is_enough(self):
        roster = Roster(
            arcana(("A", 2, FIXED), ("B", 2, FIXED)),
            members(best=2, yes=2, maybe=2),
        )
        result = allocate(roster, seed=3)
        used = {n for a in result.assignments for n in a.members}
        self.assertTrue(used.isdisjoint({"△0", "△1"}), used)

    def test_maybe_is_used_when_primary_runs_out(self):
        roster = Roster(
            arcana(("A", 2, FIXED), ("B", 2, FIXED)),
            members(best=1, maybe=3),
            slots_per_member=1,
        )
        result = allocate(roster, seed=4)
        used = {n for a in result.assignments for n in a.members}
        self.assertIn("◎0", used)
        self.assertTrue(used & {"△0", "△1", "△2"}, used)


class FirstHalfTest(unittest.TestCase):
    def test_first_half_arcanum_gets_the_best_attendance(self):
        roster = Roster(
            arcana(("後半でもよい", 1, FIXED), ("前半必須", 1, FIXED, True)),
            members(best=1, yes=3),
        )
        result = allocate(roster, seed=5)
        by_name = {a.arcanum: a for a in result.assignments}
        self.assertEqual(by_name["前半必須"].members, ["◎0"])
        self.assertNotIn("◎0", by_name["後半でもよい"].members)

    def test_first_half_takes_precedence_over_load_balance(self):
        # ◎が1人しかいなくても、前半必須2件は◎が押さえる。
        roster = Roster(
            arcana(("前半A", 1, FIXED, True), ("前半B", 1, FIXED, True)),
            members(best=1, yes=4),
        )
        result = allocate(roster, seed=6)
        for assignment in result.assignments:
            self.assertEqual(assignment.members, ["◎0"])

    def test_warns_when_first_half_has_no_best_member(self):
        roster = Roster(arcana(("前半A", 1, FIXED, True)), members(yes=3))
        result = allocate(roster, seed=7)
        self.assertTrue(
            any("前半必須なのに◎の担当がいない" in w for w in result.warnings)
        )

    def test_no_warning_when_best_member_covers_it(self):
        roster = Roster(arcana(("前半A", 1, FIXED, True)), members(best=1, yes=2))
        result = allocate(roster, seed=8)
        self.assertFalse(
            any("前半必須なのに◎の担当がいない" in w for w in result.warnings)
        )


class ThreeBattleTest(unittest.TestCase):
    def test_first_half_expands_to_cover_every_battle(self):
        # 各戦バラバラの◎しかいないので、必要人数2人を超えて3人になる。
        roster = Roster(
            arcana(("前半技", 2, FIXED, True)),
            [
                Member("初戦", [ATTEND_BEST, ATTEND_NO, ATTEND_NO]),
                Member("2戦", [ATTEND_NO, ATTEND_BEST, ATTEND_NO]),
                Member("3戦", [ATTEND_NO, ATTEND_NO, ATTEND_BEST]),
                Member("通し", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=1)
        assignment = result.assignments[0]
        self.assertEqual(sorted(assignment.members), ["2戦", "3戦", "初戦"])
        self.assertEqual(assignment.best_battles, [True] * BATTLE_COUNT)
        self.assertEqual(assignment.uncovered_first_half, [])

    def test_first_half_stops_at_required_when_one_person_covers_all(self):
        # 全戦◎が1人いれば◎カバーは1人で足りる。あとは必要人数ぶんだけ。
        roster = Roster(
            arcana(("前半技", 2, FIXED, True)),
            [
                Member("通し◎", [ATTEND_BEST] * BATTLE_COUNT),
                Member("普通", [ATTEND_YES] * BATTLE_COUNT),
                Member("予備", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=2)
        assignment = result.assignments[0]
        self.assertEqual(len(assignment.members), 2)
        self.assertIn("通し◎", assignment.members)
        self.assertEqual(assignment.uncovered_first_half, [])

    def test_normal_arcanum_expands_to_cover_every_battle(self):
        # 通常の奥義も、各戦に出られる担当が1人以上になるまで足す。
        # 必要人数2人を超えて3人になる。
        roster = Roster(
            arcana(("普通技", 2, FIXED)),
            [
                Member("初戦", [ATTEND_BEST, ATTEND_NO, ATTEND_NO]),
                Member("2戦", [ATTEND_NO, ATTEND_BEST, ATTEND_NO]),
                Member("3戦", [ATTEND_NO, ATTEND_NO, ATTEND_BEST]),
            ],
        )
        result = allocate(roster, seed=3)
        assignment = result.assignments[0]
        self.assertEqual(sorted(assignment.members), ["2戦", "3戦", "初戦"])
        self.assertEqual(assignment.per_battle, [1, 1, 1])
        self.assertEqual(assignment.thin_battles, [])

    def test_maybe_attendance_does_not_count_as_covered(self):
        # △は来ないことがあるので、その戦が埋まったとはみなさない。
        # ◎〇で埋められる人がいるなら足す。
        roster = Roster(
            arcana(("技", 2, FIXED)),
            [
                Member("Aさん", [ATTEND_NO, ATTEND_MAYBE, ATTEND_BEST]),
                Member("Bさん", [ATTEND_BEST, ATTEND_NO, ATTEND_YES]),
                Member("2戦確実", [ATTEND_NO, ATTEND_YES, ATTEND_NO]),
                Member("無関係", [ATTEND_NO, ATTEND_NO, ATTEND_YES]),
            ],
        )
        result = allocate(roster, seed=40)
        assignment = result.assignments[0]
        self.assertIn("2戦確実", assignment.members)
        self.assertEqual(assignment.unsure_battles, [])
        self.assertTrue(all(c >= 1 for c in assignment.sure_per_battle))

    def test_unsure_battle_is_reported_when_only_maybe_can_go(self):
        # 2戦目に◎〇で出られる人がそもそもいない。△で埋めるしかない。
        roster = Roster(
            arcana(("技", 2, FIXED)),
            [
                Member("Aさん", [ATTEND_BEST, ATTEND_MAYBE, ATTEND_BEST]),
                Member("Bさん", [ATTEND_YES, ATTEND_NO, ATTEND_YES]),
            ],
        )
        result = allocate(roster, seed=41)
        assignment = result.assignments[0]
        self.assertEqual(assignment.unsure_battles, [1])
        self.assertEqual(assignment.thin_battles, [])
        self.assertEqual(assignment.battle_marks(), ["2", "△", "2"])
        self.assertTrue(any("△頼み" in w for w in result.warnings))

    def test_battle_marks_show_x_when_nobody_can_go(self):
        roster = Roster(
            arcana(("技", 1, FIXED)),
            [Member("Aさん", [ATTEND_YES, ATTEND_NO, ATTEND_NO])],
        )
        result = allocate(roster, seed=42)
        self.assertEqual(result.assignments[0].battle_marks(), ["1", "×", "×"])

    def test_coverage_stops_when_two_people_already_cover_all(self):
        # 2人で3戦とも埋まるなら3人目は要らない。
        roster = Roster(
            arcana(("普通技", 2, FIXED)),
            [
                Member("前半", [ATTEND_YES, ATTEND_YES, ATTEND_NO]),
                Member("後半", [ATTEND_NO, ATTEND_YES, ATTEND_YES]),
                Member("予備", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=9)
        assignment = result.assignments[0]
        self.assertEqual(len(assignment.members), 2)
        self.assertEqual(assignment.thin_battles, [])

    def test_thin_battle_is_reported_when_nobody_can_cover_it(self):
        # 3戦目に出られる人がそもそもいないので塞げない。警告で知らせる。
        roster = Roster(
            arcana(("普通技", 2, FIXED)),
            [
                Member("初戦", [ATTEND_BEST, ATTEND_NO, ATTEND_NO]),
                Member("2戦", [ATTEND_NO, ATTEND_BEST, ATTEND_NO]),
            ],
        )
        result = allocate(roster, seed=4)
        self.assertEqual(result.assignments[0].per_battle, [1, 1, 0])
        self.assertEqual(result.assignments[0].thin_battles, [2])
        self.assertTrue(any("誰も出られない戦" in w for w in result.warnings))

    def test_coverage_does_not_exceed_slot_limit(self):
        roster = Roster(
            arcana(("A", 2, FIXED), ("B", 2, FIXED)),
            [
                Member("初戦", [ATTEND_YES, ATTEND_NO, ATTEND_NO]),
                Member("2戦", [ATTEND_NO, ATTEND_YES, ATTEND_NO]),
                Member("3戦", [ATTEND_NO, ATTEND_NO, ATTEND_YES]),
            ],
            slots_per_member=1,
        )
        result = allocate(roster, seed=10)
        for held in result.load.values():
            self.assertLessEqual(len(held), 1)

    def test_uncovered_first_half_battle_is_reported(self):
        roster = Roster(
            arcana(("前半技", 2, FIXED, True)),
            [
                Member("A", [ATTEND_BEST, ATTEND_BEST, ATTEND_YES]),
                Member("B", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=5)
        self.assertEqual(result.assignments[0].uncovered_first_half, [2])
        self.assertTrue(
            any("前半必須なのに◎の担当がいない戦" in w for w in result.warnings)
        )

    def test_member_joining_only_one_battle_is_still_assignable(self):
        roster = Roster(
            arcana(("技", 1, FIXED)),
            [Member("たまに", [ATTEND_NO, ATTEND_NO, ATTEND_YES])],
        )
        self.assertEqual(validate(roster), [])
        result = allocate(roster, seed=6)
        self.assertEqual(result.assignments[0].members, ["たまに"])

    def test_member_absent_in_all_battles_is_excluded_from_fixed(self):
        roster = Roster(
            arcana(("技", 1, FIXED)),
            [
                Member("出る", [ATTEND_NO, ATTEND_YES, ATTEND_NO]),
                Member("全部×", [ATTEND_NO] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=7)
        self.assertEqual(result.assignments[0].members, ["出る"])

    def test_per_battle_counts_ignore_absent_holders(self):
        roster = Roster(
            arcana(("連打", 1, FILL)),
            [
                Member("出る", [ATTEND_YES] * BATTLE_COUNT),
                Member("全部×", [ATTEND_NO] * BATTLE_COUNT),
            ],
            slots_per_member=1,
        )
        result = allocate(roster, seed=8)
        assignment = result.assignments[0]
        # 余り埋めで×の人も担当に入るが、出られる人数には数えない。
        self.assertEqual(sorted(assignment.members), ["全部×", "出る"])
        self.assertEqual(assignment.per_battle, [1, 1, 1])


class ShortageTest(unittest.TestCase):
    def test_every_arcanum_is_covered_before_anyone_gets_a_second(self):
        # 5奥義 x 必要2人 = 10枠ほしいが、2人 x 4枠 = 8枠しかない。
        roster = Roster(
            arcana(*[(f"奥義{i}", 2, FIXED) for i in range(5)]),
            members(best=1, yes=1),
            slots_per_member=4,
        )
        result = allocate(roster, seed=9)
        for assignment in result.assignments:
            self.assertGreaterEqual(len(assignment.members), 1, assignment.arcanum)
        self.assertEqual(len(result.reduced_arcana), 2)
        self.assertEqual(result.used_slots, 8)

    def test_shortage_produces_a_warning(self):
        roster = Roster(
            arcana(("A", 2, FIXED), ("B", 2, FIXED), ("C", 2, FIXED)),
            members(yes=1),
            slots_per_member=4,
        )
        result = allocate(roster, seed=10)
        self.assertTrue(any("必要人数に届きませんでした" in w for w in result.warnings))
        # 1人しかいないので、どの奥義も1人担当まで。
        for assignment in result.assignments:
            self.assertEqual(len(assignment.members), 1)

    def test_uncovered_arcanum_is_reported(self):
        # 5奥義あるのに 1人 x 1枠 = 1枠しかない。
        roster = Roster(
            arcana(*[(f"奥義{i}", 2, FIXED) for i in range(5)]),
            members(yes=1),
            slots_per_member=1,
        )
        result = allocate(roster, seed=11)
        uncovered = [a for a in result.assignments if a.is_uncovered]
        self.assertEqual(len(uncovered), 4)
        self.assertTrue(any("担当を付けられなかった" in w for w in result.warnings))


class AllocateTest(unittest.TestCase):
    def test_each_arcanum_gets_required_members(self):
        roster = Roster(
            arcana(("神楽", 2), ("鬨の声", 2), ("采配", 2)), members(best=2, yes=3)
        )
        result = allocate(roster, seed=12)
        for assignment in result.assignments:
            self.assertEqual(len(assignment.members), assignment.required)
            self.assertFalse(assignment.is_short)

    def test_no_duplicate_member_within_one_arcanum(self):
        roster = Roster(arcana(("神楽", 3)), members(best=2, yes=2))
        result = allocate(roster, seed=13)
        picked = result.assignments[0].members
        self.assertEqual(len(picked), len(set(picked)))

    def test_slots_per_member_is_respected(self):
        roster = Roster(
            arcana(("A", 2), ("B", 2), ("C", 2)), members(yes=6), slots_per_member=1
        )
        result = allocate(roster, seed=14)
        for held in result.load.values():
            self.assertLessEqual(len(held), 1)

    def test_load_is_balanced(self):
        roster = Roster(
            arcana(("A", 2), ("B", 2), ("C", 2), ("D", 2)),
            members(yes=5),
            slots_per_member=3,
        )
        result = allocate(roster, seed=15)
        counts = [len(v) for v in result.load.values()]
        self.assertLessEqual(max(counts) - min(counts), 1)

    def test_assignment_order_follows_input_order(self):
        roster = Roster(arcana(("小技", 1), ("大技", 4)), members(yes=4))
        result = allocate(roster, seed=16)
        self.assertEqual([a.arcanum for a in result.assignments], ["小技", "大技"])

    def test_same_seed_reproduces_result(self):
        roster = Roster(arcana(("A", 2), ("B", 2)), members(best=3, yes=3))
        first = allocate(roster, seed=42)
        second = allocate(roster, seed=42)
        self.assertEqual(
            [a.members for a in first.assignments],
            [a.members for a in second.assignments],
        )

    def test_same_pair_does_not_repeat_systematically(self):
        # タイブレーク順が固定だと同じ2人組が複数の奥義に入り続けてしまう。
        roster = Roster(
            arcana(("結束", 3), *[(n, 2) for n in ["天下", "神楽", "采配", "鬨"]]),
            members(yes=8),
            slots_per_member=2,
        )
        repeated = 0
        trials = 60
        for seed in range(trials):
            result = allocate(roster, seed=seed)
            pairs = [
                frozenset(a.members) for a in result.assignments if len(a.members) == 2
            ]
            if len(pairs) != len(set(pairs)):
                repeated += 1
        self.assertLess(repeated, trials // 2)

    def test_idle_members_are_reported(self):
        roster = Roster(arcana(("神楽", 2)), members(yes=4), slots_per_member=1)
        result = allocate(roster, seed=17)
        self.assertEqual(len(result.idle_members()), 2)
        self.assertTrue(any("担当しないメンバー" in w for w in result.warnings))

    def test_invalid_roster_raises(self):
        roster = Roster(arcana(("神楽", 2)), members(no=2))
        with self.assertRaises(AllocationError):
            allocate(roster)


class CategoryTest(unittest.TestCase):
    def test_fill_category_reserves_no_slots(self):
        # 「瞬時(何度も)」は必ず入れる奥義ではない。必要人数を先取りしない。
        roster = Roster(
            arcana(("絆技", 2, FIXED), ("連打", 2, FILL)), members(yes=4)
        )
        self.assertEqual(roster.demand(), 2)
        self.assertEqual([a.name for a in roster.required_arcana()], ["絆技"])
        result = allocate(roster, seed=30)
        by_name = {a.arcanum: a for a in result.assignments}
        self.assertEqual(by_name["連打"].required, 0)
        self.assertFalse(by_name["連打"].is_short)

    def test_fixed_arcana_are_filled_before_any_fill_arcanum(self):
        # 枠が固定系ちょうどしかないとき、連打に横取りされない。
        roster = Roster(
            arcana(("絆A", 2, FIXED), ("絆B", 2, FIXED), ("連打", 2, FILL)),
            members(yes=2),
            slots_per_member=2,
        )
        result = allocate(roster, seed=31)
        by_name = {a.arcanum: a for a in result.assignments}
        self.assertEqual(len(by_name["絆A"].members), 2)
        self.assertEqual(len(by_name["絆B"].members), 2)
        self.assertEqual(by_name["連打"].members, [])
        # 0人でも不足扱いにしない。
        self.assertFalse(by_name["連打"].is_short)
        self.assertFalse(any("担当を付けられなかった" in w for w in result.warnings))

    def test_fill_category_absorbs_leftover_slots(self):
        roster = Roster(
            arcana(("絆技", 2, FIXED), ("連打", 2, FILL)),
            members(best=2, yes=4),
            slots_per_member=3,
        )
        result = allocate(roster, seed=18)
        by_name = {a.arcanum: a for a in result.assignments}
        self.assertEqual(len(by_name["絆技"].members), 2)
        # 連打は1人1回までなので、上限は軍師を除いた6人。
        self.assertEqual(len(by_name["連打"].members), 6)
        self.assertEqual(by_name["絆技"].extra, 0)

    def test_fill_reaches_absent_and_unknown_members(self):
        roster = Roster(
            arcana(("絆技", 2, FIXED), ("連打", 2, FILL)),
            members(best=1, yes=1, no=2, unknown=2),
            slots_per_member=2,
        )
        result = allocate(roster, seed=19)
        fill = [a for a in result.assignments if a.category == FILL][0]
        self.assertEqual(set(fill.members), {"◎0", "〇0", "×0", "×1", "－0", "－1"})

    def test_fill_never_assigns_same_arcanum_twice_to_one_member(self):
        roster = Roster(arcana(("連打", 2, FILL)), members(yes=4), slots_per_member=5)
        result = allocate(roster, seed=20)
        for held in result.load.values():
            self.assertEqual(held.count("連打"), 1)

    def test_fixed_categories_never_exceed_required(self):
        roster = Roster(
            arcana(("絆技", 2, FIXED), ("一定技", 2, "一定"), ("一度", 2, "瞬時(一度のみ)")),
            members(best=4, yes=4),
        )
        result = allocate(roster, seed=21)
        for assignment in result.assignments:
            self.assertEqual(len(assignment.members), assignment.required)
        self.assertEqual(result.extra_slots, 0)

    def test_multiple_fill_arcana_grow_evenly(self):
        roster = Roster(
            arcana(("絆技", 2, FIXED), ("連打A", 2, FILL), ("連打B", 2, FILL)),
            members(yes=6),
            slots_per_member=3,
        )
        result = allocate(roster, seed=22)
        counts = [
            len(a.members) for a in result.assignments if a.arcanum.startswith("連打")
        ]
        self.assertLessEqual(max(counts) - min(counts), 1)

    def test_no_fill_category_leaves_slots_unused(self):
        roster = Roster(arcana(("絆技", 2, FIXED)), members(yes=4), slots_per_member=2)
        result = allocate(roster, seed=23)
        self.assertEqual(result.used_slots, 2)
        self.assertEqual(result.spare_slots, 6)
        self.assertTrue(any(FILL in w for w in result.warnings))


class MemberOrderTest(unittest.TestCase):
    """メンバー別の出力は、担当数順ではなく連合員の並び順で出す."""

    def _roster(self):
        return Roster(
            arcana(("技", 2, FIXED)),
            [
                Member("ゼ", [ATTEND_YES] * BATTLE_COUNT),
                Member("軍", [ATTEND_YES] * BATTLE_COUNT, is_strategist=True),
                Member("ア", [ATTEND_BEST] * BATTLE_COUNT),
                Member("ン", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )

    def test_load_follows_roster_order(self):
        result = allocate(self._roster(), seed=50)
        # 軍師は割り当て対象外なので出てこない。残りは名簿の並びのまま。
        self.assertEqual(list(result.load), ["ゼ", "ア", "ン"])

    def test_order_survives_reordering_the_roster(self):
        roster = self._roster()
        roster.members[0], roster.members[3] = roster.members[3], roster.members[0]
        result = allocate(roster, seed=51)
        self.assertEqual(list(result.load), ["ン", "ア", "ゼ"])

    def test_text_and_csv_use_roster_order(self):
        from arcanum_generator.storage import export_csv, format_result_text

        result = allocate(self._roster(), seed=52)
        text = format_result_text(result)
        body = text.split("【メンバー別】")[1]
        positions = [body.index(name) for name in ("ゼ", "ア", "ン")]
        self.assertEqual(positions, sorted(positions), body)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            export_csv(result, path)
            csv_body = path.read_text(encoding="utf-8-sig").split("メンバー,")[1]
        csv_positions = [csv_body.index(name) for name in ("ゼ", "ア", "ン")]
        self.assertEqual(csv_positions, sorted(csv_positions), csv_body)


class StorageTest(unittest.TestCase):
    def test_roster_survives_save_and_load(self):
        roster = Roster(
            arcana(("神楽", 2, FILL), ("鬨の声", 3, "一定", True)),
            [
                # 戦ごとに違う参戦状況が往復すること。
                Member("A", [ATTEND_BEST, ATTEND_MAYBE, ATTEND_NO]),
                Member("B", [ATTEND_YES, ATTEND_YES, ATTEND_UNKNOWN]),
                Member("軍師", [ATTEND_YES] * BATTLE_COUNT, is_strategist=True),
            ],
            slots_per_member=5,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(roster, path)
            restored = load_roster(path)
        self.assertEqual(restored.slots_per_member, 5)
        self.assertEqual([a.category for a in restored.arcana], [FILL, "一定"])
        self.assertEqual([a.first_half for a in restored.arcana], [False, True])
        self.assertEqual(
            [m.attendance for m in restored.members],
            [
                [ATTEND_BEST, ATTEND_MAYBE, ATTEND_NO],
                [ATTEND_YES, ATTEND_YES, ATTEND_UNKNOWN],
                [ATTEND_YES] * BATTLE_COUNT,
            ],
        )
        self.assertEqual(
            [m.is_strategist for m in restored.members], [False, False, True]
        )

    def test_version1_file_is_read_with_defaults(self):
        from arcanum_generator.models import DEFAULT_CATEGORY

        legacy = {
            "version": 1,
            "slots_per_member": 2,
            "arcana": [{"name": "神楽", "required": 2}],
            "members": [
                {"name": "A", "attending": True},
                {"name": "B", "attending": False},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            restored = load_roster(path)
        self.assertEqual(restored.arcana[0].category, DEFAULT_CATEGORY)
        self.assertFalse(restored.arcana[0].first_half)
        # 参戦 True/False は 〇/× に読み替え、3戦とも同じ値にする。
        self.assertEqual(
            [m.attendance for m in restored.members],
            [[ATTEND_YES] * BATTLE_COUNT, [ATTEND_NO] * BATTLE_COUNT],
        )
        self.assertFalse(any(m.is_strategist for m in restored.members))

    def test_version3_single_attendance_is_spread_to_all_battles(self):
        legacy = {
            "version": 3,
            "slots_per_member": 4,
            "arcana": [{"name": "神楽", "required": 2, "category": "絆"}],
            "members": [{"name": "A", "attendance": ATTEND_BEST}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v3.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            restored = load_roster(path)
        self.assertEqual(restored.members[0].attendance, [ATTEND_BEST] * BATTLE_COUNT)


class AutosaveLocationTest(unittest.TestCase):
    def test_project_dir_is_where_main_py_lives(self):
        # カレントディレクトリではなくソースの位置から決まること。
        # (macOS で .command をダブルクリックするとカレントがホームになる)
        self.assertTrue((project_dir() / "main.py").is_file(), project_dir())

    def test_autosave_path_is_in_project_dir(self):
        self.assertEqual(autosave_path().parent, project_dir())
        self.assertEqual(autosave_path().name, AUTOSAVE_NAME)

    def test_project_dir_ignores_current_directory(self):
        import os

        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                self.assertTrue((project_dir() / "main.py").is_file())
            finally:
                os.chdir(original)


class AtomicSaveTest(unittest.TestCase):
    def test_save_leaves_no_temp_file_behind(self):
        roster = Roster(arcana(("神楽", 2)), members(yes=2))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(roster, path)
            leftovers = [p.name for p in Path(tmp).iterdir()]
        self.assertEqual(leftovers, ["kassen.json"])

    def test_overwrite_keeps_previous_content_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(Roster(arcana(("旧", 2)), members(yes=2)), path)
            save_roster(Roster(arcana(("新", 3)), members(best=1)), path)
            restored = load_roster(path)
        self.assertEqual([a.name for a in restored.arcana], ["新"])
        self.assertEqual(restored.arcana[0].required, 3)


if __name__ == "__main__":
    unittest.main()
