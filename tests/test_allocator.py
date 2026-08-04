"""割り振りロジックのテスト."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arcanum_generator.allocator import AllocationError, allocate, validate
from arcanum_generator.models import FILE_VERSION
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
    export_csv,
    format_result_text,
    load_roster,
    project_dir,
    save_roster,
)

FILL = "瞬時(何度も)"
FIXED = "絆"


def arcana(*items) -> list[Arcanum]:
    """(名前, 必要人数[, 種類[, 前半必須[, 前衛向け]]]) から奥義リストを作る."""
    built = []
    for item in items:
        name, required = item[0], item[1]
        category = item[2] if len(item) > 2 else FIXED
        first_half = item[3] if len(item) > 3 else False
        for_vanguard = item[4] if len(item) > 4 else False
        built.append(Arcanum(name, required, category, first_half, for_vanguard))
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


class VanguardTest(unittest.TestCase):
    def test_default_is_all_rear(self):
        member = Member("A")
        self.assertEqual(member.vanguard, [False] * BATTLE_COUNT)
        self.assertEqual(member.vanguard_battles(), set())

    def test_vanguard_is_per_battle(self):
        member = Member("A", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True, False, True])
        self.assertTrue(member.is_vanguard(0))
        self.assertFalse(member.is_vanguard(1))
        self.assertEqual(member.vanguard_battles(), {0, 2})

    def test_single_bool_spreads_to_all_battles(self):
        self.assertEqual(Member("A", vanguard=True).vanguard, [True] * BATTLE_COUNT)

    def test_short_list_is_padded(self):
        self.assertEqual(Member("A", vanguard=[True]).vanguard, [True, False, False])

    def test_roster_counts_only_attending_vanguards(self):
        roster = Roster(
            arcana(("技", 1, FIXED)),
            [
                Member("出る前衛", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("休む前衛", [ATTEND_NO] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("軍師前衛", [ATTEND_YES] * BATTLE_COUNT, True, [True] * 3),
                Member("後衛", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        # 出られない人と軍師は数えない。
        self.assertEqual([m.name for m in roster.vanguards(0)], ["出る前衛"])

    def test_result_carries_vanguard(self):
        roster = Roster(
            arcana(("技", 2, FIXED)),
            [
                Member("A", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True, False, False]),
                Member("B", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=60)
        self.assertEqual(result.vanguard["A"], [True, False, False])
        self.assertEqual(result.vanguard["B"], [False] * BATTLE_COUNT)

    def test_vanguard_survives_save_and_load(self):
        roster = Roster(
            arcana(("技", 2, FIXED)),
            [
                Member("A", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True, False, True]),
                Member("B", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(roster, path)
            restored = load_roster(path)
        self.assertEqual(restored.members[0].vanguard, [True, False, True])
        self.assertEqual(restored.members[1].vanguard, [False] * BATTLE_COUNT)

    def test_version4_file_without_vanguard_loads_as_rear(self):
        legacy = {
            "version": 4,
            "slots_per_member": 4,
            "arcana": [{"name": "技", "required": 2, "category": FIXED}],
            "members": [{"name": "A", "attendance": [ATTEND_YES] * BATTLE_COUNT}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v4.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            restored = load_roster(path)
        self.assertEqual(restored.members[0].vanguard, [False] * BATTLE_COUNT)



class VanguardArcanumTest(unittest.TestCase):
    """前衛向けの奥義は、どの戦にも前衛の担当が1人以上いるようにする."""

    def test_vanguard_arcanum_prefers_vanguard_members(self):
        roster = Roster(
            arcana(("前衛技", 1, FIXED, False, True)),
            [
                Member("後衛", [ATTEND_BEST] * BATTLE_COUNT),
                Member("前衛", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True] * 3),
            ],
        )
        result = allocate(roster, seed=70)
        self.assertEqual(result.assignments[0].members, ["前衛"])
        self.assertEqual(result.assignments[0].uncovered_vanguard, [])

    def test_expands_when_vanguard_differs_per_battle(self):
        # 戦ごとに前衛が入れ替わるので、必要人数1でも3人必要になる。
        roster = Roster(
            arcana(("前衛技", 1, FIXED, False, True)),
            [
                Member("前1", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True, False, False]),
                Member("前2", [ATTEND_YES] * BATTLE_COUNT, vanguard=[False, True, False]),
                Member("前3", [ATTEND_YES] * BATTLE_COUNT, vanguard=[False, False, True]),
                Member("後衛", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=71)
        assignment = result.assignments[0]
        self.assertEqual(sorted(assignment.members), ["前1", "前2", "前3"])
        self.assertEqual(assignment.vanguard_battles, [True] * BATTLE_COUNT)

    def test_vanguard_must_also_attend_that_battle(self):
        # 2戦目は前衛だが参戦しないので、その戦は埋まったことにしない。
        roster = Roster(
            arcana(("前衛技", 1, FIXED, False, True)),
            [
                Member("休む前衛", [ATTEND_YES, ATTEND_NO, ATTEND_YES], vanguard=[True] * 3),
                Member("後衛", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=72)
        self.assertEqual(result.assignments[0].uncovered_vanguard, [1])
        self.assertTrue(
            any("前衛の担当がいない戦" in w for w in result.warnings)
        )

    def test_no_warning_when_all_battles_have_a_vanguard(self):
        roster = Roster(
            arcana(("前衛技", 2, FIXED, False, True)),
            [
                Member("通し前衛", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("後衛", [ATTEND_YES] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=73)
        self.assertEqual(result.assignments[0].uncovered_vanguard, [])
        self.assertFalse(any("前衛の担当がいない戦" in w for w in result.warnings))

    def test_rearguard_is_never_assigned_even_to_fill_the_required_count(self):
        # 全戦とも前衛の人が1人いてもカバーは済むが、2人目に後衛を入れてはいけない。
        roster = Roster(
            arcana(("前衛技", 2, FIXED, False, True)),
            [
                Member("通し前衛", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("前衛2", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True, False, False]),
                Member("後衛A", [ATTEND_BEST] * BATTLE_COUNT),
                Member("後衛B", [ATTEND_BEST] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=80)
        self.assertEqual(sorted(result.assignments[0].members), ["前衛2", "通し前衛"])

    def test_shortage_rather_than_falling_back_to_rearguard(self):
        # 前衛が1人しかいなければ1人担当のままにする。後衛で埋めない。
        roster = Roster(
            arcana(("前衛技", 2, FIXED, False, True)),
            [
                Member("唯一の前衛", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("後衛A", [ATTEND_BEST] * BATTLE_COUNT),
                Member("後衛B", [ATTEND_BEST] * BATTLE_COUNT),
            ],
        )
        result = allocate(roster, seed=81)
        assignment = result.assignments[0]
        self.assertEqual(assignment.members, ["唯一の前衛"])
        self.assertTrue(assignment.is_short)
        self.assertTrue(
            any("前衛として出られる人が" in w for w in result.warnings)
        )

    def test_vanguard_who_never_attends_is_not_eligible(self):
        # 前衛に設定されていても、その戦に参戦しないなら前衛として使えない。
        roster = Roster(
            arcana(("前衛技", 1, FIXED, False, True)),
            [
                Member("来ない前衛", [ATTEND_NO] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("出る前衛", [ATTEND_YES, ATTEND_NO, ATTEND_NO], vanguard=[True] * 3),
            ],
        )
        result = allocate(roster, seed=82)
        self.assertEqual(result.assignments[0].members, ["出る前衛"])

    def test_fill_category_also_respects_vanguard(self):
        # 「瞬時(何度も)」に前衛向けを付けたら、余り埋めでも後衛には配らない。
        roster = Roster(
            arcana(("連打", 2, FILL, False, True)),
            [
                Member("前衛", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True] * 3),
                Member("後衛A", [ATTEND_YES] * BATTLE_COUNT),
                Member("後衛B", [ATTEND_NO] * BATTLE_COUNT),
            ],
            slots_per_member=4,
        )
        result = allocate(roster, seed=83)
        self.assertEqual(result.assignments[0].members, ["前衛"])

    def test_plain_arcanum_ignores_vanguard(self):
        roster = Roster(
            arcana(("普通技", 1, FIXED)),
            [Member("後衛", [ATTEND_YES] * BATTLE_COUNT)],
        )
        result = allocate(roster, seed=74)
        self.assertEqual(result.assignments[0].uncovered_vanguard, [])

    def test_flag_survives_save_and_load(self):
        roster = Roster(
            arcana(("前衛技", 2, FIXED, False, True), ("普通技", 2)),
            members(yes=3),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(roster, path)
            restored = load_roster(path)
        self.assertEqual([a.for_vanguard for a in restored.arcana], [True, False])


class OutputExcludesVanguardTest(unittest.TestCase):
    """連合員の前衛設定は出力に載せない(奥義側の印は載せる)."""

    def _result(self):
        roster = Roster(
            arcana(("前衛技", 1, FIXED, False, True)),
            [Member("A", [ATTEND_YES] * BATTLE_COUNT, vanguard=[True, False, True])],
        )
        return allocate(roster, seed=75)

    def test_text_has_no_member_vanguard_marks(self):
        from arcanum_generator.storage import format_result_text

        body = format_result_text(self._result()).split("【メンバー別】")[1]
        self.assertNotIn("前－前", body)
        self.assertIn("A:", body)

    def test_csv_has_no_member_vanguard_columns(self):
        from arcanum_generator.storage import export_csv

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            export_csv(self._result(), path)
            text = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("1戦目の前衛", text)
        self.assertIn("メンバー,1戦目,2戦目,3戦目,担当数,担当奥義", text)
        # 奥義側の「前衛向け」印は残す。
        self.assertIn("種類,前半必須,前衛向け,奥義", text)


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


class FileVersionTest(unittest.TestCase):
    def test_current_version_is_accepted(self):
        """保存した直後のファイルが必ず読めること.

        保存側と読み込み側でバージョンを二重管理していた頃、項目を増やすたびに
        受け入れ側の更新を忘れて開けなくなった。その再発を防ぐ。
        """
        from arcanum_generator.models import FILE_VERSION
        from arcanum_generator.storage import SUPPORTED_VERSIONS

        self.assertIn(FILE_VERSION, SUPPORTED_VERSIONS)
        roster = Roster(arcana(("技", 2)), members(yes=2))
        self.assertEqual(roster.to_dict()["version"], FILE_VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(roster, path)
            load_roster(path)  # 例外が出ないこと

    def test_all_older_versions_are_accepted(self):
        from arcanum_generator.models import FILE_VERSION
        from arcanum_generator.storage import SUPPORTED_VERSIONS

        self.assertEqual(set(SUPPORTED_VERSIONS), set(range(1, FILE_VERSION + 1)))

    def test_future_version_is_rejected(self):
        from arcanum_generator.models import FILE_VERSION

        future = {"version": FILE_VERSION + 1, "arcana": [], "members": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "future.json"
            path.write_text(json.dumps(future), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_roster(path)


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


class FrozenPathTest(unittest.TestCase):
    """配布用にビルドしたときの保存先."""

    def _project_dir_for(self, executable: str) -> Path:
        import arcanum_generator.storage as storage

        original_frozen = getattr(sys, "frozen", None)
        original_exe = sys.executable
        sys.frozen = True  # type: ignore[attr-defined]
        sys.executable = executable
        try:
            return storage.project_dir()
        finally:
            sys.executable = original_exe
            if original_frozen is None:
                del sys.frozen  # type: ignore[attr-defined]
            else:
                sys.frozen = original_frozen  # type: ignore[attr-defined]

    def test_windows_exe_saves_next_to_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "arcanum-generator.exe"
            exe.write_text("", encoding="utf-8")
            self.assertEqual(self._project_dir_for(str(exe)), Path(tmp).resolve())

    def test_macos_app_saves_outside_the_bundle(self):
        # .app の中に保存するとアプリ内部に隠れてしまうので、.app と同じ階層に置く。
        with tempfile.TemporaryDirectory() as tmp:
            inner = Path(tmp) / "arcanum-generator.app" / "Contents" / "MacOS"
            inner.mkdir(parents=True)
            exe = inner / "arcanum-generator"
            exe.write_text("", encoding="utf-8")
            self.assertEqual(self._project_dir_for(str(exe)), Path(tmp).resolve())


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


class CarryoverTest(unittest.TestCase):
    """複数日にまたがる割り振り: 前回の担当を引き継いで差分だけ組み直す."""

    def _picked(self, result, name: str) -> list[str]:
        for assignment in result.assignments:
            if assignment.arcanum == name:
                return assignment.members
        raise AssertionError(f"{name} が結果にありません")

    def test_none_means_from_scratch(self):
        """carryover を渡さないときは従来どおり. 記録も付かない."""
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        self.assertIsNone(allocate(roster).carryover)

    def test_unchanged_roster_keeps_every_assignment(self):
        """入力が変わらなければ、担当は1件も動かない.

        ゼロからだとタイブレークがランダムなので毎回顔ぶれが変わる。引き継ぎは
        そこを固定するのが目的なので、ここが崩れたら機能として意味がない。
        """
        roster = Roster(arcana(("神楽", 2), ("鼓舞", 2), ("陣", 2)), members(best=8))
        first = allocate(roster)
        for _ in range(5):
            again = allocate(roster, carryover=first.to_carryover())
            self.assertEqual(again.to_carryover(), first.to_carryover())
            self.assertEqual(again.carryover.dropped, [])
            self.assertEqual(again.carryover.added, [])

    def test_absent_member_is_pruned_and_replaced(self):
        """参戦しなくなった人は外れ、代わりが入る(剪定)."""
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        first = allocate(roster)
        leaving = self._picked(first, "神楽")[0]
        staying = self._picked(first, "神楽")[1]

        for member in roster.members:
            if member.name == leaving:
                member.attendance = [ATTEND_NO] * BATTLE_COUNT

        second = allocate(roster, carryover=first.to_carryover())
        picked = self._picked(second, "神楽")
        self.assertNotIn(leaving, picked)
        self.assertIn(staying, picked)  # 変える必要のない人はそのまま
        self.assertEqual(len(picked), 2)  # 抜けた分は埋め直される
        self.assertTrue(any("今回は参戦しない" in d for d in second.carryover.dropped))

    def test_downgrade_to_maybe_keeps_the_member_and_adds_backup(self):
        """◎→△に落ちた人は残したまま、確実に出せる人を足す."""
        roster = Roster(arcana(("神楽", 2)), members(best=6))
        first = allocate(roster)
        kept = self._picked(first, "神楽")
        for member in roster.members:
            if member.name in kept:
                member.attendance = [ATTEND_MAYBE] * BATTLE_COUNT

        second = allocate(roster, carryover=first.to_carryover())
        picked = self._picked(second, "神楽")
        for name in kept:
            self.assertIn(name, picked)
        self.assertGreater(len(picked), len(kept))
        # 足された人は◎〇なので、△頼みの戦は残らない。
        神楽 = next(a for a in second.assignments if a.arcanum == "神楽")
        self.assertEqual(神楽.unsure_battles, [])

    def test_removed_arcanum_and_member_are_reported(self):
        """奥義やメンバーが消えた引き継ぎは、理由付きで外れる."""
        roster = Roster(arcana(("神楽", 2), ("鼓舞", 2)), members(best=4))
        first = allocate(roster)
        stale = first.to_carryover()
        stale["存在しない奥義"] = ["best0"]
        stale["神楽"] = list(stale["神楽"]) + ["いない人"]

        second = allocate(roster, carryover=stale)
        dropped = "\n".join(second.carryover.dropped)
        self.assertIn("奥義が無くなった", dropped)
        self.assertIn("メンバーがいない", dropped)
        self.assertNotIn("いない人", self._picked(second, "神楽"))

    def test_member_turned_strategist_is_dropped(self):
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        first = allocate(roster)
        gone = self._picked(first, "神楽")[0]
        for member in roster.members:
            if member.name == gone:
                member.is_strategist = True

        second = allocate(roster, carryover=first.to_carryover())
        self.assertNotIn(gone, self._picked(second, "神楽"))

    def test_rearguard_is_dropped_when_arcanum_becomes_vanguard_only(self):
        roster = Roster(
            arcana(("突撃", 2)),
            [
                Member("前衛A", [ATTEND_BEST] * BATTLE_COUNT, vanguard=True),
                Member("前衛B", [ATTEND_BEST] * BATTLE_COUNT, vanguard=True),
                Member("後衛A", [ATTEND_BEST] * BATTLE_COUNT),
                Member("後衛B", [ATTEND_BEST] * BATTLE_COUNT),
            ],
        )
        stale = {"突撃": ["後衛A", "前衛A"]}
        roster.arcana[0].for_vanguard = True

        result = allocate(roster, carryover=stale)
        picked = self._picked(result, "突撃")
        self.assertNotIn("後衛A", picked)
        self.assertIn("前衛A", picked)
        self.assertTrue(any("前衛でなくなった" in d for d in result.carryover.dropped))

    def test_slot_cap_is_respected(self):
        """1人あたりの枠を減らしたら、引き継ぎもその上限を超えない."""
        roster = Roster(
            arcana(("A", 1), ("B", 1), ("C", 1)), members(best=3), slots_per_member=1
        )
        stale = {"A": ["◎0"], "B": ["◎0"], "C": ["◎0"]}
        result = allocate(roster, carryover=stale)
        self.assertEqual(len(result.load["◎0"]), 1)
        self.assertTrue(any("枠がいっぱい" in d for d in result.carryover.dropped))

    def test_duplicate_in_carryover_is_dropped(self):
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        result = allocate(roster, carryover={"神楽": ["◎0", "◎0"]})
        self.assertEqual(self._picked(result, "神楽").count("◎0"), 1)
        self.assertTrue(any("重複" in d for d in result.carryover.dropped))

    def test_required_arcana_win_slots_over_leftover_fill(self):
        """必須の奥義は、前回の余り埋めより先に枠を取る.

        余り埋めを先に戻すと、本来そこに入るべき必須の奥義が枠切れで入れない。
        """
        roster = Roster(
            arcana(("必須", 2, FIXED), ("連打", 1, FILL)),
            members(best=2),
            slots_per_member=1,
        )
        stale = {"連打": ["◎0", "◎1"], "必須": []}
        result = allocate(roster, carryover=stale)
        self.assertEqual(len(self._picked(result, "必須")), 2)
        self.assertEqual(self._picked(result, "連打"), [])

    def test_leftover_fill_is_carried_over_when_slots_remain(self):
        """枠が余っていれば、余り埋めの担当も前回のまま残る."""
        roster = Roster(arcana(("必須", 2, FIXED), ("連打", 1, FILL)), members(best=4))
        first = allocate(roster)
        second = allocate(roster, carryover=first.to_carryover())
        self.assertEqual(
            sorted(self._picked(second, "連打")), sorted(self._picked(first, "連打"))
        )

    def test_absent_member_still_gets_leftover_fill(self):
        """×の人でも余り埋めの担当は引き継ぐ(その段階は参戦を見ないため)."""
        roster = Roster(arcana(("必須", 2, FIXED), ("連打", 1, FILL)), members(best=3, no=1))
        result = allocate(roster, carryover={"連打": ["×0"], "必須": []})
        self.assertIn("×0", self._picked(result, "連打"))

    def test_report_counts_add_up(self):
        roster = Roster(arcana(("神楽", 2), ("鼓舞", 2)), members(best=6))
        first = allocate(roster)
        second = allocate(roster, carryover=first.to_carryover())
        report = second.carryover
        self.assertEqual(report.source_total, len(report.kept) + len(report.dropped))
        self.assertEqual(
            len(report.kept) + len(report.added),
            sum(len(a.members) for a in second.assignments),
        )
        self.assertIn("維持", report.summary())

    def test_warning_mentions_carryover(self):
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        first = allocate(roster)
        second = allocate(roster, carryover=first.to_carryover())
        self.assertTrue(any("【引き継ぎ】" in w for w in second.warnings))


class CarryoverMarkTest(unittest.TestCase):
    """コピー用テキスト・CSVに付く「変わった人」の印."""

    def _roster(self):
        return Roster(arcana(("神楽", 2), ("鼓舞", 2)), members(best=6))

    def test_no_mark_when_allocated_from_scratch(self):
        """ゼロから割り振ったときは印を付けない(全員が変更扱いになるため)."""
        text = format_result_text(allocate(self._roster()))
        self.assertNotIn("←変更", text)
        self.assertNotIn("＊", text)

    @staticmethod
    def _body(text: str) -> list[str]:
        """凡例(※で始まる行)を除いた本文の行. 印の数を数えるのに使う."""
        return [l for l in text.splitlines() if l and not l.startswith("※")]

    def test_unchanged_member_gets_no_mark(self):
        roster = self._roster()
        first = allocate(roster)
        text = format_result_text(allocate(roster, carryover=first.to_carryover()))
        body = "\n".join(self._body(text))
        self.assertNotIn("←変更", body)
        self.assertNotIn("＊", body)
        # 何も変わっていないことは、印ではなく文で伝える。
        self.assertIn("前回から担当が変わった人はいません", text)
        self.assertNotIn("新しくその奥義の担当", text)

    def test_changed_member_is_marked_with_the_difference(self):
        roster = self._roster()
        first = allocate(roster)
        gone = next(a for a in first.assignments if a.arcanum == "神楽").members[0]
        for member in roster.members:
            if member.name == gone:
                member.attendance = [ATTEND_NO] * BATTLE_COUNT

        second = allocate(roster, carryover=first.to_carryover())
        report = second.carryover
        # 抜けた人は「外れ」、代役は「追加」。
        self.assertEqual(report.member_removed[gone], ["神楽"])
        self.assertEqual(report.member_added[gone], [])
        replacement = next(
            n for n, v in report.member_added.items() if "神楽" in v
        )
        self.assertEqual(report.changed_members(), {gone, replacement})

        text = format_result_text(second)
        # 代役は奥義別で＊、メンバー別で ←変更(追加 神楽)。
        self.assertIn(replacement + "＊", text)
        self.assertIn(f"{replacement}: ", text)
        self.assertIn("←変更(追加 神楽)", text)
        self.assertIn("←変更(外れ 神楽)", text)
        self.assertIn("前回から担当が変わったのは2人", text)
        # 動いていない人の行には印が付かない(凡例を除いて2行だけ)。
        marked = [l for l in self._body(text) if "←変更" in l]
        self.assertEqual(len(marked), 2)

    def test_swap_shows_both_sides(self):
        """担当が入れ替わった人は 追加 と 外れ の両方が出る."""
        roster = Roster(arcana(("神楽", 1), ("鼓舞", 1)), members(best=2))
        stale = {"神楽": ["◎0"], "鼓舞": ["◎0"]}
        roster.slots_per_member = 1  # ◎0 は1つしか持てない
        second = allocate(roster, carryover=stale)
        note = second.carryover.change_note("◎1")
        self.assertIn("追加", note)
        self.assertEqual(second.carryover.change_note("◎0").count("追加"), 0)

    def test_removed_arcanum_counts_as_a_change(self):
        """奥義ごと消えた場合も、担当だった人は変更扱いにする."""
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        stale = {"神楽": ["◎0", "◎1"], "廃止": ["◎2"]}
        result = allocate(roster, carryover=stale)
        self.assertEqual(result.carryover.member_removed["◎2"], ["廃止"])
        self.assertIn("←変更(外れ 廃止)", format_result_text(result))

    def test_csv_gains_a_change_column_only_when_carried_over(self):
        roster = self._roster()
        first = allocate(roster)
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.csv"
            export_csv(first, plain)
            self.assertNotIn("前回から", plain.read_text(encoding="utf-8-sig"))

            gone = next(a for a in first.assignments if a.arcanum == "神楽").members[0]
            for member in roster.members:
                if member.name == gone:
                    member.attendance = [ATTEND_NO] * BATTLE_COUNT
            carried = Path(tmp) / "carried.csv"
            export_csv(allocate(roster, carryover=first.to_carryover()), carried)
            body = carried.read_text(encoding="utf-8-sig")
        self.assertIn("前回から", body)
        self.assertIn("外れ 神楽", body)
        self.assertIn("追加 神楽", body)

    def test_change_note_is_empty_for_untouched_member(self):
        roster = self._roster()
        first = allocate(roster)
        second = allocate(roster, carryover=first.to_carryover())
        for name in second.load:
            self.assertEqual(second.carryover.change_note(name), "")


class CarryoverStorageTest(unittest.TestCase):
    """引き継ぎ元の保存・読み込み."""

    def test_round_trip(self):
        roster = Roster(arcana(("神楽", 2)), members(best=4))
        roster.carryover = allocate(roster).to_carryover()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kassen.json"
            save_roster(roster, path)
            restored = load_roster(path)
        self.assertEqual(restored.carryover, roster.carryover)
        self.assertTrue(restored.has_carryover())

    def test_old_file_without_carryover_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 6,
                        "arcana": [{"name": "神楽", "required": 2, "category": FIXED}],
                        "members": [{"name": "A", "attendance": [ATTEND_BEST] * 3}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            restored = load_roster(path)
        self.assertEqual(restored.carryover, {})
        self.assertFalse(restored.has_carryover())

    def test_broken_carryover_is_ignored(self):
        """形が違う引き継ぎで落ちない(手で編集されたファイル対策)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odd.json"
            path.write_text(
                json.dumps(
                    {
                        "version": FILE_VERSION,
                        "arcana": [{"name": "神楽", "required": 2, "category": FIXED}],
                        "members": [{"name": "A", "attendance": [ATTEND_BEST] * 3}],
                        "carryover": {"神楽": "文字列", "鼓舞": ["A"], "変": 3},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            restored = load_roster(path)
        self.assertEqual(restored.carryover, {"鼓舞": ["A"]})

    def test_empty_carryover_is_not_offered(self):
        roster = Roster(arcana(("神楽", 2)), members(best=2))
        self.assertFalse(roster.has_carryover())
        roster.carryover = {"神楽": []}
        self.assertFalse(roster.has_carryover())  # 中身が空なら引き継ぐものがない
        roster.carryover = {"神楽": ["A"]}
        self.assertTrue(roster.has_carryover())
        self.assertEqual(roster.carryover_size(), 1)


if __name__ == "__main__":
    unittest.main()
