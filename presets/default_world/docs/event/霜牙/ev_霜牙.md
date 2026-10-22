# 被认出来的那一刻

```event-graph
{
  "id": "ev_霜牙",
  "title": "被认出来的那一刻",
  "duration_days": 3,
  "scale": "medium",
  "stage": "霜牙一带",
  "default_outcome": "哨站把你当成那个失踪的人押了一程，发现不对才松手",
  "rumors": [
    "霜牙岭的哨站最近在盘查过路人。",
    "有人半年前在这儿失踪了，名字还留在册上。",
    "部盟的人不缺马，缺的是能替他们干活的手。"
  ],
  "graph": {
    "conflict": "哨站的人叫得出你的名字——可你从未来过这里",
    "scale": "medium",
    "facts": {
      "named": {
        "text": "哨站的人叫出了你的名字",
        "kind": "secret"
      },
      "book": {
        "text": "七号池底记着上一个管事",
        "kind": "proof"
      },
      "camp": {
        "text": "断齿关的犬养着认人的本事",
        "kind": "proof"
      },
      "gone": {
        "text": "上一个管事已经没了",
        "kind": "secret"
      },
      "freed": {
        "text": "你被放出了哨站",
        "kind": "proof",
        "excludes": [
          "gone"
        ]
      },
      "mercy": {
        "text": "犬认错了人，你欠它一条命",
        "kind": "debt"
      }
    },
    "nodes": {
      "n1": {
        "title": "被认出来的那一刻",
        "at": "霜牙哨站",
        "kind": "encounter",
        "requires": [],
        "grants": [
          "named"
        ],
        "on_fail": [],
        "cost": "被认错的人得先站一会儿",
        "depth": 1,
        "hint": "哨兵伸手拦住你，把你认错成了另一个人，叫出了那个名字——那不是你告诉他的，册子上写着，是半年前登的记。"
      },
      "n2": {
        "title": "霜牙哨站",
        "at": "霜牙哨站",
        "kind": "clue",
        "requires": [
          "named"
        ],
        "grants": [
          "book"
        ],
        "on_fail": [],
        "cost": "翻池册的人会被哨站的狗盯上",
        "depth": 1,
        "hint": ""
      },
      "n3": {
        "title": "断齿关",
        "at": "断齿关",
        "kind": "battle",
        "requires": [
          "book"
        ],
        "grants": [
          "camp"
        ],
        "on_fail": [],
        "cost": "闯营的人得先过犬群",
        "depth": 2,
        "hint": ""
      },
      "n4": {
        "title": "断齿关",
        "at": "断齿关",
        "kind": "clue",
        "requires": [
          "camp"
        ],
        "grants": [
          "gone"
        ],
        "on_fail": [],
        "cost": "问得太细就走不脱身",
        "depth": 2,
        "hint": ""
      },
      "n5": {
        "title": "霜牙哨站",
        "at": "霜牙哨站",
        "kind": "decision",
        "requires": [
          "named"
        ],
        "grants": [
          "freed"
        ],
        "on_fail": [
          "gone"
        ],
        "cost": "认下那个名字，你就欠了另一个名字",
        "depth": 2,
        "hint": ""
      },
      "n6": {
        "title": "霜牙哨站",
        "at": "霜牙哨站",
        "kind": "battle",
        "requires": [
          "camp"
        ],
        "grants": [
          "mercy"
        ],
        "on_fail": [],
        "cost": "犬群的牙可不认人",
        "depth": 2,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "你被放出了哨站",
        "requires": [
          [
            "freed"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "犬认错了人",
        "requires": [
          [
            "mercy"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "顶着别人的名字过冬",
        "requires": [
          [
            "gone"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "哨站的换班",
        "every": 3,
        "fires": "n3"
      }
    ]
  }
}
```
