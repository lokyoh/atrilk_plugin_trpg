"""跑团插件的可调设置（WebUI「插件设置」里改，即时生效）。

这些开关主要用来换成本：跑团的开销几乎全在「每轮调几次模型 × 每次塞多少上下文」，
默认值按「体验优先」，想省 token 就按下面注释调。
"""

from ATRI.configs import Field, PluginConfig


class TrpgConfig(PluginConfig):
    """
    跑团设置：
    """

    partial_narration = Field(
        bool,
        False,
        "逐人结算",
        "每名玩家提交后立刻只结算他这一行动（最后一人做整轮总结）；"
        "关闭则等全员提交后一次性主持——调用次数从 n 次降回 1 次，但前几名玩家要等",
    )
    partial_min_players = Field(
        int,
        3,
        "逐人结算最少人数",
        "参与玩家达到该人数才逐人结算（2 人局关掉最省），填 99 等于彻底关闭",
        min=1,
        max=99,
    )
    history_events = Field(
        int,
        5,
        "最近事件条数",
        "每轮塞给 DM 的历史事件条数（逐人结算时自动减半），越少越省 token",
        min=0,
        max=20,
    )
    npc_roster_limit = Field(
        int,
        6,
        "NPC 名单条数",
        "提示词里最多列几名具名 NPC（旧伤 / 关系）",
        min=0,
        max=20,
    )
    battle_map_always = Field(
        bool,
        False,
        "每轮附战场地图",
        "关闭时只在站位或地形变化的那一轮附地图，其余轮只给坐标与距离，省一大块 token",
    )
    world_bible_max_chars = Field(
        int,
        800,
        "世界圣经字数上限",
        "每轮塞给 DM / 按需生成的世界圣经（专有名词登记）最多多少字；"
        "填 0 不限制——实体多的世界建议适当调大，避免重要名字被截掉",
        min=0,
        max=5000,
    )
    auto_enemy_turns = Field(
        bool,
        True,
        "敌人回合自动结算",
        "每轮由系统替敌人打完机械部分：挑目标、够不着就先靠近、选招（多攻按数据卡）、"
        "掷骰扣血，结果直接播给玩家；吐息 / 施法 / 撤退这类战术仍由 DM 叙述。"
        "关闭后敌人不再自动出手（战斗只剩玩家一侧结算），一般只在排查问题时关",
    )
