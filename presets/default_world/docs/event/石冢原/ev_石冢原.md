# 砸在你脚边的那个人

```event-graph
{
  "id": "ev_石冢原",
  "title": "砸在你脚边的那个人",
  "duration_days": 4,
  "scale": "heavy",
  "stage": "石冢原一带",
  "default_outcome": "行会们照旧轮流坐庄，天坑底下的节拍一夜没停",
  "rumors": [
    "磨盘镇的节拍这几天慢了半拍。",
    "北边天坑里传出金属声，三夜没断。",
    "七十二眼最近封了两回，回回都是行会自己封的。"
  ],
  "graph": {
    "conflict": "天坑底下的东西在按磨盘的节拍敲，而七个采石行会都在装没听见",
    "scale": "heavy",
    "facts": {
      "seen": {
        "text": "一个人倒在你脚边，从下面爬上来的",
        "kind": "secret"
      },
      "beat": {
        "text": "天坑的敲击和磨盘一个节拍",
        "kind": "secret"
      },
      "ash": {
        "text": "他指甲缝里是灰，不是土",
        "kind": "proof"
      },
      "sealed": {
        "text": "七十二眼被行会封了",
        "kind": "secret"
      },
      "opened": {
        "text": "封口被撬开了",
        "kind": "debt",
        "excludes": [
          "sealed"
        ]
      },
      "guild": {
        "text": "行会知道你动了封口",
        "kind": "debt"
      }
    },
    "nodes": {
      "n1": {
        "title": "砸在你脚边的那个人",
        "at": "磨盘镇",
        "kind": "encounter",
        "requires": [],
        "grants": [
          "seen"
        ],
        "on_fail": [],
        "cost": "撞见这种事的人就别想安生走",
        "depth": 1,
        "hint": "一个人从北边天坑的方向走过来，在你脚边三尺外倒下。他的指甲缝里是灰，不是土——那是从下面爬上来的。"
      },
      "n2": {
        "title": "磨盘镇",
        "at": "磨盘镇",
        "kind": "clue",
        "requires": [
          "seen"
        ],
        "grants": [
          "beat"
        ],
        "on_fail": [],
        "cost": "打听节拍的人会被当成疯子",
        "depth": 1,
        "hint": ""
      },
      "n3": {
        "title": "七十二眼",
        "at": "七十二眼",
        "kind": "clue",
        "requires": [
          "beat"
        ],
        "grants": [
          "ash"
        ],
        "on_fail": [],
        "cost": "下坑的人得自己带绳",
        "depth": 2,
        "hint": ""
      },
      "n4": {
        "title": "七十二眼",
        "at": "七十二眼",
        "kind": "battle",
        "requires": [
          "ash"
        ],
        "grants": [
          "sealed"
        ],
        "on_fail": [],
        "cost": "封口前守着的不是人",
        "depth": 2,
        "hint": ""
      },
      "n5": {
        "title": "万人磨盘",
        "at": "万人磨盘",
        "kind": "clue",
        "requires": [
          "sealed"
        ],
        "grants": [
          "opened"
        ],
        "on_fail": [],
        "cost": "撬封口会被磨盘镇的行会记住",
        "depth": 2,
        "hint": ""
      },
      "n6": {
        "title": "万人磨盘",
        "at": "万人磨盘",
        "kind": "decision",
        "requires": [
          "opened"
        ],
        "grants": [
          "guild"
        ],
        "on_fail": [
          "opened"
        ],
        "cost": "认下撬封口，你就欠了七个行会",
        "depth": 2,
        "hint": ""
      },
      "n7": {
        "title": "七十二眼",
        "at": "七十二眼",
        "kind": "clue",
        "requires": [
          "opened"
        ],
        "grants": [
          "ash"
        ],
        "on_fail": [],
        "cost": "下过坑的人身上会带灰",
        "depth": 3,
        "hint": ""
      },
      "n8": {
        "title": "万人磨盘",
        "at": "万人磨盘",
        "kind": "battle",
        "requires": [
          "opened"
        ],
        "grants": [
          "sealed"
        ],
        "on_fail": [],
        "cost": "行会来的人不讲道理",
        "depth": 3,
        "hint": ""
      },
      "n9": {
        "title": "七十二眼",
        "at": "七十二眼",
        "kind": "battle",
        "requires": [
          "sealed"
        ],
        "grants": [
          "beat"
        ],
        "on_fail": [],
        "cost": "坑底那东西还没走",
        "depth": 3,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "你撬开了封口",
        "requires": [
          [
            "opened"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "行会记下了你的名字",
        "requires": [
          [
            "guild"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "封口照旧封着",
        "requires": [
          [
            "sealed"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "天坑的敲击",
        "every": 3,
        "fires": "n4"
      }
    ]
  }
}
```
