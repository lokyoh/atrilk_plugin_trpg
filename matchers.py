"""跑团插件的消息入口。

所有指令都挂在主指令「跑团」之下（``/跑团.建卡``），
不占用顶层通用词——顶层的 ``/帮助`` 由框架自带的帮助服务负责，
本插件不再重复注册。

注意：**场景切换不开放给玩家**，完全由 DM 决定并由 DM 生成相应的离场 / 入场剧情。
"""

from . import plugin

#: 建卡：按步骤引导玩家生成 DND 5e 角色卡
create_card = plugin.cmd_as_group("建卡", "创建 DND 5e 角色卡，按提示回复即可")

#: 看卡：查看已保存的角色卡
show_card = plugin.cmd_as_group("看卡", "查看自己的角色卡")

#: 骰：掷骰表达式
roll_dice = plugin.cmd_as_group("骰", "掷骰，例如 /跑团.骰 2d6+3")

#: 检定：技能 / 属性 / 豁免检定
make_check = plugin.cmd_as_group("检定", "进行检定，例如 /跑团.检定 潜行 DC15 优势")

#: 改卡：重做建卡流程中的一步
edit_card = plugin.cmd_as_group("改卡", "重做建卡中的一步，例如 /跑团.改卡 种族")

#: 开局：选择 / 生成世界与故事，开一局冒险
start_game = plugin.cmd_as_group(
    "开局",
    "开启一局冒险：选择世界与故事（或用「生成」新建），建立存档并绑定本会话",
)

#: 加入：加入一个已有存档
join_save = plugin.cmd_as_group(
    "加入", "加入一个已有存档，例如 /跑团.加入 save_20261017_ab12"
)

#: 存档：查看当前存档 / 列出全部存档
show_save = plugin.cmd_as_group("存档", "查看当前存档，或列出全部存档")

#: 继续：接着一局暂停中的冒险继续玩
resume_game = plugin.cmd_as_group(
    "继续", "继续一局暂停中的冒险；/跑团.继续 <存档ID> 可指定存档"
)

#: 结档：告诉 DM 本局可以收尾了（由 DM 在剧情里写出结局）
finish_game = plugin.cmd_as_group(
    "结档", "告诉 DM 可以收尾了：他会顺势推进结局并为本局写下收官"
)

#: 结束：暂停本会话的冒险（加「删除」则一并删除存档）
end_game = plugin.cmd_as_group(
    "结束", "暂停本会话的冒险（存档保留，可用 /跑团.继续 接着玩）；加「删除」会删除存档"
)
