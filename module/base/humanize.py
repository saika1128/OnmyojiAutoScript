# -*- coding: utf-8 -*-
"""
humanize.py —— 自用极简版拟人化层（空间点云 / 时序 / 按压 / 滑动轨迹）

设计原则
--------
1. 时间播种（不是"当前墙钟秒的初等函数"）：
   会话种子 = 启动 perf_counter_ns()，默认再异或一次一次性随机 salt（可关，关后为
   纯时间复现模式）。每次采样按 (用途域, 全局自增序号) 派生独立子流：
     - 给定启动时刻、相同调用顺序 -> 整段序列可复现（便于调试）；
     - 启动时刻相差 1ms / 每次运行 -> 序列完全不同；
     - 同一秒内连点因序号不同而不同；
   因此不会出现 f(墙钟秒) 那种与时钟锁相的固定周期。
2. 空间：会话级慢漂重心(OU) + 各向异性瑞利即时抖动 + 软边界；几何全部按 roi 自适应，
   结算/战斗为宽档、普通按钮为窄档（点得准但不机械）。
3. 时序：对数正态反应时 + 慢 OU 乘子 + 重尾"走神"长停 + 连点簇（哒哒…顿）。
4. 按压 dwell：对数正态（替换原 triangular/均匀）。
5. 滑动轨迹：在既有三次贝塞尔点列上叠加 AR(1) 相关高斯法向抖动、末段 ease-out 减速、
   小概率沿运动方向过冲再慢速回拉（仅滑动，按钮点击不做过冲）。

本模块只依赖 numpy / 标准库，可脱离项目单独 import 做蒙特卡洛回归。
挂载点在开关关闭时回退原上游逻辑，本模块不强制生效。
"""
import os
import time
import numpy as np

# 用途域标签（混入种子，保证不同用途随机流互不相关）
_D_SPACE = 1
_D_TIME = 2
_D_DWELL = 3
_D_PATH = 4

PROFILE_BATTLE = 'battle'   # 战斗中随机点击（C_RANDOM_CLICK）
PROFILE_SETTLE = 'settle'   # 结算/过场安全随机点击（SAFE_RANDOM_CLICK / C_REWARD_x）
PROFILE_BUTTON = 'button'   # 其它一切普通按钮

# 每多少个空间点重摇一次"一局会话"级偏置（模拟一局内手感缓慢变化）
_SESSION_POINTS = 120


def profile_of_name(name):
    """按点击对象名字路由拟人档位。

    注意顺序：'safe_random_click' 同时包含子串 'random_click'，
    必须先判结算(settle)，否则会被战斗(battle)抢先匹配。
    """
    n = (str(name) or '').lower()
    if 'safe_random' in n or 'reward' in n:
        return PROFILE_SETTLE
    if 'random_click' in n:
        return PROFILE_BATTLE
    return PROFILE_BUTTON


def _point_in_rect(x, y, rect):
    rx, ry, rw, rh = rect
    return rx <= x <= rx + rw and ry <= y <= ry + rh


class Humanizer:
    def __init__(self):
        # 总开关与子开关（默认启用；挂载点在关闭时走原上游表达式）
        self.enable = True
        self.space_enable = True
        self.timing_enable = True
        self.dwell_enable = True
        self.path_enable = True
        # False = 启动时刻 + 一次性随机 salt（跨会话不可复现，推荐）
        # True  = 仅用启动时刻（纯时间复现，调试用）
        self.pure_time_seed = False
        self.reset()

    def configure(self, **kw):
        for k, v in kw.items():
            if v is None:
                continue
            if hasattr(self, k):
                setattr(self, k, v)
        # 改播种模式后重建会话种子
        if 'pure_time_seed' in kw:
            self.reset()
        return self

    def reset(self):
        salt = 0 if self.pure_time_seed else int.from_bytes(os.urandom(8), 'little')
        self.session_seed = (time.perf_counter_ns() ^ salt) & 0xFFFFFFFFFFFFFFFF
        self._nonce = 0
        self._space_state = {}   # 每档位的空间会话状态
        self._time_state = {}    # 每档位的时序状态机

    # ------------------------------------------------------------------ #
    # 时间播种
    # ------------------------------------------------------------------ #
    def _rng(self, domain):
        """派生一个独立子随机流：与 (会话种子, 用途域, 全局序号) 绑定。"""
        self._nonce += 1
        seed_seq = np.random.SeedSequence(
            [self.session_seed, int(domain), int(self._nonce)]
        )
        return np.random.default_rng(seed_seq)

    # ------------------------------------------------------------------ #
    # 空间点云
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rayleigh_offset(rng, ax, ay):
        """各向异性瑞利抖动：瑞利半径 + 均匀方向，再乘 x/y 尺度。"""
        radius = rng.rayleigh(0.5)          # 均值≈0.63，95 分位≈1.12
        theta = rng.uniform(0.0, 2.0 * np.pi)
        return ax * radius * np.cos(theta), ay * radius * np.sin(theta)

    def _space_session(self, profile, w, h):
        st = self._space_state.get(profile)
        if st is None or st['count'] >= _SESSION_POINTS:
            rng = self._rng(_D_SPACE)
            st = {
                'count': 0,
                'bx': float(rng.normal(0, 0.05 * w)),    # 一局会话级 x 偏置
                'by': float(rng.normal(0, 0.04 * h)),    # 一局会话级 y 偏置
                'q': float(rng.uniform(-0.045 * h, 0.045 * h)),  # 上下不对称
                'gx': 0.0, 'gy': 0.0,                   # OU 慢漂重心
            }
            self._space_state[profile] = st
        return st

    def coord(self, roi, name, blacklist=None):
        """
        在 roi=(x,y,w,h) 内生成一个拟人点，返回 (int, int)，保证落在 roi 软边界内。

        Args:
            roi: (x, y, w, h)
            name: 点击对象名（用于档位路由）
            blacklist: 战斗档可选黑名单矩形列表 [(x,y,w,h), ...]，落入则拒绝重采
        """
        x, y, w, h = (int(v) for v in roi)
        if w <= 1 or h <= 1:
            return x, y
        profile = profile_of_name(name)
        rng = self._rng(_D_SPACE)
        cx, cy = x + w / 2.0, y + h / 2.0
        bx = by = gx = gy = 0.0

        if profile == PROFILE_BUTTON:
            # 普通按钮：围绕中心小幅瑞利，不做慢漂，保证点得准
            ax, ay = 0.16 * w, 0.16 * h
        else:
            st = self._space_session(profile, w, h)
            st['count'] += 1
            rho = 0.82
            st['gx'] = rho * st['gx'] + (1 - rho) * st['bx'] + rng.normal(0, 0.06 * w)
            st['gy'] = rho * st['gy'] + (1 - rho) * (st['by'] + st['q']) + rng.normal(0, 0.05 * h)
            st['gx'] = float(np.clip(st['gx'], -0.14 * w, 0.14 * w))
            st['gy'] = float(np.clip(st['gy'], -0.13 * h, 0.13 * h))
            gx, gy = st['gx'], st['gy']
            bx, by = st['bx'], st['by'] + st['q']
            if profile == PROFILE_BATTLE:
                # 战斗随机区：拉宽成簇（C 方案，不反转，平滑越过中线）
                ax, ay = 0.34 * w, 0.23 * h
            else:
                # 结算区：几何自适应（竖条/横条都按各自尺寸）
                ax, ay = 0.30 * w, 0.30 * h

        def sample_once():
            px, py = self._rayleigh_offset(rng, ax, ay)
            inset = float(rng.uniform(2, 5))           # 软边界随机内缩
            ox = float(np.clip(cx + bx + gx + px, x + inset, x + w - inset))
            oy = float(np.clip(cy + by + gy + py, y + inset, y + h - inset))
            return ox, oy

        ox, oy = sample_once()
        if profile == PROFILE_BATTLE and blacklist:
            # 拒绝采样
            for _ in range(12):
                if not any(_point_in_rect(ox, oy, r) for r in blacklist):
                    break
                ox, oy = sample_once()
            # 兜底：仍落入黑名单则沿"黑名单中心->点"方向推到该黑框边界外
            for r in blacklist:
                if _point_in_rect(ox, oy, r):
                    rcx, rcy = r[0] + r[2] / 2.0, r[1] + r[3] / 2.0
                    dx, dy = ox - rcx, oy - rcy
                    if abs(dx) / max(r[2], 1) >= abs(dy) / max(r[3], 1):
                        ox = r[0] if dx < 0 else r[0] + r[2]
                    else:
                        oy = r[1] if dy < 0 else r[1] + r[3]
            ox = float(np.clip(ox, x + 2, x + w - 2))
            oy = float(np.clip(oy, y + 2, y + h - 2))
        return int(round(ox)), int(round(oy))

    # ------------------------------------------------------------------ #
    # 时序
    # ------------------------------------------------------------------ #
    # median=反应中位(s), cv=变异系数, lo/hi=截断, p_long=长停顿概率,
    # p_burst=进入连点簇概率
    _TIME_CONF = {
        PROFILE_BATTLE: dict(median=0.34, cv=0.33, lo=0.16, hi=0.95,
                             p_long=0.02, p_burst=0.20),
        PROFILE_SETTLE: dict(median=0.62, cv=0.39, lo=0.28, hi=1.60,
                             p_long=0.04, p_burst=0.15),
        PROFILE_BUTTON: dict(median=0.26, cv=0.35, lo=0.12, hi=0.80,
                             p_long=0.02, p_burst=0.10),
    }

    def _time_session(self, profile):
        st = self._time_state.get(profile)
        if st is None:
            st = {'ou': 1.0, 'burst_left': 0, 'burst_cool': False}
            self._time_state[profile] = st
        return st

    def pre_click_delay(self, name):
        """返回本次点击"前"应等待的秒数（即相邻点击间隔拟人化）。关闭时返回 0。"""
        if not (self.enable and self.timing_enable):
            return 0.0
        profile = profile_of_name(name)
        cfg = self._TIME_CONF[profile]
        st = self._time_session(profile)
        rng = self._rng(_D_TIME)

        # 连点簇内部：短间隔
        if st['burst_left'] > 0:
            st['burst_left'] -= 1
            if st['burst_left'] == 0:
                st['burst_cool'] = True
            return float(np.clip(rng.uniform(0.12, 0.30), 0.0, 3.0))
        # 一簇结束后的回顿
        if st['burst_cool']:
            st['burst_cool'] = False
            return float(rng.uniform(0.5, 1.1))

        # 慢 OU 乘子，消除固定间隔尖峰
        st['ou'] = 0.9 * st['ou'] + 0.1 + rng.normal(0, 0.03)
        st['ou'] = float(np.clip(st['ou'], 0.85, 1.15))

        # 重尾"走神"长停顿
        if rng.random() < cfg['p_long']:
            return float(np.clip(rng.uniform(0.8, 2.8), 0.0, 3.0))

        # 对数正态反应时
        sigma = float(np.sqrt(np.log(1.0 + cfg['cv'] ** 2)))
        mu = float(np.log(cfg['median']))
        delay = float(np.exp(mu + sigma * rng.normal())) * st['ou']
        delay = float(np.clip(delay, cfg['lo'], cfg['hi']))

        # 以 p_burst 开启一簇（影响后续 1~3 次为短间隔），本次也略快
        if rng.random() < cfg['p_burst']:
            st['burst_left'] = int(rng.integers(1, 4))
            delay *= 0.8
        return max(delay, 0.0)

    def dwell_ms(self):
        """手指按下到抬起的按压时长(ms)，对数正态；关闭返回 None 由挂载点回退。"""
        if not (self.enable and self.dwell_enable):
            return None
        rng = self._rng(_D_DWELL)
        median, cv = 78.0, 0.22
        sigma = float(np.sqrt(np.log(1.0 + cv ** 2)))
        value = float(np.exp(np.log(median) + sigma * rng.normal()))
        return int(np.clip(value, 45, 140))

    def touch_jitter_ms(self, lo=20, hi=90):
        """down 前 / up 后的微小停顿(ms)。"""
        rng = self._rng(_D_DWELL)
        return int(rng.integers(lo, hi + 1))

    # ------------------------------------------------------------------ #
    # 滑动轨迹
    # ------------------------------------------------------------------ #
    def refine_swipe(self, points):
        """
        在既有贝塞尔点列上拟人化，返回 (new_points, waits_ms)。
        new_points: list[[x,y], ...]；waits_ms[i] 为到达 new_points[i] 后等待(ms)，
        长度与 new_points 对齐（首点 down 不等待，挂载点从第二个点起取 waits）。
        """
        pts = [np.array(p, dtype=float) for p in points]
        n = len(pts)
        rng = self._rng(_D_PATH)
        if n < 2 or not (self.enable and self.path_enable):
            base = [[int(round(p[0])), int(round(p[1]))] for p in pts]
            waits = [0] + [int(rng.integers(6, 16)) for _ in range(max(n - 1, 0))]
            return base, waits

        direction = pts[-1] - pts[0]
        norm = float(np.linalg.norm(direction))
        perp = np.array([-direction[1], direction[0]]) / norm if norm > 1e-6 else np.array([0.0, 0.0])

        out, waits = [], []
        jitter_prev = 0.0
        for i, p in enumerate(pts):
            progress = i / max(n - 1, 1)
            # AR(1) 相关法向抖动，幅度随接近终点收敛到 0（瞄准收敛）
            jitter_prev = 0.6 * jitter_prev + 0.4 * rng.normal(0, 1.0)
            amp = 2.6 * (1.0 - progress)
            q = p + perp * (jitter_prev * amp)
            out.append(q)
            # ease-out：越接近终点每步越慢
            step_wait = rng.uniform(7, 12) * (1.0 + 1.8 * progress ** 2) + rng.uniform(-1.5, 1.5)
            waits.append(int(max(5, round(step_wait))) if i > 0 else 0)

        # 小概率过冲：沿运动方向冲过一点，再 2~3 个慢速点回拉到终点
        if rng.random() < 0.10 and norm > 30:
            unit = direction / norm
            over = pts[-1] + unit * (norm * float(rng.rayleigh(0.04)))
            out.append(over)
            waits.append(int(rng.integers(14, 25)))
            back = int(rng.integers(2, 4))
            for k in range(1, back + 1):
                t = k / back
                out.append(over + (pts[-1] - over) * t)
                waits.append(int(rng.integers(16, 29)))

        new_points = [[int(round(p[0])), int(round(p[1]))] for p in out]
        return new_points, waits


# 全局单例（挂载点统一使用它，保证会话状态/时间播种全局连续）
humanizer = Humanizer()
