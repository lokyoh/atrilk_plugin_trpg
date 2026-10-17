from ATRI.service import Service

plugin = Service(
    "跑团", "跑团插件", "0.0.1", Service.ServiceType.GAME, "l_o_o_k"
).main_cmd("跑团")

from . import handlers as handlers  # noqa: E402
