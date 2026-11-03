"""测试用插件：不做事，只验证清单、设置、启停、命令。"""

from app.sdk import Command, CommandContext, Extension


class Demo(Extension):
    name = "demo"


async def handle_echo(cmd: CommandContext) -> str:
    return "echo " + " ".join(cmd.args)


COMMANDS = [Command("echo", handler=handle_echo, help="回声")]


def setup(ctx):
    return Demo()
