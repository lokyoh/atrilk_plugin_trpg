# 不肯学的幼龙

```event-graph
{
  "id": "ev_崖顶天文台",
  "title": "不肯学的幼龙",
  "duration_days": 4,
  "scale": "heavy",
  "stage": "风骸岛 · 崖顶天文台",
  "default_outcome": "仪式完成，岛上的死龙醒来，而你们还在山下",
  "rumors": [
    "东南边那片废墟最近有龙在飞。",
    "狗头人管那道蓝光叫「主人的脾气」。",
    "有只青铜龙被关在塔楼里——关它的和关它的是仇家。"
  ],
  "graph": {
    "conflict": "斯帕克兰德要用艾德隆的命唤醒岛上所有死去的巨龙",
    "scale": "heavy",
    "facts": {
      "at_d1": {
        "text": "鸟瞰台看得见整座岛",
        "kind": "proof"
      },
      "at_d2": {
        "text": "圆顶大厅有斯帕克兰德的雕像",
        "kind": "secret"
      },
      "at_d3": {
        "text": "狗头人认得斯帕克兰德",
        "kind": "proof"
      },
      "at_d4": {
        "text": "独立研究院有古怪的装置",
        "kind": "proof"
      },
      "at_d5": {
        "text": "塔楼里关着青铜幼龙",
        "kind": "secret"
      },
      "at_d6": {
        "text": "秘密图书馆记着龙的真名",
        "kind": "secret"
      },
      "killed_spark": {
        "text": "斯帕克兰德死了",
        "kind": "proof",
        "excludes": [
          "freed_aidron"
        ]
      },
      "freed_aidron": {
        "text": "艾德隆自由了",
        "kind": "debt"
      },
      "ritual_done": {
        "text": "仪式完成了",
        "kind": "debt"
      }
    },
    "nodes": {
      "n_d1": {
        "title": "鸟瞰台",
        "at": "鸟瞰台",
        "kind": "clue",
        "requires": [],
        "grants": [
          "at_d1"
        ],
        "on_fail": [],
        "cost": "被看见的人会知道",
        "depth": 1,
        "hint": "鸟瞰台看得见整座岛，东南边那道蓝光正在废墟上盘旋。"
      },
      "n_d2": {
        "title": "圆顶大厅废墟",
        "at": "圆顶大厅废墟",
        "kind": "encounter",
        "requires": [
          "at_d1"
        ],
        "grants": [
          "at_d2"
        ],
        "on_fail": [],
        "cost": "在雕像前动手，消息会传出去",
        "depth": 1,
        "hint": ""
      },
      "n_d2r": {
        "title": "圆顶大厅废墟",
        "at": "圆顶大厅废墟",
        "kind": "encounter",
        "requires": [
          "at_d2"
        ],
        "grants": [
          "ritual_done"
        ],
        "on_fail": [],
        "cost": "你没能打断那场仪式",
        "depth": 2,
        "hint": ""
      },
      "n_d3": {
        "title": "狗头人营地",
        "at": "狗头人营地",
        "kind": "battle",
        "requires": [
          "at_d2"
        ],
        "grants": [
          "at_d3"
        ],
        "on_fail": [],
        "cost": "狗头人会替他们的主人记着你",
        "depth": 1,
        "hint": ""
      },
      "n_d4": {
        "title": "独立研究院",
        "at": "独立研究院",
        "kind": "clue",
        "requires": [
          "at_d3"
        ],
        "grants": [
          "at_d4"
        ],
        "on_fail": [],
        "cost": "碰过装置的人会留下痕迹",
        "depth": 1,
        "hint": ""
      },
      "n_d5": {
        "title": "天文台塔楼",
        "at": "天文台塔楼",
        "kind": "battle",
        "requires": [
          "at_d4"
        ],
        "grants": [
          "at_d5"
        ],
        "on_fail": [],
        "cost": "艾德隆的爪痕会留在你身上",
        "depth": 1,
        "hint": ""
      },
      "n_d6": {
        "title": "秘密图书馆",
        "at": "秘密图书馆",
        "kind": "clue",
        "requires": [
          "at_d4"
        ],
        "grants": [
          "at_d6"
        ],
        "on_fail": [],
        "cost": "读完真名的人没法装作不知道",
        "depth": 2,
        "hint": ""
      },
      "n_d5b": {
        "title": "天文台塔楼",
        "at": "天文台塔楼",
        "kind": "battle",
        "requires": [
          "at_d6"
        ],
        "grants": [
          "killed_spark"
        ],
        "on_fail": [],
        "cost": "斯帕克兰德不会让你走",
        "depth": 2,
        "hint": ""
      },
      "n_d6b": {
        "title": "秘密图书馆",
        "at": "秘密图书馆",
        "kind": "clue",
        "requires": [
          "at_d6"
        ],
        "grants": [
          "freed_aidron"
        ],
        "on_fail": [],
        "cost": "放走一条龙，另一条会记得",
        "depth": 2,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "斯帕克兰德伏诛",
        "requires": [
          [
            "killed_spark"
          ],
          [
            "at_d5"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "艾德隆自由了",
        "requires": [
          [
            "freed_aidron"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "仪式完成了",
        "requires": [
          [
            "ritual_done"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "斯帕克兰德的仪式",
        "every": 4,
        "fires": "n_d5b"
      }
    ]
  }
}
```
