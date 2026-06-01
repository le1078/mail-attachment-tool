"""全局常量定义模块。

集中管理项目中跨模块共享的常量值。
"""

from pathlib import Path

# 文件名长度限制
MAX_FILENAME_LEN = 200

# 下载历史记录相关常量
MAX_HISTORY_RECORDS = 2000
HISTORY_FILE = Path(__file__).parent / "download_history.json"