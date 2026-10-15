from ATRI.service import Service

plugin = Service(
    "HelloWorld",
    "一个ATRI-LK插件示例模板。",
    "1.0.0",
    Service.ServiceType.OTHER,
    "l_o_o_k"
)

hello = plugin.on_command("hello", "helloworld")

@hello.handle()
async def _():
    await hello.send("World!")
