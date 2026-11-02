"""插件运行时：清单解析、加载、钩子分发。

插件放在项目根的 `plugins/`，每个插件一个文件夹：

    plugins/stats/
        plugin.toml     元数据与设置项声明（不导入代码就能读，坏插件也只是显示成错误卡）
        plugin.py       代码，导出 setup(ctx)，可选 router
        ...

运行期数据写到 `data/plugin_data/<name>/`，插件拿到的 `ctx.data_dir` 就是这个目录。
"""

from __future__ import annotations

from pathlib import Path

from ..config import ROOT

PLUGIN_DIR = ROOT / "plugins"
DATA_DIR = ROOT / "data" / "plugin_data"

META_FILE = "plugin.toml"
ENTRY_FILE = "plugin.py"
