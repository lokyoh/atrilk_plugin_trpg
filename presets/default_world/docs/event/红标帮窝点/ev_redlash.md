# 崔森德庄园下的窝点

```event-graph
{
  "id": "ev_redlash",
  "title": "崔森德庄园下的窝点",
  "duration_days": 2,
  "scale": "medium",
  "stage": "剑湾 · 凡达林与红标帮窝点",
  "default_outcome": "红标帮把队伍赶出凡达林，玻璃杖在暗门后面听着；但镇上的人知道了有人动过手。",
  "rumors": [
    "红标帮在凡达林收保护费，谁不给就砸谁的摊子。",
    "崔森德庄园下面有红标帮的地牢，庄园东边的树林里有条秘密隧道。",
    "矿工兑换所的哈利娅·桑顿出 100 金币要红标帮首领的人头。",
    "狮盾小贩莱妮·灰风的商队被峭壁地精劫了，货到现在没回来。",
    "加莱尔修女要有人替她问报丧妖一个问题。"
  ],
  "graph": {
    "conflict": "红标帮把持着凡达林，玻璃杖的窝点就藏在这座小镇东边的庄园底下",
    "scale": "medium",
    "facts": {
      "rl_estate": {
        "text": "崔森德庄园的地窖通向下面那片地牢",
        "kind": "secret"
      },
      "rl_pack": {
        "text": "蓄水池水下挂着伊阿诺的防水包：治疗药水、隐形药水、五十金币",
        "kind": "proof"
      },
      "rl_cape": {
        "text": "三件脏猩红斗篷挂在营房的铺位上",
        "kind": "proof"
      },
      "rl_trap": {
        "text": "走廊中央的假地板下是陷坑",
        "kind": "proof"
      },
      "rl_skeletons_down": {
        "text": "骷髅认出了猩红斗篷（或听懂了那个旧名字），没有动手",
        "kind": "proof"
      },
      "rl_skeletons_fight": {
        "text": "三具骷髅从石棺里站起来扑过来",
        "kind": "proof"
      },
      "rl_bracelet": {
        "text": "崔森德墓室里躺着九十金币的金手镯",
        "kind": "proof"
      },
      "rl_captives": {
        "text": "米尔娜·登德拉尔和两个孩子被关在围栏里",
        "kind": "proof"
      },
      "rl_freed": {
        "text": "米尔娜一家三口被放走了",
        "kind": "proof",
        "excludes": [
          "rl_sold"
        ]
      },
      "rl_sold": {
        "text": "你把俘虏交出去领了赏金",
        "kind": "debt",
        "excludes": [
          "rl_freed"
        ]
      },
      "rl_serel": {
        "text": "木雕师塞尔·登德拉尔的尸体在裂隙底下，被啃了一半",
        "kind": "proof"
      },
      "rl_nosth": {
        "text": "诺斯怪愿意用新鲜血肉换取不被打扰",
        "kind": "secret"
      },
      "rl_nosth_pact": {
        "text": "你和诺斯怪达成了休战",
        "kind": "proof",
        "excludes": [
          "rl_nosth_kill"
        ]
      },
      "rl_nosth_kill": {
        "text": "诺斯怪死在裂隙底下",
        "kind": "proof",
        "excludes": [
          "rl_nosth_pact"
        ]
      },
      "rl_claw": {
        "text": "从裂隙底木箱里拿到的「禽爪」——+1 长剑，剑柄塑成展翅猛禽",
        "kind": "proof"
      },
      "rl_musk": {
        "text": "莫斯克带着一把能锁上也能打开所有门的铁钥匙",
        "kind": "proof"
      },
      "rl_drook": {
        "text": "地精德鲁普知道窝点的布局、暗门和陷阱在哪",
        "kind": "proof"
      },
      "rl_wavemouth": {
        "text": "熊地精知道去回声洞的路，但怎么都不肯说",
        "kind": "secret"
      },
      "rl_silver": {
        "text": "赌桌上的钱和一枚镶红宝石的金耳环",
        "kind": "proof"
      },
      "rl_rat": {
        "text": "工坊里那只老鼠和伊阿诺有传念联结",
        "kind": "secret"
      },
      "rl_rat_dead": {
        "text": "老鼠被打死了，没人能提前报信",
        "kind": "proof"
      },
      "rl_rat_alive": {
        "text": "老鼠还活着，伊阿诺已经知道有人来了",
        "kind": "debt"
      },
      "rl_letter": {
        "text": "黑蜘蛛的信：把矮人身上的地图尽快交给我",
        "kind": "proof"
      },
      "rl_iano": {
        "text": "玻璃杖就是伊阿诺·阿尔布雷克——领主联盟的法师",
        "kind": "secret"
      },
      "rl_spider": {
        "text": "黑蜘蛛是个卓尔，正在回声洞找法术锻造炉",
        "kind": "secret"
      },
      "rl_book": {
        "text": "《书之魔契》：矮人语写的失落矿坑史，还提到「光明使者」丢在回声洞",
        "kind": "secret"
      },
      "rl_reagents": {
        "text": "三小瓶珍稀试剂：水银、龙胆汁、颠茄粉",
        "kind": "proof"
      },
      "rl_arms": {
        "text": "军械库的铁架上摆着十二把三叉枪、六把短剑和四把轻弩",
        "kind": "proof"
      },
      "rl_hides": {
        "text": "储藏室的两扇暗门：一扇通军械库，一扇通玻璃杖的居所",
        "kind": "secret"
      },
      "rl_escaped": {
        "text": "伊阿诺带着法杖和卷轴从暗门逃了",
        "kind": "debt"
      },
      "rl_captured": {
        "text": "伊阿诺被活捉了",
        "kind": "proof"
      },
      "rl_cleared": {
        "text": "红标帮窝点被端了",
        "kind": "proof"
      }
    },
    "nodes": {
      "n_rl_estate": {
        "title": "崔森德庄园",
        "at": "崔森德庄园",
        "kind": "clue",
        "requires": [],
        "grants": [
          "rl_estate"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "废弃庄园的地窖楼梯通到红标帮的地牢：石板地牢，砌石墙，西端比东端低。"
      },
      "n_rl_cellar": {
        "title": "地窖",
        "at": "地窖",
        "kind": "clue",
        "requires": [
          "rl_estate"
        ],
        "grants": [
          "rl_pack"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "大型蓄水池水面下两尺挂着防水包；西南角还有一扇暗门。"
      },
      "n_rl_barracks": {
        "title": "营房",
        "at": "营房",
        "kind": "battle",
        "requires": [
          "rl_pack"
        ],
        "grants": [
          "rl_cape"
        ],
        "on_fail": [],
        "cost": "三个红标帮恶霸倒下，小镇会知道有人动了他们",
        "depth": 1,
        "hint": "三名恶霸在铺位边打盹；铺位上挂着三件肮脏的猩红斗篷。"
      },
      "n_rl_corridor": {
        "title": "陷阱走廊",
        "at": "陷阱走廊",
        "kind": "clue",
        "requires": [
          "rl_cape"
        ],
        "grants": [
          "rl_trap"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "灰尘下是松散石板铺在断木梁上；坑南北两侧有窄边可以绕。"
      },
      "n_rl_tomb": {
        "title": "崔森德墓室",
        "at": "崔森德墓室",
        "kind": "decision",
        "requires": [
          "rl_trap"
        ],
        "grants": [
          "rl_skeletons_down"
        ],
        "on_fail": [
          "rl_skeletons_fight"
        ],
        "cost": "穿上猩红斗篷，或者对它们说出那个旧名字",
        "depth": 2,
        "hint": "三具骷髅守着通往围栏与墓穴的门。穿猩红斗篷、或说出「伊勒芬」，它们才不动手。"
      },
      "n_rl_skeletons": {
        "title": "崔森德墓室",
        "at": "崔森德墓室",
        "kind": "battle",
        "requires": [
          "rl_skeletons_fight"
        ],
        "grants": [
          "rl_bracelet"
        ],
        "on_fail": [],
        "cost": "墓室的响动会传到围栏那边",
        "depth": 1,
        "hint": "三具骷髅拼起身体扑过来；石棺里的金手镯就压在骸骨之间。"
      },
      "n_rl_pens": {
        "title": "奴隶围栏",
        "at": "奴隶围栏",
        "kind": "battle",
        "requires": [
          "rl_bracelet"
        ],
        "grants": [
          "rl_captives"
        ],
        "on_fail": [],
        "cost": "两名恶霸在围栏边守着；救人会把他们全招来",
        "depth": 1,
        "hint": "米尔娜·登德拉尔和十三十八岁的两个孩子被铁项圈锁着；她的丈夫塞尔因为不肯屈服被杀了。"
      },
      "n_rl_captives": {
        "title": "奴隶围栏",
        "at": "奴隶围栏",
        "kind": "decision",
        "requires": [
          "rl_captives"
        ],
        "grants": [
          "rl_freed"
        ],
        "on_fail": [
          "rl_sold"
        ],
        "cost": "放他们走，赏金就没有了",
        "depth": 2,
        "hint": "铁栏杆的锁要 DC 10 开锁或 DC 22 硬拉；镇长哈宾愿不愿意收押囚犯是另一回事。"
      },
      "n_rl_armory": {
        "title": "军械库",
        "at": "军械库",
        "kind": "clue",
        "requires": [
          "rl_captives"
        ],
        "grants": [
          "rl_arms"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "武器架上摆着十二把三叉枪、六把短剑；门从外面锁着，对面有暗门。"
      },
      "n_rl_store": {
        "title": "储藏室和工作间",
        "at": "储藏室和工作间",
        "kind": "clue",
        "requires": [
          "rl_arms"
        ],
        "grants": [
          "rl_hides"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "两扇暗门：一扇通军械库，一扇通玻璃杖的居所。"
      },
      "n_rl_chasm": {
        "title": "裂隙",
        "at": "裂隙",
        "kind": "encounter",
        "requires": [
          "rl_hides"
        ],
        "grants": [
          "rl_nosth",
          "rl_serel"
        ],
        "on_fail": [
          "rl_nosth_kill"
        ],
        "cost": "诺斯怪要的是新鲜血肉，而桥只撑得住五十磅",
        "depth": 1,
        "hint": "裂隙底下住着渴望血肉的诺斯怪；伊阿诺用宝藏和鲜肉喂它。深处的瓦砾堆里露出半具尸体——木雕师塞尔·登德拉尔。"
      },
      "n_rl_nosth": {
        "title": "裂隙",
        "at": "裂隙",
        "kind": "battle",
        "requires": [
          "rl_nosth_kill"
        ],
        "grants": [
          "rl_claw"
        ],
        "on_fail": [],
        "cost": "诺斯怪被杀了，窝点里少一个能拖住追兵的东西",
        "depth": 1,
        "hint": "诺斯怪从石柱后扑出来；它藏着的那只木箱在裂隙底部北桥下。"
      },
      "n_rl_pact": {
        "title": "裂隙",
        "at": "裂隙",
        "kind": "clue",
        "requires": [
          "rl_nosth"
        ],
        "grants": [
          "rl_nosth_pact"
        ],
        "on_fail": [],
        "cost": "喂它的肉从你们自己的口粮里出",
        "depth": 2,
        "hint": "丢一块鲜肉过去，诺斯怪会讲条件，也会讲红标帮知道的一切。"
      },
      "n_rl_bearquarters": {
        "title": "红标帮警卫营房",
        "at": "红标帮警卫营房",
        "kind": "battle",
        "requires": [
          "rl_claw"
        ],
        "grants": [
          "rl_musk"
        ],
        "on_fail": [],
        "cost": "三个熊地精倒下，德鲁普醒来后会一直讨好你们",
        "depth": 1,
        "hint": "三只熊地精在欺凌地精德鲁普；首领莫斯克戴着一只镶半宝石的黑皮眼罩，腰间挂着一把能开所有门的铁钥匙。"
      },
      "n_rl_drook": {
        "title": "红标帮警卫营房",
        "at": "红标帮警卫营房",
        "kind": "clue",
        "requires": [
          "rl_musk"
        ],
        "grants": [
          "rl_drook",
          "rl_wavemouth"
        ],
        "on_fail": [],
        "cost": "熊地精更怕黑蜘蛛，不是更怕你们；撬它们的嘴要用威吓",
        "depth": 2,
        "hint": "德鲁普愿意把窝点的暗门和陷阱都讲出来；熊地精知道克拉摩堡和回声洞的位置，但要 DC 15 的威吓才肯说。"
      },
      "n_rl_common": {
        "title": "公共休息室",
        "at": "公共休息室",
        "kind": "battle",
        "requires": [
          "rl_drook"
        ],
        "grants": [
          "rl_silver"
        ],
        "on_fail": [],
        "cost": "四个人在赌桌上作弊，输的人不会好说话",
        "depth": 1,
        "hint": "四名恶霸喝得酩酊大醉，桌上压着钱币和饰品。"
      },
      "n_rl_workshop": {
        "title": "法师的工作坊",
        "at": "法师的工作坊",
        "kind": "clue",
        "requires": [
          "rl_silver"
        ],
        "grants": [
          "rl_rat",
          "rl_book",
          "rl_reagents"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "蒸馏器在冒泡，一只老鼠在工作台下乱窜；架上有一本用矮人语写的《书之魔契》，记着失落矿坑与法术锻造炉的历史。"
      },
      "n_rl_rat": {
        "title": "法师的工作坊",
        "at": "法师的工作坊",
        "kind": "decision",
        "requires": [
          "rl_rat"
        ],
        "grants": [
          "rl_rat_dead"
        ],
        "on_fail": [
          "rl_rat_alive"
        ],
        "cost": "老鼠死了，法师就没有准备时间；老鼠活着，他就有",
        "depth": 2,
        "hint": "老鼠与主人共享传念联结——它看见你们，伊阿诺立刻就知道。"
      },
      "n_rl_chamber": {
        "title": "玻璃手杖的居所",
        "at": "玻璃手杖的居所",
        "kind": "battle",
        "requires": [
          "rl_rat"
        ],
        "grants": [
          "rl_letter",
          "rl_iano",
          "rl_cleared"
        ],
        "on_fail": [],
        "cost": "伊阿诺·阿尔布雷克，玻璃杖——玻璃法杖就在手边",
        "depth": 1,
        "hint": "他坐在书桌前读《书之魔契》；慌乱中留下一封黑蜘蛛的信，东北角的暗门没关严。"
      },
      "n_rl_interrogate": {
        "title": "玻璃手杖的居所",
        "at": "玻璃手杖的居所",
        "kind": "clue",
        "requires": [
          "rl_iano"
        ],
        "grants": [
          "rl_spider",
          "rl_captured"
        ],
        "on_fail": [],
        "cost": "他比命金贵，肯开口是因为他知道自己跑不掉",
        "depth": 2,
        "hint": "生命值掉到八点他就会投降；被押住之后他说：黑蜘蛛是个卓尔，正在回声洞找法术锻造炉。"
      },
      "n_rl_escape": {
        "title": "玻璃手杖的居所",
        "at": "玻璃手杖的居所",
        "kind": "clue",
        "requires": [
          "rl_rat_alive"
        ],
        "grants": [
          "rl_escaped"
        ],
        "on_fail": [],
        "cost": "他带着防护法杖和卷轴跑了",
        "depth": 2,
        "hint": "老鼠先报的警：他抓起法杖和卷轴，从东北角暗门溜走。"
      }
    },
    "convergences": [
      {
        "id": "c_rl_cleared",
        "title": "红标帮窝点被端了",
        "requires": [
          [
            "rl_cleared"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "玻璃杖被按在书桌前。修达·霍温特会把他关进镇长大厅的牢房，等无冬城来人。"
      },
      {
        "id": "c_rl_captured",
        "title": "活捉了玻璃杖",
        "requires": [
          [
            "rl_captured"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "伊阿诺·阿尔布雷克没有反抗——他比什么都惜命。他说黑蜘蛛是个卓尔，正在回声洞里找法术锻造炉。"
      },
      {
        "id": "c_rl_escaped",
        "title": "玻璃杖带着信跑了",
        "requires": [
          [
            "rl_escaped"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "老鼠先看见的客人。伊阿诺抓起法杖和卷轴从暗门溜走，桌上留下一封黑蜘蛛的信。"
      },
      {
        "id": "c_rl_freed",
        "title": "米尔娜一家回到了镇上",
        "requires": [
          [
            "rl_freed"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "米尔娜·登德拉尔带着两个孩子回到了凡达林。她说家在雷树废墟的草药店里还有一样东西没拿回来。"
      },
      {
        "id": "c_rl_bought",
        "title": "三个人的命换成了赏金",
        "requires": [
          [
            "rl_sold"
          ]
        ],
        "kind": "ending",
        "rank": 2,
        "outcome": "镇长收下了俘虏，赏金也发下来了。队伍从此不再是无名之辈。"
      },
      {
        "id": "c_rl_nosth",
        "title": "和裂隙底下的东西谈成了",
        "requires": [
          [
            "rl_nosth_pact"
          ]
        ],
        "kind": "turn",
        "rank": 2,
        "outcome": "诺斯怪收了肉，也收了话：红标帮知道的一切它都知道。"
      }
    ],
    "clocks": [
      {
        "name": "老鼠的传念",
        "every": 2,
        "fires": "n_rl_escape"
      }
    ]
  }
}
```
