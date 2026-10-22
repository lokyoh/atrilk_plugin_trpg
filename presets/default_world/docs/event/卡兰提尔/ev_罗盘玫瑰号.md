# 不肯散的船长

```event-graph
{
  "id": "ev_罗盘玫瑰号",
  "title": "不肯散的船长",
  "duration_days": 4,
  "scale": "medium",
  "stage": "风骸岛 · 罗盘玫瑰号残骸",
  "default_outcome": "雾起船散，岛上的人以为那些船难终于停了",
  "rumors": [
    "最近又有船撞在岛北的礁上。",
    "修道院的人说那是罗盘玫瑰号——一艘二十年前就该沉的船。",
    "那艘船上的船长还在找他的妻子。"
  ],
  "graph": {
    "conflict": "罗盘玫瑰号上有一具不肯安息的船长，和一只引船撞礁的鸟妖",
    "scale": "medium",
    "facts": {
      "at_c1": {
        "text": "主甲板上有拖拽的痕迹",
        "kind": "proof"
      },
      "at_c2": {
        "text": "前桅压着碎裂的弩炮",
        "kind": "proof"
      },
      "at_c3": {
        "text": "船长的靴子钉在地板上",
        "kind": "proof"
      },
      "at_c4": {
        "text": "船长室里有一本日记",
        "kind": "secret"
      },
      "at_c5": {
        "text": "侧房里有干涸的锈迹",
        "kind": "proof"
      },
      "at_c6": {
        "text": "宿舍里全是骷髅",
        "kind": "secret"
      },
      "at_c7": {
        "text": "餐桌上摆着六个人的份",
        "kind": "proof"
      },
      "at_c8": {
        "text": "下层甲板传来撞击声",
        "kind": "secret"
      },
      "at_c9": {
        "text": "储藏室里有船长的储物箱",
        "kind": "proof"
      },
      "amulet": {
        "text": "艾莉莎的护身符",
        "kind": "proof"
      },
      "bird_gone": {
        "text": "鸟妖被你赶走了",
        "kind": "proof",
        "excludes": [
          "amulet"
        ]
      }
    },
    "nodes": {
      "n_c1": {
        "title": "主甲板",
        "at": "主甲板",
        "kind": "clue",
        "requires": [],
        "grants": [
          "at_c1"
        ],
        "on_fail": [],
        "cost": "甲板上的东西会被风刮到海里",
        "depth": 1,
        "hint": "主甲板上全是海藻与骨头，比这艘船的残骸新得多。"
      },
      "n_c2": {
        "title": "前甲板",
        "at": "前甲板",
        "kind": "clue",
        "requires": [
          "at_c1"
        ],
        "grants": [
          "at_c2"
        ],
        "on_fail": [],
        "cost": "动过弩炮的人会留下脚印",
        "depth": 1,
        "hint": ""
      },
      "n_c3": {
        "title": "尾甲板",
        "at": "尾甲板",
        "kind": "clue",
        "requires": [
          "at_c1"
        ],
        "grants": [
          "at_c3"
        ],
        "on_fail": [],
        "cost": "撬开地板会惹来船上的东西",
        "depth": 1,
        "hint": ""
      },
      "n_c4": {
        "title": "船长室",
        "at": "船长室",
        "kind": "clue",
        "requires": [
          "at_c3"
        ],
        "grants": [
          "at_c4",
          "amulet"
        ],
        "on_fail": [],
        "cost": "读了日记的人会知道诅咒",
        "depth": 1,
        "hint": ""
      },
      "n_c5": {
        "title": "侧房",
        "at": "侧房",
        "kind": "clue",
        "requires": [
          "at_c4"
        ],
        "grants": [
          "at_c5"
        ],
        "on_fail": [],
        "cost": "侧房里有人待过",
        "depth": 1,
        "hint": ""
      },
      "n_c6": {
        "title": "船员宿舍",
        "at": "船员宿舍",
        "kind": "battle",
        "requires": [
          "at_c5"
        ],
        "grants": [
          "at_c6"
        ],
        "on_fail": [],
        "cost": "骷髅会记住砍过它们的人",
        "depth": 1,
        "hint": ""
      },
      "n_c7": {
        "title": "餐厅",
        "at": "餐厅",
        "kind": "clue",
        "requires": [
          "at_c6"
        ],
        "grants": [
          "at_c7"
        ],
        "on_fail": [],
        "cost": "动过餐桌的人会被船上的人看见",
        "depth": 1,
        "hint": ""
      },
      "n_c8": {
        "title": "下层甲板",
        "at": "下层甲板",
        "kind": "clue",
        "requires": [
          "at_c7"
        ],
        "grants": [
          "at_c8"
        ],
        "on_fail": [],
        "cost": "下层甲板的声音会一直跟着你",
        "depth": 1,
        "hint": ""
      },
      "n_c9": {
        "title": "储藏室",
        "at": "储藏室",
        "kind": "clue",
        "requires": [
          "at_c8"
        ],
        "grants": [
          "at_c9"
        ],
        "on_fail": [],
        "cost": "开箱子的声音会招来鸟妖",
        "depth": 2,
        "hint": ""
      },
      "n_c8b": {
        "title": "下层甲板",
        "at": "下层甲板",
        "kind": "battle",
        "requires": [
          "at_c9"
        ],
        "grants": [
          "bird_gone"
        ],
        "on_fail": [],
        "cost": "鸟妖在桅杆顶上叫",
        "depth": 2,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "把护身符带回修道院",
        "requires": [
          [
            "amulet"
          ],
          [
            "at_c4",
            "at_c9"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "在船上毁掉护身符",
        "requires": [
          [
            "amulet",
            "bird_gone"
          ],
          [
            "at_c9",
            "bird_gone"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "带着宝藏离开",
        "requires": [
          [
            "at_c9"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "鸟妖的返巢",
        "every": 3,
        "fires": "n_c8b"
      }
    ]
  }
}
```
