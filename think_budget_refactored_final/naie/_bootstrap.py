"""NAIE Bootstrap — 统一处理 sys.path 和环境变量设置。

所有入口脚本 (run_*.py, api_server.py) 应在文件顶部 import 此模块:
    import naie._bootstrap

此模块确保:
  1. 项目根目录和父目录在 sys.path 中 (无需手动 sys.path.insert)
  2. NO_PROXY=* 已设置 (绕过企业代理访问内部模型端点)

注意: 此模块被 import 时必须有 naie 包在 sys.path 中。
      入口脚本通常从项目根目录运行，因此当前目录 '.' 在 sys.path 中，
      import naie._bootstrap 可直接工作。
"""
import sys
import os

# ─── 1. sys.path — 确保项目根目录和父目录可导入 ───
# __file__ = naie/_bootstrap.py, 其父目录 = 项目根, 祖父目录 = 工作区
_this_dir = os.path.dirname(os.path.abspath(__file__))          # .../naie
_project_root = os.path.dirname(_this_dir)                      # .../think_budget_refactored_final
_workspace = os.path.dirname(_project_root)                     # .../workspace

for p in [_workspace, _project_root]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ─── 2. NO_PROXY — 绕过企业代理 ───
# NAIE 使用内部模型端点 (141.73.x.x)，必须绕过公司代理
if 'NO_PROXY' not in os.environ:
    os.environ['NO_PROXY'] = '*'
