# This Python file uses the following encoding: utf-8
# [极简绘卷版 / 自用] 拟人化点击开关
#   space  : 空间点云（OU 慢漂 + 各向异性瑞利 + 软边界）
#   timing : 点击前反应时（对数正态 + 慢OU乘子 + 长停 + 连点簇）
#   dwell  : 手指按压时长（对数正态）
#   path   : 滑动轨迹（相关高斯法向抖动 + ease-out 减速 + 小概率过冲回拉）
#   pure_time_seed : False=启动时刻+随机salt(跨会话不可复现,推荐); True=仅启动时刻(可复现调试)
# 关闭任一开关，对应环节回退上游原始逻辑；总开关 enable=False 全部回退。
from pydantic import BaseModel, Field


class Humanize(BaseModel):
    enable: bool = Field(default=True, description='humanize_enable_help')
    space_enable: bool = Field(default=True, description='humanize_space_enable_help')
    timing_enable: bool = Field(default=True, description='humanize_timing_enable_help')
    dwell_enable: bool = Field(default=True, description='humanize_dwell_enable_help')
    path_enable: bool = Field(default=True, description='humanize_path_enable_help')
    pure_time_seed: bool = Field(default=False, description='humanize_pure_time_seed_help')
