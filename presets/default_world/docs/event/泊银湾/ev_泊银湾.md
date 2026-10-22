# 夹层翻出来的那件

```event-graph
{
  "id": "ev_泊银湾",
  "title": "夹层翻出来的那件",
  "duration_days": 3,
  "scale": "light",
  "stage": "泊银湾一带",
  "default_outcome": "货栈找到下一个顶罪的人，你带着没洗清的名声离开渡口",
  "rumors": [
    "渡口的查验这两天天天翻行李。",
    "翻出来的东西值不少——听说是一箱精铁。",
    "有个外乡人已经替人认下了一箱，现在还在浮屋里。"
  ],
  "graph": {
    "conflict": "渡口的查验翻出一件没报关的货，而它现在算在你头上",
    "scale": "light",
    "facts": {
      "in_hold": {
        "text": "你被扣在货栈的浮屋等讯后开",
        "kind": "debt"
      },
      "found": {
        "text": "你听说有人认下了一箱",
        "kind": "secret"
      },
      "ledger": {
        "text": "渡口的过关簿上有另一个名字",
        "kind": "proof"
      },
      "witness": {
        "text": "那个替你认下的人肯开口了",
        "kind": "debt"
      },
      "told": {
        "text": "你把货栈的事捅到了账房",
        "kind": "proof",
        "excludes": [
          "found"
        ]
      },
      "cleared": {
        "text": "查验官当众撤了扣押",
        "kind": "proof"
      }
    },
    "nodes": {
      "n1": {
        "title": "夹层翻出来的那件",
        "at": "渡鸦渡口",
        "kind": "clue",
        "requires": [],
        "grants": [
          "in_hold"
        ],
        "on_fail": [],
        "cost": "扣你的人会记住这张脸",
        "depth": 1,
        "hint": "查验官把你的行李翻了个底朝天，从夹层里扯出一件没报关的货，当场扣下：封条上的日子是三天前，可这箱子昨天才到你手上。渡口要你扣在浮屋里等开。"
      },
      "n2": {
        "title": "渡鸦渡口",
        "at": "渡鸦渡口",
        "kind": "encounter",
        "requires": [
          "in_hold"
        ],
        "grants": [
          "found"
        ],
        "on_fail": [],
        "cost": "认下那件事的人会欠你一次",
        "depth": 1,
        "hint": ""
      },
      "n3": {
        "title": "渡鸦渡口",
        "at": "渡鸦渡口",
        "kind": "clue",
        "requires": [
          "found"
        ],
        "grants": [
          "ledger"
        ],
        "on_fail": [],
        "cost": "翻过关簿的人会被账房盯上",
        "depth": 2,
        "hint": ""
      },
      "n4": {
        "title": "渡鸦渡口",
        "at": "渡鸦渡口",
        "kind": "battle",
        "requires": [
          "in_hold"
        ],
        "grants": [
          "witness"
        ],
        "on_fail": [],
        "cost": "跟浮屋里的人硬碰，结案的人就得出面",
        "depth": 2,
        "hint": ""
      },
      "n5": {
        "title": "渡鸦渡口",
        "at": "渡鸦渡口",
        "kind": "decision",
        "requires": [
          "ledger"
        ],
        "grants": [
          "told"
        ],
        "on_fail": [
          "cleared"
        ],
        "cost": "把货栈的事捅出去，得罪的是整条街",
        "depth": 2,
        "hint": ""
      },
      "n6": {
        "title": "渡鸦渡口",
        "at": "渡鸦渡口",
        "kind": "clue",
        "requires": [
          "told"
        ],
        "grants": [
          "cleared"
        ],
        "on_fail": [],
        "cost": "账房记你的名字，一辈子都在",
        "depth": 3,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "当众撤了扣押",
        "requires": [
          [
            "cleared"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "认下另一件事的人欠你一次",
        "requires": [
          [
            "witness"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "带着没洗清的名声离开",
        "requires": [
          [
            "in_hold"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "浮屋的汛期",
        "every": 3,
        "fires": "n2"
      }
    ]
  }
}
```
