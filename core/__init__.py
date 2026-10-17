"""跑团插件核心代码。

子包划分（与 ``.agents/new_plusin_lktrpg.md`` 的约定一致）：

- ``trpg``：与具体规则体系无关的跑团基类与通用工具（骰子、检定、角色基类、规则判断器基类）
- ``dnd``：DND 专属数据对象与规则实现，全部继承自 ``trpg`` 中的基类
- ``agents``：AI（LLM）相关内容，负责世界观 / 剧情生成与 DM 行为
  （通用框架在 ``agents``，如 ``base`` / ``thinking``；DND 专属 agent 在 ``agents.dnd``）

各子包按需自行导入，这里不做聚合导入，避免仅需骰子工具时拉起 LLM 依赖。
"""
