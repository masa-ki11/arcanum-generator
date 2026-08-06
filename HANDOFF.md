# 引き継ぎメモ(2026-08-06)

奥義に **「双 = 各戦2人」** の印を足す作業。**コードは完成・テスト通過済み。未コミット。**
残っているのは連合データの入力(どの4奥義に印を付けるか / 前衛の設定)だけ。

---

## 1. 何を作ったか

奥義ごとに付けられる3つ目の印 **`双`(各戦2人)**。

付けた奥義は、**どの戦にも確実に出せる担当(◎〇)が2人そろうまで**担当を足す。

なぜ要るか: 必要人数2人は**のべ人数**なので、担当が2人いてもその2人が別々の戦を欠けば
「どの戦も実際に出せるのは1人」という組み方になりうる。それを許さない指定。

- 担当は必要人数を超えて3人・4人になる
- **△は数に入れない**(足しても「確実に2人」にならないので頭数を増やさない)
- 2人に届かなかった戦は橙色表示 +【注意】。確実0人の戦は既存の「△頼み/×」で報告するので二重に出ない
- **「瞬時(何度も)」には効かない**(必要人数を確保しない種類)。GUI側でもチェックを無効化

仕様の詳細は `README.md` の「奥義に付ける印 → `双`(各戦2人)」節と「割り振りのルール」節。

## 2. 変更したファイル

| ファイル | 要点 |
|---|---|
| `arcanum_generator/models.py` | `Arcanum.two_per_battle` 追加 / `PAIR_PER_BATTLE = 2` / **FILE_VERSION 7 → 8** |
| `arcanum_generator/allocator.py` | **段階1.5** 新設(`cover_battles_twice`)、`sure_quota()`、`_constraint_order()`、警告追加、`_coverage` を人数比較に変更 |
| `arcanum_generator/gui.py` | チェックボックス「双 各戦2人」、印列を64pxに拡張、2人未達を橙色表示。印の生成を `storage.arcanum_marks()` に一本化 |
| `arcanum_generator/storage.py` | `PAIR_MARK = "双"`、`arcanum_marks()`、CSVに「各戦2人」列、コピー用テキストに凡例 |
| `tests/test_allocator.py` | `TwoPerBattleTest` を新設(13件追加)。全133件 |
| `README.md` | 印の表・段階リスト・測定結果を更新 |

### 実装で効いている2箇所(壊さないよう注意)

1. **段階1.5 の位置**(`allocator.py` の `allocate()`)
   全奥義に1人ずつ配った直後、**他のどの穴埋めよりも先**に2人目を取る。後ろに回すと
   枠が埋まったころには2人目を足せる相手が残らない。
   → テスト `test_pair_is_taken_before_other_arcana_fill_their_battles` が順序を固定している
   (段階4のあとに戻すと落ちる。確認済み)

2. **`_coverage()` を「穴の有無」から「人数」に変更**
   段階5.5(trim_excess)は必要人数を超えた担当を落とす。有無で比べると2人目が
   「居なくても穴は空いてない」と判定されて即落とされる。必要枠(`sure_quota`)で
   頭打ちにした人数で比較している。
   → 戻すと `[2,2,2]` が `[2,1,1]` に崩れる(確認済み)

## 3. 検証結果

連合20人 × 4枠 = 76枠、必須26奥義(= 枠を完全に使い切る盤面)で、
**必須26件から4件を選ぶ全14,950通り**を総当たり。

| 盤面 | 結果 |
|---|---|
| 今の `kassen.json` の前衛設定のまま | 2人未達 **0件** |
| 前衛を各戦5人にした盤面 × 配置3種 | 2人未達 **0件** / 前衛穴 **0件** |

- 段階1.5を段階4のあとに置くと140通りで未達 → 前倒しで0。他の奥義の穴もむしろ2割減
- **「双は前衛以外の人に付ける」案は効果ゼロ**(前衛が各戦2〜5人のどこでも現状と同じ)。
  前衛奥義は2件しかなく、段階1.5で先に押さえた時点で競合しないため。**実装していない**

### 測定でハマった罠(再測定するとき用)

前衛の盤面を自動生成するとき、母集団を「**3戦とも**確実に出る人」にすると
`kassen.json` では**3人しか取れない**(うらん・ゼロ・B)。「各戦5人」と指定しても
実際は3人の盤面になり、数字が嘘になる。前衛は**戦ごとの設定**なので、
戦ごとに「**その戦に**確実に出る人」から選ぶこと。

```python
def set_vanguard(rng, per_battle):          # 正しい生成
    for m in roster.members:
        m.vanguard = [False] * 3
    for b in range(3):
        pool = [m for m in roster.fill_members() if m.is_sure(b)]
        for m in rng.sample(pool, min(per_battle, len(pool))):
            m.vanguard[b] = True
```

総当たりは以下で回せる(1周およそ40秒)。

```python
import itertools
from arcanum_generator.storage import load_roster
from arcanum_generator.allocator import allocate

r = load_roster("kassen.json")
names = [a.name for a in r.arcana if not a.fills_leftover]
bad = 0
for combo in itertools.combinations(names, 4):
    for a in r.arcana:
        a.two_per_battle = a.name in combo
    if any(x.short_pair_battles for x in allocate(r, seed=1).assignments):
        bad += 1
print(bad)
```

## 4. 別PCで再開する手順

### まずこのPC側で(未コミットのため必須)

```bash
git switch -c two-per-battle      # main に直接置いてあるのでブランチを切る
git add README.md HANDOFF.md arcanum_generator tests
git commit
git push -u origin two-per-battle
```

- `kassen.json` は `.gitignore` に入っているが**追跡は残っている**(以前コミットされたため)。
  連合データなので、別PCに持っていくならコミットするか手でコピーする。追跡をやめたいなら
  `git rm --cached kassen.json`
- 現在の `kassen.json` の差分はこの作業とは無関係(作業前からあったもの。こちらでは触っていない)

### 別PC側で

```bash
git clone https://github.com/masa-ki11/arcanum-generator.git
cd arcanum-generator
git switch two-per-battle
```

環境構築は `README.md` の「環境構築」節のとおり(Python 3.14 / `.venv`)。確認:

```bash
.venv/bin/python -m unittest discover -s tests -q          # 133件 OK になるはず
.venv/bin/python -c "from arcanum_generator.gui import selftest; raise SystemExit(selftest())"
.venv/bin/python main.py                                    # 起動
```

## 5. 残っているタスク

1. **`双` を付ける4奥義を決めて画面でチェック**を入れる(奥義を選んで「選択中を更新」)
2. **前衛の設定を入れ直す** — 今の `kassen.json` は前衛がちゃんと設定されていない。
   各戦5人ずつを想定。`前1`〜`前3` 列をダブルクリック、または「対象」で戦を選んで
   「前衛」ボタンでまとめて切り替え
3. コミット & push(上記)

## 6. 注意点

- **FILE_VERSION が 8 に上がった。** このブランチのアプリで保存した `kassen.json` は
  旧バージョンのアプリでは開けない(「未対応のファイル形式です」)。逆(旧→新)は読める
- `双` は「**他の奥義を削ってでも各戦2人**」という優先順位の指定。原理上、印の無い奥義に
  ×や△頼みが出ることはある(実データでは逆に減ったが、性質としては把握しておくこと)
- 印は最大3つ(`★前双`)並ぶ。列幅を狭めると隠れる
