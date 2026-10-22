# 会认孢子的守卫

```event-graph
{
  "id": "ev_海藻洞穴",
  "title": "会认孢子的守卫",
  "duration_days": 4,
  "scale": "medium",
  "stage": "风骸岛 · 海藻洞穴",
  "default_outcome": "守卫继续守着蕈人的地盘，塔拉克等不到那批蘑菇",
  "rumors": [
    "洞里的蘑菇最近不太对劲。",
    "塔拉克说蕈人造了个八爪的东西守着洞口。",
    "守洞的那东西认得孢子——你手里有孢子，它就不动你。"
  ],
  "graph": {
    "conflict": "蕈人被真菌守卫挡住，而守卫只认孢子的规矩",
    "scale": "medium",
    "facts": {
      "at_b1": {
        "text": "隧道口的蘑菇是活的",
        "kind": "proof"
      },
      "at_b2": {
        "text": "真菌农场里有八爪鱼守卫",
        "kind": "secret"
      },
      "at_b3": {
        "text": "储藏室里有红玉肚菌",
        "kind": "proof"
      },
      "at_b4": {
        "text": "议事圆厅空着",
        "kind": "secret"
      },
      "at_b5": {
        "text": "塞娜萨中了毒烟",
        "kind": "secret"
      },
      "at_b6": {
        "text": "橙色水晶在渗毒",
        "kind": "proof"
      },
      "spore_talk": {
        "text": "你用通念孢子和蕈人谈上了",
        "kind": "proof"
      },
      "broke_crystal": {
        "text": "你砸碎了橙色水晶",
        "kind": "debt",
        "excludes": [
          "ally"
        ]
      },
      "ally": {
        "text": "塞娜萨答应与你为敌",
        "kind": "proof"
      }
    },
    "nodes": {
      "n_b1": {
        "title": "入口隧道",
        "at": "入口隧道",
        "kind": "clue",
        "requires": [],
        "grants": [
          "at_b1"
        ],
        "on_fail": [],
        "cost": "洞口的水手会知道你进过洞",
        "depth": 1,
        "hint": "隧道口的蘑菇是活的：它们在你靠近时收拢了伞盖。"
      },
      "n_b2": {
        "title": "真菌农场",
        "at": "真菌农场",
        "kind": "battle",
        "requires": [
          "at_b1"
        ],
        "grants": [
          "at_b2"
        ],
        "on_fail": [],
        "cost": "守卫会记住你的气味",
        "depth": 1,
        "hint": ""
      },
      "n_b2p": {
        "title": "真菌农场",
        "at": "真菌农场",
        "kind": "clue",
        "requires": [
          "at_b1"
        ],
        "grants": [
          "spore_talk"
        ],
        "on_fail": [],
        "cost": "通念孢子让蕈人愿意听你说",
        "depth": 2,
        "hint": ""
      },
      "n_b3": {
        "title": "食品储藏室",
        "at": "食品储藏室",
        "kind": "clue",
        "requires": [
          "at_b2",
          "spore_talk"
        ],
        "grants": [
          "at_b3"
        ],
        "on_fail": [],
        "cost": "拿了蘑菇，蕈人未必同意",
        "depth": 1,
        "hint": ""
      },
      "n_b4": {
        "title": "议事圆厅",
        "at": "议事圆厅",
        "kind": "clue",
        "requires": [
          "at_b3"
        ],
        "grants": [
          "at_b4"
        ],
        "on_fail": [],
        "cost": "空着的圆厅会有人记得",
        "depth": 1,
        "hint": ""
      },
      "n_b5": {
        "title": "塞娜萨圣殿",
        "at": "塞娜萨圣殿",
        "kind": "showdown",
        "requires": [
          "at_b3"
        ],
        "grants": [
          "at_b5"
        ],
        "on_fail": [],
        "cost": "救不回塞娜萨，你就得带着消息走",
        "depth": 1,
        "hint": ""
      },
      "n_b6": {
        "title": "水晶洞穴",
        "at": "水晶洞穴",
        "kind": "battle",
        "requires": [
          "at_b4"
        ],
        "grants": [
          "at_b6",
          "broke_crystal"
        ],
        "on_fail": [],
        "cost": "碎水晶时洞里的蕈人都在看着",
        "depth": 1,
        "hint": ""
      },
      "n_b5b": {
        "title": "塞娜萨圣殿",
        "at": "塞娜萨圣殿",
        "kind": "clue",
        "requires": [
          "at_b4",
          "spore_talk"
        ],
        "grants": [
          "ally"
        ],
        "on_fail": [],
        "cost": "谈成了，塞娜萨愿意配合",
        "depth": 2,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "救回塞娜萨",
        "requires": [
          [
            "at_b5"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "与蕈人结盟",
        "requires": [
          [
            "ally"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "带着消息撤出去",
        "requires": [
          [
            "at_b1",
            "at_b3"
          ],
          [
            "at_b1",
            "spore_talk"
          ]
        ],
        "kind": "turn",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "潮汐",
        "every": 4,
        "fires": "n_b5"
      }
    ]
  }
}
```
