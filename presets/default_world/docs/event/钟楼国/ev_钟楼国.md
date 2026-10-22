# 关了三日的门

```event-graph
{
  "id": "ev_钟楼国",
  "title": "关了三日的门",
  "duration_days": 3,
  "scale": "heavy",
  "stage": "钟楼国一带",
  "default_outcome": "门照旧关着，大祭司照旧敲他的钟，你被记成不敬",
  "rumors": [
    "台心的钟今天没敲。",
    "圣殿的门关了三天了，没人敢问为什么。",
    "地窖里传出诵经声，可地窖早已封了。"
  ],
  "graph": {
    "conflict": "大祭司要你别问灰是谁落的，而圣殿的门已经关了三日",
    "scale": "heavy",
    "facts": {
      "shut": {
        "text": "圣殿的门关了三日",
        "kind": "proof"
      },
      "hush": {
        "text": "广场上没人说话",
        "kind": "secret"
      },
      "crypt": {
        "text": "地窖里有诵经声",
        "kind": "secret"
      },
      "censer": {
        "text": "炉心的军械箱子刚到",
        "kind": "proof"
      },
      "named": {
        "text": "大祭司叫出了你的名字",
        "kind": "secret",
        "excludes": [
          "ashen"
        ]
      },
      "ashen": {
        "text": "你在灰里闻到了别的东西",
        "kind": "proof"
      }
    },
    "nodes": {
      "n1": {
        "title": "关了三日的门",
        "at": "钟楼国圣灰广场",
        "kind": "clue",
        "requires": [],
        "grants": [
          "shut"
        ],
        "on_fail": [],
        "cost": "问门的人会被守门人记住",
        "depth": 1,
        "hint": "圣殿的门已经关了三日。守门人说你欠祭礼，可你没进过这座城。"
      },
      "n2": {
        "title": "钟楼国圣灰广场",
        "at": "钟楼国圣灰广场",
        "kind": "encounter",
        "requires": [
          "shut"
        ],
        "grants": [
          "hush"
        ],
        "on_fail": [],
        "cost": "广场上失言的人都记着",
        "depth": 1,
        "hint": ""
      },
      "n3": {
        "title": "灰门",
        "at": "灰门",
        "kind": "clue",
        "requires": [
          "hush"
        ],
        "grants": [
          "crypt"
        ],
        "on_fail": [],
        "cost": "地窖的封条撕不得",
        "depth": 2,
        "hint": ""
      },
      "n4": {
        "title": "钟楼国圣灰广场",
        "at": "钟楼国圣灰广场",
        "kind": "clue",
        "requires": [
          "hush"
        ],
        "grants": [
          "censer"
        ],
        "on_fail": [],
        "cost": "碰守灰军的箱子会被记下",
        "depth": 2,
        "hint": ""
      },
      "n5": {
        "title": "圣殿地窖",
        "at": "圣殿地窖",
        "kind": "battle",
        "requires": [
          "crypt"
        ],
        "grants": [
          "ashen"
        ],
        "on_fail": [],
        "cost": "地窖里的东西不怕人",
        "depth": 2,
        "hint": ""
      },
      "n6": {
        "title": "灰门",
        "at": "灰门",
        "kind": "encounter",
        "requires": [
          "censer"
        ],
        "grants": [
          "hush"
        ],
        "on_fail": [],
        "cost": "守门人不是人",
        "depth": 2,
        "hint": ""
      },
      "n7": {
        "title": "钟楼国圣灰广场",
        "at": "钟楼国圣灰广场",
        "kind": "battle",
        "requires": [
          "crypt"
        ],
        "grants": [
          "ashen"
        ],
        "on_fail": [],
        "cost": "广场上动手，灰钟会一直敲",
        "depth": 3,
        "hint": ""
      },
      "n8": {
        "title": "灰门",
        "at": "灰门",
        "kind": "battle",
        "requires": [
          "censer"
        ],
        "grants": [
          "named"
        ],
        "on_fail": [],
        "cost": "守门人不是人",
        "depth": 3,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "你在灰里闻到了别的东西",
        "requires": [
          [
            "ashen"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "大祭司叫出了你的名字",
        "requires": [
          [
            "named"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "门照旧关着",
        "requires": [
          [
            "shut"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "晨昏的灰钟",
        "every": 3,
        "fires": "n6"
      }
    ]
  }
}
```
