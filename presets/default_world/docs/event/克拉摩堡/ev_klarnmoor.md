# 克拉摩堡的王座

```event-graph
{
  "id": "ev_klarnmoor",
  "title": "克拉摩堡的王座",
  "duration_days": 2,
  "scale": "heavy",
  "stage": "剑湾 · 无冬森林里的克拉摩堡",
  "default_outcome": "队伍没能从碎颅部落手里拿到那张地图；但活着出来的人知道，克拉摩堡在无冬森林深处。",
  "rumors": [
    "碎颅部落的王住在最高的那座塔楼里，没人见过他出手。",
    "有个穿黑袍的女人跟碎颅首领在谈生意。",
    "三只大地精带着战利品回巢，路过的人都得让道。",
    "一只鹰头巨熊被关在塔里，门上装了门闩。"
  ],
  "graph": {
    "conflict": "碎颅部落的王格罗尔在克拉摩堡里，而刚铎·寻岩者和他那张地图就在王座厅",
    "scale": "heavy",
    "facts": {
      "km_sneak": {
        "text": "箭垛后的哨兵没在认真看",
        "kind": "secret"
      },
      "km_bluff": {
        "text": "你们假扮红标帮的信使混了进去",
        "kind": "secret"
      },
      "km_alarm": {
        "text": "守卫的吼声惊动了整座城堡",
        "kind": "debt"
      },
      "km_trap": {
        "text": "通往地精圣坛的路上有一根赤铜绊线",
        "kind": "proof"
      },
      "km_archer": {
        "text": "两个地精弓箭手守在箭缝后",
        "kind": "secret"
      },
      "km_gear": {
        "text": "储藏室里的锁甲和长剑属于修达",
        "kind": "proof"
      },
      "km_ogres": {
        "text": "大地精营房里的四个大地精想除掉熊地精自己上位",
        "kind": "secret"
      },
      "km_yegg": {
        "text": "宴会厅的厨子耶格一死，其余地精就会逃",
        "kind": "secret"
      },
      "km_idol": {
        "text": "黑暗大厅的火盆里藏着日精灵金像",
        "kind": "proof"
      },
      "km_question": {
        "text": "金像注入过预言魔法，只能问一个问题",
        "kind": "proof"
      },
      "km_bear": {
        "text": "枭熊被锁在塔楼里，门闩就是警告",
        "kind": "secret"
      },
      "km_bear_fed": {
        "text": "枭熊吃了肉，让开了路",
        "kind": "proof",
        "excludes": [
          "km_bear_corpse"
        ]
      },
      "km_bear_corpse": {
        "text": "枭熊的爪子卡在门框上",
        "kind": "proof",
        "excludes": [
          "km_bear_fed"
        ]
      },
      "km_gorol": {
        "text": "格罗尔君王是只四十五点生命的熊地精",
        "kind": "secret"
      },
      "km_vyris": {
        "text": "维耶里斯是变形怪，扮成女性卓尔的信使",
        "kind": "secret"
      },
      "km_gondo": {
        "text": "刚铎·寻岩者昏迷在王座厅的地上，生命值为零",
        "kind": "proof"
      },
      "km_map_lost": {
        "text": "维耶里斯带着地图逃向塔楼废墟",
        "kind": "debt",
        "excludes": [
          "km_map_taken"
        ]
      },
      "km_map_taken": {
        "text": "回声洞的地图在你们手里",
        "kind": "proof",
        "excludes": [
          "km_map_lost"
        ]
      },
      "km_saved": {
        "text": "刚铎被救活了",
        "kind": "proof",
        "excludes": [
          "km_dead"
        ]
      },
      "km_dead": {
        "text": "刚铎死在维耶里斯手里",
        "kind": "debt",
        "excludes": [
          "km_saved"
        ]
      },
      "km_party": {
        "text": "塔戈尔·血剑的返程战团在回路上等着",
        "kind": "secret"
      },
      "km_mercy": {
        "text": "塔戈尔放过了你们",
        "kind": "proof"
      },
      "km_cleared": {
        "text": "克拉摩堡被清空了",
        "kind": "proof"
      },
      "km_crown": {
        "text": "格罗尔的王冠和碎颅部落的信物",
        "kind": "proof"
      },
      "km_altar_gold": {
        "text": "圣坛上的圣杯座、刀和香炉",
        "kind": "proof"
      },
      "km_back_open": {
        "text": "南侧的铁门被撞开了",
        "kind": "secret"
      },
      "km_canvas": {
        "text": "塔楼废墟的帆布后面藏着暗门",
        "kind": "secret"
      }
    },
    "nodes": {
      "n_km_gate": {
        "title": "城堡入口",
        "at": "城堡入口",
        "kind": "clue",
        "requires": [],
        "grants": [
          "km_sneak"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "七座塔楼只剩底层还立着。箭垛后的哨兵偶尔往外瞥一眼，石门早已腐蚀倒塌。"
      },
      "n_km_bluff": {
        "title": "城堡入口",
        "at": "城堡入口",
        "kind": "decision",
        "requires": [
          "km_sneak"
        ],
        "grants": [
          "km_bluff"
        ],
        "on_fail": [
          "km_alarm"
        ],
        "cost": "穿着猩红斗篷、说是替黑蜘蛛办事——地精会当场核对人数",
        "depth": 2,
        "hint": "碎颅部落和红标帮都在为黑蜘蛛办事，宣称代表黑蜘蛛的使者通常能通行——但需要一次魅力（欺瞒）检定。"
      },
      "n_km_alarm": {
        "title": "陷阱大厅",
        "at": "陷阱大厅",
        "kind": "battle",
        "requires": [
          "km_sneak"
        ],
        "grants": [
          "km_trap"
        ],
        "on_fail": [],
        "cost": "地精的吼声会同时惊动大厅南北两侧的守卫",
        "depth": 1,
        "hint": "若哨兵先发现你们，地精与大地精会从南北两门同时冲出。通往地精圣坛的门前有根赤铜绊线，连着受难者天花板里的暗栓。"
      },
      "n_km_archers": {
        "title": "弓箭手岗哨",
        "at": "弓箭手岗哨",
        "kind": "battle",
        "requires": [
          "km_trap"
        ],
        "grants": [
          "km_archer"
        ],
        "on_fail": [],
        "cost": "两个弓箭手倒下，大厅里的地精会往这边看",
        "depth": 1,
        "hint": "两个地精轮流从箭缝射击掩体后的目标；近身后他们会拔出短弓。"
      },
      "n_km_store": {
        "title": "克拉摩堡储藏室",
        "at": "克拉摩堡储藏室",
        "kind": "clue",
        "requires": [
          "km_archer"
        ],
        "grants": [
          "km_gear"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "陈旧的腌肉桶和腐烂的谷物袋之间，有一件血迹斑斑的锁甲、一把重弩和一柄刻着无冬城徽章的长剑——修达的装备。"
      },
      "n_km_ogres": {
        "title": "大地精营房",
        "at": "大地精营房",
        "kind": "battle",
        "requires": [
          "km_gear"
        ],
        "grants": [
          "km_ogres"
        ],
        "on_fail": [],
        "cost": "大地精的武器架空了，剩下的人不会客气",
        "depth": 1,
        "hint": "四个大地精驻守此处，他们正盘算着除掉熊地精、自己接管部落。"
      },
      "n_km_banquet": {
        "title": "宴会厅",
        "at": "宴会厅",
        "kind": "battle",
        "requires": [
          "km_ogres"
        ],
        "grants": [
          "km_yegg"
        ],
        "on_fail": [],
        "cost": "厨子耶格一死，其余地精会朝东西两边逃开",
        "depth": 1,
        "hint": "七只地精和十二点生命值的首领耶格守着污秽的宴席；火盆里烧着炽热的煤。"
      },
      "n_km_dark": {
        "title": "黑暗大厅",
        "at": "黑暗大厅",
        "kind": "battle",
        "requires": [
          "km_yegg"
        ],
        "grants": [
          "km_idol"
        ],
        "on_fail": [],
        "cost": "穴居攫怪从雕像的壁架上跳下来",
        "depth": 1,
        "hint": "一只穴居攫怪藏在天使雕像的阴影里；火盆的煤下埋着一尊用深红布裹着的日精灵金像。"
      },
      "n_km_idol": {
        "title": "黑暗大厅",
        "at": "黑暗大厅",
        "kind": "clue",
        "requires": [
          "km_idol"
        ],
        "grants": [
          "km_question"
        ],
        "on_fail": [],
        "cost": "金像只能回答一个问题，问过就再也问不了",
        "depth": 2,
        "hint": "任何非邪恶生物握住金像，可以问一个问题，得到一次心灵感应的答复。"
      },
      "n_km_altar": {
        "title": "地精圣坛",
        "at": "地精圣坛",
        "kind": "battle",
        "requires": [
          "km_question"
        ],
        "grants": [
          "km_altar_gold"
        ],
        "on_fail": [],
        "cost": "圣坛上的金器归你们，邪教徒的名声也归你们",
        "depth": 1,
        "hint": "卢波和两个侍僧跪在马格鲁比耶的神龛前；若大厅那边先开打，他们会躲在祭坛后突袭。"
      },
      "n_km_back": {
        "title": "后门",
        "at": "后门",
        "kind": "clue",
        "requires": [
          "km_altar_gold"
        ],
        "grants": [
          "km_back_open"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "上锁的铁门通向古堡南侧的杂草丛小径；门闩很沉，但箭垛上没有人守。"
      },
      "n_km_tower": {
        "title": "塔楼废墟",
        "at": "塔楼废墟",
        "kind": "clue",
        "requires": [
          "km_back_open"
        ],
        "grants": [
          "km_canvas"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "北面藏着一道用帆布遮住的入口，DC 15 的察觉才能看穿它。"
      },
      "n_km_bear": {
        "title": "枭熊塔楼",
        "at": "枭熊塔楼",
        "kind": "encounter",
        "requires": [
          "km_canvas"
        ],
        "grants": [
          "km_bear"
        ],
        "on_fail": [],
        "cost": "门闩就是警告：门后有东西在等",
        "depth": 1,
        "hint": "三十尺高的中空筒仓里站着一只长着猫头鹰头的巨兽；扔给它新鲜肉块它就吃，否则它会攻击门口第一个生物。"
      },
      "n_km_bear_fed": {
        "title": "枭熊塔楼",
        "at": "枭熊塔楼",
        "kind": "clue",
        "requires": [
          "km_bear"
        ],
        "grants": [
          "km_bear_fed"
        ],
        "on_fail": [
          "km_bear_corpse"
        ],
        "cost": "一块肉换一条路",
        "depth": 2,
        "hint": "新鲜肉块能让枭熊松开爪牙；它吃饱了会从塔楼废墟逃走。"
      },
      "n_km_throne": {
        "title": "王之居所",
        "at": "王之居所",
        "kind": "battle",
        "requires": [
          "km_bear_fed"
        ],
        "grants": [
          "km_gorol",
          "km_vyris",
          "km_gondo",
          "km_cleared"
        ],
        "on_fail": [],
        "cost": "格罗尔国王挟持着刚铎；维耶里斯在门后等着",
        "depth": 1,
        "hint": "四十五点生命的熊地精格罗尔、十八点生命的狼斯纳尔，还有一个扮成女性卓尔的变形怪维耶里斯；刚铎昏迷在墙角。"
      },
      "n_km_bed": {
        "title": "王之居所",
        "at": "王之居所",
        "kind": "decision",
        "requires": [
          "km_gorol"
        ],
        "grants": [
          "km_map_taken"
        ],
        "on_fail": [
          "km_map_lost"
        ],
        "cost": "床垫底下的麻袋，还是门口那条逃走的影子——只来得及做一件",
        "depth": 2,
        "hint": "刚铎的地图藏在格罗尔的床垫下；但格罗尔一死，维耶里斯就会杀掉刚铎、带着地图逃向塔楼废墟。"
      },
      "n_km_rescue": {
        "title": "王之居所",
        "at": "王之居所",
        "kind": "clue",
        "requires": [
          "km_gondo"
        ],
        "grants": [
          "km_saved"
        ],
        "on_fail": [
          "km_dead"
        ],
        "cost": "先救他还是先拿地图，时间只够做一件事",
        "depth": 3,
        "hint": "刚铎生命值为零，昏迷不醒；没有地图他醒过来也不会走。"
      },
      "n_km_party": {
        "title": "塔楼废墟",
        "at": "塔楼废墟",
        "kind": "encounter",
        "requires": [
          "km_cleared"
        ],
        "grants": [
          "km_party"
        ],
        "on_fail": [],
        "cost": "三只大地精加两匹狼堵在回去的路上",
        "depth": 1,
        "hint": "塔戈尔·血剑带着返程的大地精战团撞上你们；他的袋子里装着砍下来的精灵头颅。DC 15 的游说也许能让他放你们一马。"
      },
      "n_km_mercy": {
        "title": "塔楼废墟",
        "at": "塔楼废墟",
        "kind": "clue",
        "requires": [
          "km_party"
        ],
        "grants": [
          "km_mercy"
        ],
        "on_fail": [],
        "cost": "塔戈尔一直渴望当上峭壁巨魔部落的领袖",
        "depth": 3,
        "hint": "你们若能让他相信你们也是来夺位的，他也许会把头颅留下。"
      },
      "n_km_crown": {
        "title": "王之居所",
        "at": "王之居所",
        "kind": "clue",
        "requires": [
          "km_gorol"
        ],
        "grants": [
          "km_crown"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "格罗尔的王冠压在火盆边，碎颅部落的信物也在他身上。"
      }
    },
    "convergences": [
      {
        "id": "c_km_map",
        "title": "回声洞的地图到手了",
        "requires": [
          [
            "km_map_taken"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "地图上标着回声洞的入口。刚铎在队伍身边——这一次，他有理由跟你们回去了。"
      },
      {
        "id": "c_km_saved",
        "title": "刚铎活着被救出来了",
        "requires": [
          [
            "km_saved"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "刚铎被救活了。没有地图他不肯走，但至少他睁开了眼。"
      },
      {
        "id": "c_km_crown",
        "title": "带走了碎颅的王冠",
        "requires": [
          [
            "km_crown"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "格罗尔的王冠在行囊里。碎颅部落会另找首领——但不会找得那么快。"
      },
      {
        "id": "c_km_mercy",
        "title": "塔戈尔放过了你们",
        "requires": [
          [
            "km_mercy"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "大地精战团让开一条路。塔戈尔想要的不是你们的命，是峭壁巨魔部落的位子。"
      },
      {
        "id": "c_km_asked",
        "title": "问掉了金像上的那个问题",
        "requires": [
          [
            "km_question"
          ]
        ],
        "kind": "turn",
        "rank": 2,
        "outcome": "日精灵金像给了一个答复，然后再也不肯出声。答案指向别处——回声洞之外，还有别的副本。"
      }
    ],
    "clocks": [
      {
        "name": "维耶里斯的毒手",
        "every": 3,
        "fires": "n_km_bed"
      }
    ]
  }
}
```
