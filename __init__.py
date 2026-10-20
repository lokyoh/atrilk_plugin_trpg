from ATRI.service import Service

plugin = Service(
    "跑团", "跑团插件", "0.2.0", Service.ServiceType.GAME, "l_o_o_k"
).main_cmd("跑团")

# 插件设置（WebUI「插件设置」里改）：逐人结算 / 历史条数 / 战场地图频率等
from .config import TrpgConfig  # noqa: E402

config: TrpgConfig = plugin.add_plugin_config(TrpgConfig)

from . import handlers as handlers  # noqa: E402
