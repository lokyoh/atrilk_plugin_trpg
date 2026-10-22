# 雷树废墟的龙之邪教徒

```event-graph
{
  "id": "ev_spider",
  "title": "雷树废墟的龙之邪教徒",
  "duration_days": 3,
  "scale": "medium",
  "stage": "剑湾 · 凡达林近郊与雷树废墟",
  "default_outcome": "队伍把雷树废墟丢在身后，往北边的无冬森林走；德鲁伊雷多斯说，他会在废墟里等着。",
  "rumors": [
    "凡达林东北的山区有人在挖掘，探矿者被不死生物赶了回来。",
    "飞龙突岩上的兽人斥候在劫旅人，镇长出了 100 金币的赏格。",
    "三猪小径尽头的废墟里，有人在跟一条绿龙谈判。",
    "达朗·埃德玛在打听古枭井；他从不白给消息。",
    "报丧妖阿加莎只回答一个问题——谁问对了她就说什么。"
  ],
  "graph": {
    "conflict": "镇上的五件差事都指向北边：无冬森林边缘的雷树废墟，一条绿龙占着那里",
    "scale": "medium",
    "facts": {
      "sp_tasks": {
        "text": "镇上五个人各派了一件差事",
        "kind": "secret"
      },
      "sp_hammon": {
        "text": "哈蒙·科斯特在古枭井，用死灵术占着塔楼",
        "kind": "secret"
      },
      "sp_hammon_want": {
        "text": "科斯特想知道建造古枭井的法师叫什么名字",
        "kind": "secret"
      },
      "sp_ashinore": {
        "text": "阿辛多尔——建造古枭井的法师",
        "kind": "secret"
      },
      "sp_pact": {
        "text": "科斯特收下了答案，赏给你们一枚耐瑟瑞尔的防护戒指",
        "kind": "proof"
      },
      "sp_agatha": {
        "text": "阿加莎只肯回答一个问题",
        "kind": "secret"
      },
      "sp_comb": {
        "text": "加莱尔修女的镶银珠宝梳子",
        "kind": "proof"
      },
      "sp_asked": {
        "text": "你们用一个问题换来了一个答案",
        "kind": "secret",
        "excludes": [
          "sp_asked_twice"
        ]
      },
      "sp_asked_twice": {
        "text": "阿加莎不再回答第二个问题",
        "kind": "debt",
        "excludes": [
          "sp_asked"
        ]
      },
      "sp_orcs_cleared": {
        "text": "飞龙突岩的兽人营地被清空了",
        "kind": "proof"
      },
      "sp_thundertree": {
        "text": "雷多斯知道克拉摩堡和回声洞的位置",
        "kind": "secret"
      },
      "sp_druid": {
        "text": "德鲁伊雷多斯愿意为队伍带路",
        "kind": "secret"
      },
      "sp_ashen": {
        "text": "灰烬丧尸第一次挨打会把灰烬扬向周围",
        "kind": "secret"
      },
      "sp_coin": {
        "text": "枯萎小屋石板下埋着一只装满钱币的旧箱子",
        "kind": "proof"
      },
      "sp_goldgreen": {
        "text": "草药店储藏架下那条镶祖母绿的项链",
        "kind": "proof"
      },
      "sp_heirloom": {
        "text": "米尔娜的传家宝在草药店——她一家曾从这里逃出",
        "kind": "secret"
      },
      "sp_tooth": {
        "text": "毒牙盘踞在龙之塔，箱底的「砍伐者」专砍植物",
        "kind": "proof"
      },
      "sp_cultists": {
        "text": "龙之邪教徒想拿你们当贡品献给绿龙",
        "kind": "secret"
      },
      "sp_cleared": {
        "text": "雷树废墟被清空了",
        "kind": "proof"
      }
    },
    "nodes": {
      "n_sp_hall": {
        "title": "镇长大厅",
        "at": "镇长大厅",
        "kind": "clue",
        "requires": [],
        "grants": [
          "sp_tasks"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "镇长哈宾·威斯特的告示板上贴着「奖励——飞龙突岩附近的兽人」；修达·霍温特在等你们带回伊阿诺的消息。"
      },
      "n_sp_hammon": {
        "title": "古枭井",
        "at": "古枭井",
        "kind": "encounter",
        "requires": [
          "sp_tasks"
        ],
        "grants": [
          "sp_hammon",
          "sp_hammon_want"
        ],
        "on_fail": [],
        "cost": "塔楼里全是他们拼起来的骨头，法师不会请你们喝茶",
        "depth": 1,
        "hint": "十二只丧尸潜伏在瞭望塔废墟里；戴红袍、额上有死灵学派刺青的哈蒙·科斯特愿意做交易——他想知道建塔的法师叫什么名字。"
      },
      "n_sp_agatha": {
        "title": "报丧妖的庇护所",
        "at": "报丧妖的庇护所",
        "kind": "encounter",
        "requires": [
          "sp_tasks"
        ],
        "grants": [
          "sp_agatha"
        ],
        "on_fail": [],
        "cost": "报丧妖的耐心比金币薄，答错话她会直接消失",
        "depth": 1,
        "hint": "枝条编织的穹顶庇护所里，苍白冰冷的光化作一位女性精灵。她肯听一个奉承的问题，但只答一个。"
      },
      "n_sp_comb": {
        "title": "幸运圣坛",
        "at": "幸运圣坛",
        "kind": "clue",
        "requires": [
          "sp_tasks"
        ],
        "grants": [
          "sp_comb"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "侍僧加莱尔修女要人带一件合适的礼物去见报丧妖：她那把镶银的珠宝梳子。她出三瓶治疗药水作酬劳。"
      },
      "n_sp_ask": {
        "title": "报丧妖的庇护所",
        "at": "报丧妖的庇护所",
        "kind": "decision",
        "requires": [
          "sp_agatha",
          "sp_comb"
        ],
        "grants": [
          "sp_asked"
        ],
        "on_fail": [
          "sp_asked_twice"
        ],
        "cost": "问错问题，或者问第二个问题，就都没有第二个答案了",
        "depth": 2,
        "hint": "梳子能换一次自动成功的说服。她能答：博恩格尔法术书的下落、克拉摩堡的位置、回声洞的位置、黑蜘蛛的身份——只能挑一样。"
      },
      "n_sp_ashinore": {
        "title": "报丧妖的庇护所",
        "at": "报丧妖的庇护所",
        "kind": "clue",
        "requires": [
          "sp_asked",
          "sp_hammon_want"
        ],
        "grants": [
          "sp_ashinore"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "为了科斯特那句「建塔的法师叫什么」，阿加莎给出了一个名字。"
      },
      "n_sp_pact": {
        "title": "古枭井",
        "at": "古枭井",
        "kind": "clue",
        "requires": [
          "sp_ashinore"
        ],
        "grants": [
          "sp_pact"
        ],
        "on_fail": [],
        "cost": "科斯特收下答案，也收下了你们的名字",
        "depth": 2,
        "hint": "他很满意：这座古塔是耐瑟瑞尔的古堡，建塔者是法师阿辛多尔。帐篷里的珠宝盒装着那枚防护戒指。"
      },
      "n_sp_orcs": {
        "title": "兽人营地",
        "at": "兽人营地",
        "kind": "battle",
        "requires": [
          "sp_tasks"
        ],
        "grants": [
          "sp_orcs_cleared"
        ],
        "on_fail": [],
        "cost": "兽人斥候看见了你们；食人魔戈格会战至死",
        "depth": 1,
        "hint": "洞口二十码外蹲着一名兽人守卫；洞穴里是布鲁格霍、六个兽人和食人魔戈格。悄悄解决哨兵就能打他们一个措手不及。"
      },
      "n_sp_redos": {
        "title": "德鲁伊哨点",
        "at": "德鲁伊哨点",
        "kind": "clue",
        "requires": [
          "sp_orcs_cleared"
        ],
        "grants": [
          "sp_thundertree",
          "sp_druid"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "雷多斯说塔里那条绿龙霸着他不敢落脚的地方；赶走它，他才肯带路。他也知道克拉摩堡在哪。"
      },
      "n_sp_hut": {
        "title": "最西边的小屋",
        "at": "最西边的小屋",
        "kind": "battle",
        "requires": [
          "sp_druid"
        ],
        "grants": [
          "sp_ashen"
        ],
        "on_fail": [],
        "cost": "枯枝怪被惊动，隔壁小屋的枯枝怪会赶来",
        "depth": 1,
        "hint": "两只枯枝怪躲在门道两侧的杂草里；它们不主动攻击，除非被逼到绝路。"
      },
      "n_sp_cellar": {
        "title": "枯萎的小屋",
        "at": "枯萎的小屋",
        "kind": "battle",
        "requires": [
          "sp_ashen"
        ],
        "grants": [
          "sp_coin"
        ],
        "on_fail": [],
        "cost": "灰烬扬起来，周围的人都要过 DC 10 体质豁免",
        "depth": 1,
        "hint": "六只枯枝怪伏在过度生长的植被里。地板下压着一只旧木箱——一个商人把钱藏在这里。"
      },
      "n_sp_tavern": {
        "title": "棕马酒馆",
        "at": "棕马酒馆",
        "kind": "battle",
        "requires": [
          "sp_coin"
        ],
        "grants": [
          "sp_goldgreen"
        ],
        "on_fail": [],
        "cost": "丧尸追着任何能走动的目标",
        "depth": 1,
        "hint": "四只灰烬丧尸倚着墙根；木桶早已空，酵母味还在。"
      },
      "n_sp_herbs": {
        "title": "草药店",
        "at": "草药店",
        "kind": "clue",
        "requires": [
          "sp_goldgreen"
        ],
        "grants": [
          "sp_heirloom"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "储藏架下方的隔间里藏着一只小木箱，箱中是一条镶祖母绿吊坠的金项链。这是米尔娜一家当年从这里逃出时留下的。"
      },
      "n_sp_tower": {
        "title": "龙之塔",
        "at": "龙之塔",
        "kind": "battle",
        "requires": [
          "sp_heirloom"
        ],
        "grants": [
          "sp_tooth"
        ],
        "on_fail": [],
        "cost": "毒牙的血量砍掉一半就会飞走改日再战",
        "depth": 1,
        "hint": "青年绿龙毒牙占据着这座塔楼；它刚把两只巨蜘蛛撕开当早餐。地板上砸开的木箱里，除了法师的遗物，还有一把对植物生物最狠的「砍伐者」。"
      },
      "n_sp_cult": {
        "title": "龙之邪教徒",
        "at": "龙之邪教徒",
        "kind": "encounter",
        "requires": [
          "sp_tooth"
        ],
        "grants": [
          "sp_cultists"
        ],
        "on_fail": [],
        "cost": "法夫里克想要龙的效忠，贡品就是你们",
        "depth": 1,
        "hint": "六个人类邪教徒躲在闩死的农舍里，等一个和绿龙结盟的时机。他们的首领法夫里克打算把你们献给龙。"
      },
      "n_sp_cleared": {
        "title": "城镇广场",
        "at": "城镇广场",
        "kind": "battle",
        "requires": [
          "sp_cultists"
        ],
        "grants": [
          "sp_cleared"
        ],
        "on_fail": [],
        "cost": "邪教徒散进林子，废墟才算真的清干净",
        "depth": 1,
        "hint": "广场中央立着无冬城英雄帕里恩的雕像；四周的建筑里还散着没跑干净的丧尸与枯枝怪。"
      }
    },
    "convergences": [
      {
        "id": "c_sp_cleared",
        "title": "雷树废墟被清空了",
        "requires": [
          [
            "sp_cleared"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "枯枝怪、丧尸、邪教徒都散了。雷多斯从哨点里走出来，说该去找克拉摩堡了。"
      },
      {
        "id": "c_sp_route",
        "title": "雷多斯肯带路",
        "requires": [
          [
            "sp_druid"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "德鲁伊说这片大地上没有一寸是他不知道的。他会指路，但不会陪你们进矿井。"
      },
      {
        "id": "c_sp_asked",
        "title": "问掉了一个问题",
        "requires": [
          [
            "sp_asked"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "报丧妖给了一个答案，也只给一个。剩下的事，队伍得自己想。"
      },
      {
        "id": "c_sp_heirloom",
        "title": "找回了米尔娜的传家宝",
        "requires": [
          [
            "sp_heirloom"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "镶祖母绿的金项链躺在一堆碎陶片底下。队伍可以把它带回凡达林，也可以自己留着。"
      },
      {
        "id": "c_sp_orcs",
        "title": "飞龙突岩的兽人散了",
        "requires": [
          [
            "sp_orcs_cleared"
          ]
        ],
        "kind": "ending",
        "rank": 2,
        "outcome": "镇长哈宾会付清 100 金币，科斯特那边也算还了人情。"
      }
    ],
    "clocks": [
      {
        "name": "邪教徒的耐心",
        "every": 3,
        "fires": "n_sp_cult"
      }
    ]
  }
}
```
