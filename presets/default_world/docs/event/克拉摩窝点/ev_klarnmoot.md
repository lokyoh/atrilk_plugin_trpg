# 克拉摩窝点的伏击

```event-graph
{
  "id": "ev_klarnmoot",
  "title": "克拉摩窝点的伏击",
  "duration_days": 2,
  "scale": "light",
  "stage": "剑湾 · 三猪小径与克拉摩窝点",
  "default_outcome": "队伍在伏击点丢了一车补给，只好绕过克拉摩窝点往凡达林走；修达还在洞里。",
  "rumors": [
    "三猪小径上有人看见两匹马死在路中间。",
    "碎颅部落的地精最近在劫商队，劫完就往西北跑。",
    "有个叫刚铎的矮人托人送补给去凡达林，人却没到。",
    "三猪小径往西北五英里有个地精窝点，熊地精在那儿称王。"
  ],
  "graph": {
    "conflict": "护送的货车在离凡达林半天路程的三猪小径上被劫了，凶手往西北的克拉摩窝点逃了",
    "scale": "light",
    "facts": {
      "kl_dead_horses": {
        "text": "两匹马死在三猪小径的弯道旁，鞍囊被洗劫",
        "kind": "proof"
      },
      "kl_map_case": {
        "text": "空的皮甲地图匣落在死马身边",
        "kind": "proof"
      },
      "kl_trail": {
        "text": "地精的踪迹向西北延伸五英里，通向克拉摩窝点",
        "kind": "secret"
      },
      "kl_traps": {
        "text": "小径上埋着圈套，还有一处伪装的陷坑",
        "kind": "proof"
      },
      "kl_guard": {
        "text": "灌木丛后面藏着两个懒散的地精哨兵",
        "kind": "secret"
      },
      "kl_sneak": {
        "text": "你们从灌木丛边溜过去，没被哨兵发现",
        "kind": "proof"
      },
      "kl_wolves": {
        "text": "三头狼被铁链锁在犬舍里，链子能被拽开",
        "kind": "secret"
      },
      "kl_cliff": {
        "text": "西侧通道的岩架撑不住一百磅的重量",
        "kind": "proof"
      },
      "kl_flood": {
        "text": "上游的水坝被拉开了，洪水正沿主通道冲下来",
        "kind": "secret"
      },
      "kl_dam": {
        "text": "双子池的石坝能挡住洪水，也能放它下来",
        "kind": "proof"
      },
      "kl_suda": {
        "text": "修达·霍温特被绑在地精休息室，只剩一口气",
        "kind": "proof"
      },
      "kl_imik": {
        "text": "伊米克愿意拿克拉格的头换修达",
        "kind": "secret"
      },
      "kl_suda_alive": {
        "text": "修达活着跟上了队伍",
        "kind": "proof",
        "excludes": [
          "kl_suda_dead"
        ]
      },
      "kl_suda_dead": {
        "text": "修达被推下悬崖摔死了",
        "kind": "debt",
        "excludes": [
          "kl_suda_alive"
        ]
      },
      "kl_klarg": {
        "text": "克拉格是这窝地精的首领，住在最深的洞里",
        "kind": "secret"
      },
      "kl_kingdom": {
        "text": "格罗尔国王住在克拉摩堡，在东北二十英里外的无冬森林里",
        "kind": "secret"
      },
      "kl_cleared": {
        "text": "克拉摩窝点被清空了",
        "kind": "proof"
      },
      "kl_klarg_head": {
        "text": "克拉格的熊地精头颅",
        "kind": "proof"
      },
      "kl_bargained": {
        "text": "你和伊米克做了一笔交易",
        "kind": "debt"
      },
      "kl_loot": {
        "text": "补给品堆里是狮盾小贩的货",
        "kind": "proof"
      },
      "kl_frog": {
        "text": "翡翠青蛙雕像，眼睛是微型金球",
        "kind": "proof"
      },
      "kl_swept": {
        "text": "洪水把你们冲回了洞口",
        "kind": "debt"
      }
    },
    "nodes": {
      "n_kl_ambush": {
        "title": "地精伏击地",
        "at": "地精伏击地",
        "kind": "battle",
        "requires": [],
        "grants": [
          "kl_dead_horses",
          "kl_map_case"
        ],
        "on_fail": [],
        "cost": "你们把两匹死马留在了路边——总有人会问是谁干的",
        "depth": 1,
        "hint": "两匹马死在路中央，横在你们脚边，鞍囊被洗劫，空的皮甲地图匣散在旁边。树影里四只地精正等着下一个走近的旅人。"
      },
      "n_kl_follow": {
        "title": "地精伏击地",
        "at": "地精伏击地",
        "kind": "clue",
        "requires": [
          "kl_dead_horses"
        ],
        "grants": [
          "kl_trail",
          "kl_traps"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "灌木后的小径向西北延伸五英里；沿途一个圈套、一处伪装成浮土的陷坑，还有被拖走的人类躯体的痕迹。"
      },
      "n_kl_mouth": {
        "title": "洞口",
        "at": "洞口",
        "kind": "clue",
        "requires": [
          "kl_trail"
        ],
        "grants": [
          "kl_guard"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "溪水从洞口流出，荆棘遮住入口；灌木后有两个打瞌睡的地精。"
      },
      "n_kl_sentry": {
        "title": "地精暗哨",
        "at": "地精暗哨",
        "kind": "battle",
        "requires": [
          "kl_guard"
        ],
        "grants": [
          "kl_sneak"
        ],
        "on_fail": [],
        "cost": "两个地精倒下，窝点里的人会知道有人来过",
        "depth": 1,
        "hint": "两名地精守着灌木丛里的瞭望点；压低身形走过去，或者硬闯。"
      },
      "n_kl_kennel": {
        "title": "犬舍",
        "at": "犬舍",
        "kind": "encounter",
        "requires": [
          "kl_sneak"
        ],
        "grants": [
          "kl_wolves"
        ],
        "on_fail": [],
        "cost": "安抚狼群要花时间和食物；链子松了它们会咬人",
        "depth": 1,
        "hint": "三头狼被铁链拴在洞里，链子只靠一根插在石笋下的铁棒固定。喂食能让 DC 降到 10，安抚需要 DC 15 的驯兽检定。"
      },
      "n_kl_cliff": {
        "title": "陡峭的通道",
        "at": "陡峭的通道",
        "kind": "clue",
        "requires": [
          "kl_wolves"
        ],
        "grants": [
          "kl_cliff"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "西侧通道堆着瓦砾，两块悬崖之间的岩架已经松动——过一百磅就会塌。"
      },
      "n_kl_bridge": {
        "title": "天桥",
        "at": "天桥",
        "kind": "battle",
        "requires": [
          "kl_cliff"
        ],
        "grants": [
          "kl_flood"
        ],
        "on_fail": [],
        "cost": "岗哨朝你们扔下标枪；上游的水坝也被他拉下来了",
        "depth": 1,
        "hint": "一名地精守卫站在二十尺高的绳索桥上。他认出入侵者就向双子池发信号，洪水会在下一轮沿主通道冲下来。"
      },
      "n_kl_dam": {
        "title": "双子池",
        "at": "双子池",
        "kind": "clue",
        "requires": [
          "kl_flood"
        ],
        "grants": [
          "kl_dam"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "两个大池子由低矮毛石墙拦成水坝；拉掉支撑，溪流就畅通了。"
      },
      "n_kl_rest": {
        "title": "地精休息室",
        "at": "地精休息室",
        "kind": "battle",
        "requires": [
          "kl_dam"
        ],
        "grants": [
          "kl_suda"
        ],
        "on_fail": [],
        "cost": "六只地精和一个首领；被绑在岩架上的人只剩一口气",
        "depth": 1,
        "hint": "六只地精围着炊火，首领在上层。二把手耶米克把修达当人质，喊你们停战，否则就把他推下悬崖。"
      },
      "n_kl_bargain": {
        "title": "地精休息室",
        "at": "地精休息室",
        "kind": "decision",
        "requires": [
          "kl_suda"
        ],
        "grants": [
          "kl_imik"
        ],
        "on_fail": [
          "kl_suda_dead"
        ],
        "cost": "答应伊米克，就等于欠他一条命——他不会付清",
        "depth": 2,
        "hint": "耶米克想推翻克拉格：他帮你们杀克拉格，条件是带回熊地精的头颅换修达。修达本人警告你们别信他，他说得对。"
      },
      "n_kl_free": {
        "title": "地精休息室",
        "at": "地精休息室",
        "kind": "clue",
        "requires": [
          "kl_suda"
        ],
        "grants": [
          "kl_suda_alive"
        ],
        "on_fail": [],
        "cost": "救他、稳住伤势、背他走——队伍得为此停下来",
        "depth": 2,
        "hint": "把修达从岩架上解下来，喂他一口东西，他愿意跟着走。"
      },
      "n_kl_camp": {
        "title": "克拉格的洞穴",
        "at": "克拉格的洞穴",
        "kind": "clue",
        "requires": [
          "kl_suda"
        ],
        "grants": [
          "kl_klarg",
          "kl_kingdom"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "熊地精克拉格和他的狼「撕裂者」在洞里；火坑旁的补给品堆上全是狮盾小贩的蓝狮子徽记。俘虏的地精说格罗尔国王在东北二十英里外。"
      },
      "n_kl_klarg": {
        "title": "克拉格的洞穴",
        "at": "克拉格的洞穴",
        "kind": "battle",
        "requires": [
          "kl_klarg"
        ],
        "grants": [
          "kl_cleared",
          "kl_klarg_head"
        ],
        "on_fail": [],
        "cost": "克拉格死了，这窝地精也就散了",
        "depth": 1,
        "hint": "克拉格自视为督军，战败后会退到石笋后面；狼若先死，熊地精会从犬舍的烟囱爬下去逃走。"
      },
      "n_kl_loot": {
        "title": "克拉格的洞穴",
        "at": "克拉格的洞穴",
        "kind": "clue",
        "requires": [
          "kl_cleared"
        ],
        "grants": [
          "kl_loot",
          "kl_frog"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "补给品堆里有狮盾小贩的货；一个没上锁的箱子里是六百铜币、一百一十银币、两瓶治疗药水，还有一尊翡翠青蛙雕像。"
      },
      "n_kl_trade": {
        "title": "克拉格的洞穴",
        "at": "克拉格的洞穴",
        "kind": "clue",
        "requires": [
          "kl_imik",
          "kl_klarg_head"
        ],
        "grants": [
          "kl_bargained"
        ],
        "on_fail": [],
        "cost": "耶米克认了头颅，却说赎金另算",
        "depth": 2,
        "hint": "把熊地精的头颅扔给耶米克，他会放开修达——然后加价。"
      },
      "n_kl_surge": {
        "title": "陡峭的通道",
        "at": "陡峭的通道",
        "kind": "encounter",
        "requires": [
          "kl_flood"
        ],
        "grants": [
          "kl_swept"
        ],
        "on_fail": [],
        "cost": "被冲走就是从头再来一趟",
        "depth": 1,
        "hint": "洪水沿主通道灌到洞口：DC 10 敏捷豁免能避开，DC 15 力量豁免能抓住固定物，否则被击倒冲走。"
      }
    },
    "convergences": [
      {
        "id": "c_kl_cleared",
        "title": "克拉摩窝点被清空了",
        "requires": [
          [
            "kl_cleared"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "克拉格死了，窝点散了。修达带着格罗尔国王和失落矿坑的消息加入了队伍——队伍往凡达林去。"
      },
      {
        "id": "c_kl_rescue",
        "title": "修达活着跟上了队伍",
        "requires": [
          [
            "kl_suda_alive"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "修达被解下来，伤势稳住。他说刚铎的地图被送去了克拉摩堡——东北二十英里外的无冬森林。"
      },
      {
        "id": "c_kl_bargain",
        "title": "一笔没付清的账",
        "requires": [
          [
            "kl_bargained"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "耶米克收了熊地精的头颅，也放开了修达——然后开始谈赎金。队伍带着一个愿意卖命的盟友，也带着一笔还不清的债。"
      },
      {
        "id": "c_kl_withdraw",
        "title": "带着消息退回三猪小径",
        "requires": [
          [
            "kl_kingdom"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "队伍没有去啃克拉摩窝点，而是带着俘虏地精说的方向退回三猪小径：克拉摩堡在东北二十英里外的无冬森林里。"
      },
      {
        "id": "c_kl_swept",
        "title": "被洪水冲回洞口",
        "requires": [
          [
            "kl_swept"
          ]
        ],
        "kind": "turn",
        "rank": 2,
        "outcome": "洪水把队伍冲回洞口。窝点还在上面，敌人也还在。"
      }
    ],
    "clocks": [
      {
        "name": "上游的水坝",
        "every": 3,
        "fires": "n_kl_surge"
      }
    ]
  }
}
```
