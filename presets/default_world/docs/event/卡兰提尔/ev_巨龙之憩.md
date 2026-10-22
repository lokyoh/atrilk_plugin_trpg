# 关了三日的门

```event-graph
{
  "id": "ev_巨龙之憩",
  "title": "关了三日的门",
  "duration_days": 3,
  "scale": "light",
  "stage": "风骸岛 · 巨龙之憩",
  "default_outcome": "茹娜拉会自己处理岛上的麻烦，队伍在修道院住下，然后各走各的路",
  "rumors": [
    "北边有船撞在礁上，已经好些天了。",
    "修道院的人管那船叫「罗盘玫瑰号」，没人敢上去。",
    "沉船上死过人，还不止一个。"
  ],
  "graph": {
    "conflict": "修道院主人茹娜拉在等你开口，而岛北边沉着一艘不沉的船",
    "scale": "light",
    "facts": {
      "at_a1": {
        "text": "撞见三个溺亡的水手",
        "kind": "secret"
      },
      "at_a2": {
        "text": "打水房的水桶有咸味",
        "kind": "proof"
      },
      "at_a3": {
        "text": "厨房的账本记着七个人",
        "kind": "proof"
      },
      "at_a4": {
        "text": "图书馆记着塔司这个月份",
        "kind": "secret"
      },
      "at_a5": {
        "text": "茹娜拉在这里化过人形",
        "kind": "secret"
      },
      "saved_both": {
        "text": "布立普与塔拉克都活了下来",
        "kind": "proof",
        "excludes": [
          "left_them"
        ]
      },
      "left_one": {
        "text": "你只救下了一个人",
        "kind": "debt"
      },
      "left_them": {
        "text": "你丢下他们走了",
        "kind": "debt"
      }
    },
    "nodes": {
      "n_a1": {
        "title": "登山路与修道院前厅",
        "at": "登山路与修道院前厅",
        "kind": "battle",
        "requires": [],
        "grants": [
          "at_a1"
        ],
        "on_fail": [],
        "cost": "你出手了，岛上的人会记住这张脸",
        "depth": 1,
        "hint": "你刚要离开海滩开始登山，身后传来哗啦啦的水声和湿漉漉的喊叫声——三个溺亡的水手就在你眼前三十尺外，灰着皮肤往外滴海水。"
      },
      "n_a1d": {
        "title": "登山路与修道院前厅",
        "at": "登山路与修道院前厅",
        "kind": "decision",
        "requires": [
          "at_a1"
        ],
        "grants": [
          "saved_both"
        ],
        "on_fail": [
          "left_one"
        ],
        "cost": "两条人命，你只来得及拽一个",
        "depth": 2,
        "hint": "两条人命：狗头人布立普和人类花匠塔拉克，你只来得及拽一个。"
      },
      "n_a1e": {
        "title": "登山路与修道院前厅",
        "at": "登山路与修道院前厅",
        "kind": "clue",
        "requires": [
          "at_a1"
        ],
        "grants": [
          "left_them"
        ],
        "on_fail": [],
        "cost": "转身走开是最省事的选择",
        "depth": 2,
        "hint": "这些水手走得慢，你可以转身就走——他们也不会跟上来。"
      },
      "n_a2": {
        "title": "打水房",
        "at": "打水房",
        "kind": "clue",
        "requires": [
          "at_a1"
        ],
        "grants": [
          "at_a2"
        ],
        "on_fail": [],
        "cost": "打水的人会记住有人动过水",
        "depth": 1,
        "hint": ""
      },
      "n_a3": {
        "title": "厨房",
        "at": "厨房",
        "kind": "clue",
        "requires": [
          "at_a1"
        ],
        "grants": [
          "at_a3"
        ],
        "on_fail": [],
        "cost": "厨房里的人开始躲着你",
        "depth": 1,
        "hint": ""
      },
      "n_a4": {
        "title": "图书馆",
        "at": "图书馆",
        "kind": "clue",
        "requires": [
          "at_a3"
        ],
        "grants": [
          "at_a4"
        ],
        "on_fail": [],
        "cost": "翻书的人会知道你来过",
        "depth": 2,
        "hint": ""
      },
      "n_a5": {
        "title": "巴哈姆特神殿",
        "at": "巴哈姆特神殿",
        "kind": "showdown",
        "requires": [
          "at_a2"
        ],
        "grants": [
          "at_a5"
        ],
        "on_fail": [],
        "cost": "在神殿里撒谎，茹娜拉会闻出来",
        "depth": 1,
        "hint": ""
      }
    },
    "convergences": [
      {
        "id": "c1",
        "title": "全身而退",
        "requires": [
          [
            "at_a5"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c2",
        "title": "救了人但被记住",
        "requires": [
          [
            "saved_both"
          ],
          [
            "left_one"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      },
      {
        "id": "c3",
        "title": "丢下他们走了",
        "requires": [
          [
            "left_them"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": ""
      }
    ],
    "clocks": [
      {
        "name": "岛上的水手",
        "every": 3,
        "fires": "n_a5"
      }
    ]
  }
}
```
