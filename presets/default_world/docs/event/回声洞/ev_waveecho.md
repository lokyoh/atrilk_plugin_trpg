# 回声洞的法术锻造炉

```event-graph
{
  "id": "ev_waveecho",
  "title": "回声洞的法术锻造炉",
  "duration_days": 3,
  "scale": "heavy",
  "stage": "剑湾 · 剑山脉深处的回声洞",
  "default_outcome": "黑蜘蛛还在回声洞里，队伍带着一身矿坑的灰退回凡达林；但「光明使者」的名字已经传出去了。",
  "rumors": [
    "回声洞在剑山脉的深谷里，五百年前被兽人毁过一次。",
    "矿里每两分钟就有一次像碎浪的响声，一路往东北越来越响。",
    "有人说矿底有座法术锻造炉，造出过矮人的魔法战锤。",
    "黑蜘蛛派了熊地精在北边的营房守着，不让不死生物过去。",
    "寻岩者三兄弟只剩两个了：一个死在入口，一个在矿底。"
  ],
  "graph": {
    "conflict": "黑蜘蛛那兹纳尔正在回声洞里翻找五百年前的法术锻造炉，而矿里的不死生物还没清完",
    "scale": "heavy",
    "facts": {
      "wv_boot": {
        "text": "塔登的跑跳之靴就在入口的尸骨上",
        "kind": "proof"
      },
      "wv_tunnel": {
        "text": "坑底隧道西北通矿道，向东通旧入口",
        "kind": "secret"
      },
      "wv_jelly": {
        "text": "矿道里有一只等着落单者的赭果冻",
        "kind": "secret"
      },
      "wv_bats": {
        "text": "旧入口的洞顶倒挂着十只蚊蝠",
        "kind": "secret"
      },
      "wv_skeleton": {
        "text": "旧警卫室的骨头在下一轮自己拼起来",
        "kind": "secret"
      },
      "wv_vault": {
        "text": "试金师办公室的柜台后面有一只上锁的铁保险箱",
        "kind": "proof"
      },
      "wv_flesh": {
        "text": "食尸鬼在矿工的遗骸里啃骨头",
        "kind": "secret"
      },
      "wv_rest": {
        "text": "储物间废墟的门可以从里面堵上",
        "kind": "proof"
      },
      "wv_spores": {
        "text": "真菌洞窟的孢子会放毒",
        "kind": "secret"
      },
      "wv_hall": {
        "text": "大洞窟西侧岩架上蹲着七只食尸鬼",
        "kind": "secret"
      },
      "wv_wand": {
        "text": "黑水池底那具法师遗骸手里握着一根魔法飞弹魔杖",
        "kind": "proof"
      },
      "wv_ogres": {
        "text": "北部营房住着五只熊地精，是黑蜘蛛的前线",
        "kind": "secret"
      },
      "wv_flame": {
        "text": "燃焰之颅还在守着冶炼洞窟",
        "kind": "secret"
      },
      "wv_bright": {
        "text": "两座按人类尺寸盖的建筑，矿里别处没有",
        "kind": "secret"
      },
      "wv_spirit": {
        "text": "墨梅斯克要魔法物品才肯谈",
        "kind": "secret"
      },
      "wv_spirit_pact": {
        "text": "墨梅斯克放过了你们，还给了一张秘密地图",
        "kind": "proof",
        "excludes": [
          "wv_spirit_kill"
        ]
      },
      "wv_spirit_kill": {
        "text": "墨梅斯克被驱散了",
        "kind": "debt",
        "excludes": [
          "wv_spirit_pact"
        ]
      },
      "wv_map": {
        "text": "墨梅斯克给的地图指向别处的副本",
        "kind": "proof"
      },
      "wv_eye": {
        "text": "观察者眼魔守在法术工厂",
        "kind": "secret"
      },
      "wv_eye_gone": {
        "text": "眼魔被说服离开岗位",
        "kind": "proof",
        "excludes": [
          "wv_eye_blind"
        ]
      },
      "wv_eye_blind": {
        "text": "眼魔被彻底打瞎，退回了自己的位面",
        "kind": "proof",
        "excludes": [
          "wv_eye_gone"
        ]
      },
      "wv_forge": {
        "text": "光明使者和龙之守护",
        "kind": "proof"
      },
      "wv_tide": {
        "text": "隆隆潮浪每隔两分钟涌一次，向东北越来越响",
        "kind": "secret"
      },
      "wv_bed": {
        "text": "古老河床能绕过冶炼洞窟的不死生物",
        "kind": "secret"
      },
      "wv_gauntlet": {
        "text": "食人魔力量护手埋在崩塌洞窟的谷底",
        "kind": "proof"
      },
      "wv_nazvar": {
        "text": "黑蜘蛛那兹纳尔在杜马松的神庙，身边有四只巨蜘蛛",
        "kind": "secret"
      },
      "wv_statue": {
        "text": "杜马松雕像的绿宝石是玻璃做的，但碰了会塌房",
        "kind": "secret"
      },
      "wv_key": {
        "text": "内兹纳尔那把形似锻砧的铁钥匙能开最里面那扇门",
        "kind": "proof"
      },
      "wv_nanzhuo": {
        "text": "南卓被锁在祭司的居所里",
        "kind": "proof"
      },
      "wv_nanzhuo_saved": {
        "text": "南卓被放了出来",
        "kind": "proof"
      },
      "wv_cleared": {
        "text": "回声洞被清空了",
        "kind": "proof"
      },
      "wv_south": {
        "text": "南卓知道一点矿井的布局",
        "kind": "secret"
      },
      "wv_wandered": {
        "text": "你们在矿井里撞上了游荡的怪物",
        "kind": "proof"
      }
    },
    "nodes": {
      "n_wv_entrance": {
        "title": "洞穴入口",
        "at": "洞穴入口",
        "kind": "clue",
        "requires": [],
        "grants": [
          "wv_boot"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "寻岩者的营地：面粉袋、盐袋、提灯，还有一具死了至少一周的矮人尸体——塔登。他脚上那双靴子还没被拿走。"
      },
      "n_wv_pit": {
        "title": "洞穴入口",
        "at": "洞穴入口",
        "kind": "clue",
        "requires": [
          "wv_boot"
        ],
        "grants": [
          "wv_tunnel"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "东北角塌出一个十尺宽二十尺深的坑，坑底两条隧道通向不同方向。"
      },
      "n_wv_mine": {
        "title": "矿道",
        "at": "矿道",
        "kind": "battle",
        "requires": [
          "wv_tunnel"
        ],
        "grants": [
          "wv_jelly"
        ],
        "on_fail": [],
        "cost": "赭果冻会一直跟着落单的人",
        "depth": 1,
        "hint": "六尺高的交叉通道迷宫里，一只胶质怪正跟着你们，等一个落单的目标。"
      },
      "n_wv_old": {
        "title": "旧入口",
        "at": "旧入口",
        "kind": "battle",
        "requires": [
          "wv_jelly"
        ],
        "grants": [
          "wv_bats"
        ],
        "on_fail": [],
        "cost": "倒挂的蚊蝠俯冲下来",
        "depth": 1,
        "hint": "近二十具骷髅与兽人遗骸躺在地上；洞顶倒挂着十只饥肠辘辘的蚊蝠。"
      },
      "n_wv_guard": {
        "title": "旧警卫室",
        "at": "旧警卫室",
        "kind": "battle",
        "requires": [
          "wv_bats"
        ],
        "grants": [
          "wv_skeleton"
        ],
        "on_fail": [],
        "cost": "拼起来的骨头不会记仇，但也不会停",
        "depth": 1,
        "hint": "任何活物进屋，下一轮九具骷髅就会自己拼起来站起来。"
      },
      "n_wv_assay": {
        "title": "试金师办公室",
        "at": "试金师办公室",
        "kind": "clue",
        "requires": [
          "wv_skeleton"
        ],
        "grants": [
          "wv_vault"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "柜台后面有一只上锁的铁保险箱；矮人语的称重记录还剩几个碎片。"
      },
      "n_wv_south": {
        "title": "南部营房",
        "at": "南部营房",
        "kind": "battle",
        "requires": [
          "wv_vault"
        ],
        "grants": [
          "wv_flesh"
        ],
        "on_fail": [],
        "cost": "食尸鬼闻到活人的味道就扑",
        "depth": 1,
        "hint": "三只食尸鬼蹲在矮人与兽人的遗骸里啃骨头，看见活人就冲上来。"
      },
      "n_wv_store": {
        "title": "储物间废墟",
        "at": "储物间废墟",
        "kind": "clue",
        "requires": [
          "wv_flesh"
        ],
        "grants": [
          "wv_rest"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "北面那间储藏室完好：木桶裂了，但门结实，能从里面堵住。"
      },
      "n_wv_fungus": {
        "title": "真菌洞窟",
        "at": "真菌洞窟",
        "kind": "encounter",
        "requires": [
          "wv_rest"
        ],
        "grants": [
          "wv_spores"
        ],
        "on_fail": [],
        "cost": "孢子毒气不认人，屏住呼吸也没用",
        "depth": 1,
        "hint": "遍地的真菌会释放毒气：每分钟一次 DC 11 体质豁免，失败就是 3d6 毒素伤害与十分钟中毒。"
      },
      "n_wv_hall": {
        "title": "大洞窟",
        "at": "大洞窟",
        "kind": "battle",
        "requires": [
          "wv_spores"
        ],
        "grants": [
          "wv_hall"
        ],
        "on_fail": [],
        "cost": "十尺高的悬崖挡住去路，绕过去要花力气",
        "depth": 1,
        "hint": "西侧岩架的阴影里蹲着七只食尸鬼；它们会连着光一起注意到。"
      },
      "n_wv_pool": {
        "title": "黑水池",
        "at": "黑水池",
        "kind": "clue",
        "requires": [
          "wv_hall"
        ],
        "grants": [
          "wv_wand"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 2,
        "hint": "池底二十尺躺着一具古老人类法师的遗骸，兽人箭矢还插在胸腔里；他手里握着一根魔法飞弹魔杖。"
      },
      "n_wv_north": {
        "title": "北部营房",
        "at": "北部营房",
        "kind": "battle",
        "requires": [
          "wv_wand"
        ],
        "grants": [
          "wv_ogres"
        ],
        "on_fail": [],
        "cost": "这五个熊地精是黑蜘蛛的前线，死一个他就少一道屏障",
        "depth": 1,
        "hint": "五只熊地精守着北边的门；他们是内兹纳尔的忠实仆从。"
      },
      "n_wv_smelter": {
        "title": "冶炼洞窟",
        "at": "冶炼洞窟",
        "kind": "battle",
        "requires": [
          "wv_ogres"
        ],
        "grants": [
          "wv_flame"
        ],
        "on_fail": [],
        "cost": "八具矮人僵尸和一颗燃焰之颅；它们还在执行五百年前的命令",
        "depth": 1,
        "hint": "八名阵亡矮人战士的僵尸会追击任何活物，但追出房间一轮就停；更聪明的是那颗燃焰之颅。"
      },
      "n_wv_bright": {
        "title": "明亮的洞窟",
        "at": "明亮的洞窟",
        "kind": "clue",
        "requires": [
          "wv_flame"
        ],
        "grants": [
          "wv_bright"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "顶上的闪亮矿物把光反射成星空；洞里有两座按人类尺寸盖的焦黑建筑。"
      },
      "n_wv_spirit": {
        "title": "法师的居所",
        "at": "法师的居所",
        "kind": "encounter",
        "requires": [
          "wv_bright"
        ],
        "grants": [
          "wv_spirit"
        ],
        "on_fail": [
          "wv_spirit_kill"
        ],
        "cost": "邪灵要的是卷轴、魔法书和奥术知识，不是金币",
        "depth": 1,
        "hint": "缚灵墨梅斯克从地板里升起，说它的宝藏只属于它。给它一件它看重的魔法物品，也许能谈；它也想要观察者眼魔的命。"
      },
      "n_wv_pact": {
        "title": "法师的居所",
        "at": "法师的居所",
        "kind": "clue",
        "requires": [
          "wv_spirit"
        ],
        "grants": [
          "wv_spirit_pact",
          "wv_map"
        ],
        "on_fail": [],
        "cost": "木烟斗它永远不放手，钱币和宝石可以谈",
        "depth": 2,
        "hint": "DC 10 的游说能让它认可礼物的价值；答应替它杀观察者眼魔，它就放弃钱币和宝石，只留木烟斗，还会给你一张藏起来的地图。"
      },
      "n_wv_forge": {
        "title": "法术工厂",
        "at": "法术工厂",
        "kind": "battle",
        "requires": [
          "wv_map"
        ],
        "grants": [
          "wv_eye"
        ],
        "on_fail": [],
        "cost": "四眼柄的眼球在你们拿起任何东西时就会动手",
        "depth": 1,
        "hint": "绿焰火盆中央漂着一颗球形生物：四根眼柄，一只大眼睛。它以为矿井仍在运作，谁动它的东西它就攻击谁。"
      },
      "n_wv_eye": {
        "title": "法术工厂",
        "at": "法术工厂",
        "kind": "decision",
        "requires": [
          "wv_eye"
        ],
        "grants": [
          "wv_eye_gone"
        ],
        "on_fail": [
          "wv_eye_blind"
        ],
        "cost": "骗它需要 DC 15 的魅力；打瞎它要打出一场硬仗",
        "depth": 2,
        "hint": "DC 15 欺瞒能骗它以为你们是来解除雇佣关系的法师，它就会消失；把它彻底打瞎，它也会退回自己的位面。"
      },
      "n_wv_take": {
        "title": "法术工厂",
        "at": "法术工厂",
        "kind": "clue",
        "requires": [
          "wv_eye_gone"
        ],
        "grants": [
          "wv_forge"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 3,
        "hint": "工作台上摆着它奉命守护的最后两件东西：光明使者和龙之守护。"
      },
      "n_wv_surge": {
        "title": "轰鸣洞窟",
        "at": "轰鸣洞窟",
        "kind": "encounter",
        "requires": [
          "wv_forge"
        ],
        "grants": [
          "wv_tide"
        ],
        "on_fail": [],
        "cost": "水每两分钟涨十尺，岩架只有十五尺高",
        "depth": 1,
        "hint": "回声洞的雷鸣般波涛声来自这里：地下温泉不断沸腾，冲击漏斗状岩壁，像碎浪一样响。"
      },
      "n_wv_bed": {
        "title": "古老河床",
        "at": "古老河床",
        "kind": "clue",
        "requires": [
          "wv_tide"
        ],
        "grants": [
          "wv_bed"
        ],
        "on_fail": [],
        "cost": "",
        "depth": 1,
        "hint": "四尺高的卵石河床绕开了冶炼洞窟的不死生物——内兹纳尔还没发现这条路。"
      },
      "n_wv_collapse": {
        "title": "崩塌的洞窟",
        "at": "崩塌的洞窟",
        "kind": "battle",
        "requires": [
          "wv_bed"
        ],
        "grants": [
          "wv_gauntlet"
        ],
        "on_fail": [],
        "cost": "两只熊地精死在谷底，变形怪就会撤回去报信",
        "depth": 1,
        "hint": "裂谷深二十尺；谷底瓦砾下埋着一具戴着食人魔力量护手的矮人骷髅。谷边三只熊地精和一个伪装成卓尔的变形怪在监工。"
      },
      "n_wv_temple": {
        "title": "杜马松的神庙",
        "at": "杜马松的神庙",
        "kind": "battle",
        "requires": [
          "wv_gauntlet"
        ],
        "grants": [
          "wv_nazvar",
          "wv_statue",
          "wv_key"
        ],
        "on_fail": [],
        "cost": "四只巨蜘蛛誓死保护主人，卓尔本人随后才加入",
        "depth": 1,
        "hint": "「黑蜘蛛」那兹纳尔和四只巨蜘蛛守在矮人神的雕像前；他腰上挂着那把形似锻砧的铁钥匙。"
      },
      "n_wv_priest": {
        "title": "祭司的居所",
        "at": "祭司的居所",
        "kind": "battle",
        "requires": [
          "wv_key"
        ],
        "grants": [
          "wv_nanzhuo",
          "wv_cleared"
        ],
        "on_fail": [],
        "cost": "门锁着；南卓在里面已经被审问过很多次",
        "depth": 1,
        "hint": "南卓——寻岩者三兄弟里最年轻的——被绑在冰冷的石地上；他知道的比承认的多。"
      },
      "n_wv_nanzhuo": {
        "title": "祭司的居所",
        "at": "祭司的居所",
        "kind": "clue",
        "requires": [
          "wv_nanzhuo"
        ],
        "grants": [
          "wv_nanzhuo_saved",
          "wv_south"
        ],
        "on_fail": [],
        "cost": "他愿意跟着走，但他对矿井的了解不比你们多",
        "depth": 2,
        "hint": "铁钥匙开门；南卓感激不尽，主动提出在洞里同行。"
      },
      "n_wv_wander": {
        "title": "古老河床",
        "at": "古老河床",
        "kind": "battle",
        "requires": [
          "wv_tunnel"
        ],
        "grants": [
          "wv_wandered"
        ],
        "on_fail": [],
        "cost": "在矿井里待太久，什么东西都会撞上你们",
        "depth": 1,
        "hint": "游荡怪物：掷 d20 出 17-20 就有东西从暗处走出来——蚊蝠、食尸鬼、穴居攫怪、熊地精、骷髅、丧尸、赭果冻。"
      }
    },
    "convergences": [
      {
        "id": "c_wv_cleared",
        "title": "回声洞被清空了",
        "requires": [
          [
            "wv_cleared"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "黑蜘蛛那兹纳尔倒在杜马松的神庙前。刚铎和南卓会接管这座重新找回来的矿。"
      },
      {
        "id": "c_wv_forge",
        "title": "光明使者带出了矿井",
        "requires": [
          [
            "wv_forge"
          ]
        ],
        "kind": "ending",
        "rank": 0,
        "outcome": "两件五百年前的魔法物品在行囊里。回到凡达林，队伍就能重开凡达林契约的矿。"
      },
      {
        "id": "c_wv_south",
        "title": "南卓跟着出来了",
        "requires": [
          [
            "wv_nanzhuo_saved"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "南卓感激不尽，主动提出同行。他知道的比承认的多——这一路上他说了不少。"
      },
      {
        "id": "c_wv_spirit",
        "title": "和墨梅斯克做成了交易",
        "requires": [
          [
            "wv_spirit_pact"
          ]
        ],
        "kind": "ending",
        "rank": 1,
        "outcome": "邪灵放过了队伍，也给了一张藏起来的地图——上面标着一个副本的位置。"
      },
      {
        "id": "c_wv_map",
        "title": "带回了一张指向别处的地图",
        "requires": [
          [
            "wv_map"
          ]
        ],
        "kind": "ending",
        "rank": 2,
        "outcome": "这张地图不是回声洞的——它指向别处的副本。矿里的秘密比一座矿多。"
      }
    ],
    "clocks": [
      {
        "name": "隆隆潮浪",
        "every": 2,
        "fires": "n_wv_surge"
      }
    ]
  }
}
```
