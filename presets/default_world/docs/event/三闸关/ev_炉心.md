# 递上那件裂了的家伙

```event-graph
{
  "id": "ev_炉心",
  "title": "递上那件裂了的家伙",
  "duration_days": 4,
  "scale": "medium",
  "stage": "炉心一带",
  "default_outcome": "炉主们照旧互相别着劲儿，你那件裂了的家伙还是裂着",
  "rumors": [
    "千炉塔底下又塌了一角。",
    "七层那一炉的料出了岔子，谁也不肯认。",
    "炉心这阵子对过路的查得紧，工具坏了别想下炉。"
  ],
  "graph": {
    "conflict": "四炉主互不通婚，却都盯着同一炉新料",
    "scale": "medium",
    "facts": {
      "crack": {
        "text": "你那件家伙的裂口是新的",
        "kind": "secret"
      },
      "material": {
        "text": "七层那一炉的料能补它",
        "kind": "proof"
      },
      "order": {
        "text": "炉主的军械订单压在前面",
        "kind": "debt"
      },
      "debt": {
        "text": "你欠炉主李砧一笔",
        "kind": "debt"
      },
      "named": {
        "text": "有人替你认下了那炉料",
        "kind": "proof",
        "excludes": [
          "order"
        ]
      },
      "tool": {
        "text": "你那件家伙修好了",
        "kind": "proof"
      }
    },
    "nodes": {
      "n1": {
        "title": "递上那件裂了的家伙",
        "at": "三闸关",
        "kind": "clue",
        "requires": [],
        "grants": [
          "crack"
        ],
        "on_fail": [],
        "cost": "带着一件坏家伙进炉心，谁都看得出来",
        "depth": 1,
        "hint": "你那件家伙的裂口是新的：茬口发亮，还没来得及锈。补它要用七层那一炉的料——你这件家伙只有这儿能修，而炉心不外借。"
      },
      "n2": {
        "title": "千炉塔",
        "at": "千炉塔",
        "kind": "clue",
        "requires": [
          "crack"
        ],
        "grants": [
          "material"
        ],
        "on_fail": [],
        "cost": "问炉主要价，就欠了他一句话",
        "depth": 1,
        "hint": ""
      },
      "n3": {
        "title": "千炉塔",
        "at": "千炉塔",
        "kind": "encounter",
        "requires": [
          "material"
        ],
        "grants": [
          "order"
        ],
        "on_fail": [],
        "cost": "插队的会被三闸关记住",
        "depth": 2,
        "hint": ""
      },
      "n4": {
        "title": "三闸关",
        "at": "三闸关",
        "kind": "battle",
        "requires": [
          "order"
        ],
        "grants": [
          "debt"
        ],
        "on_fail": [],
        "cost": "欠账的人动手会更狠",
        "depth": 2,
        "hint": ""
      },
      "n5": {
        "title": "千炉塔",
        "at": "千炉塔",
        "kind": "clue",
        "requires": [
          "order"
        ],
        "grants": [
          "named"
        ],
        "on_fail": [],
        "cost": "认下那炉料的人会被同行盯上",
        "depth": 2,
        "hint": ""
      },
      "n6": {
        "title": "千炉塔",
        "at": "千炉塔",
        "kind": "battle",
        "requires": [
          "named"
        ],
        "grants": [
          "tool"
        ],
        "on_fail": [],
        "cost": "炉口的火不认人",
        "depth": 2,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "你那件家伙修好了",
        "requires": [
          [
            "tool"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "有人替你认下",
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
        "title": "带着修不好的家伙离开",
        "requires": [
          [
            "crack"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "炉火的火候",
        "every": 3,
        "fires": "n3"
      }
    ]
  }
}
```
