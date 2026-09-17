# -*- coding: utf-8 -*-
"""
arc_click.py —— 虚无精锐结算弹窗的“弧形安全带”拟人落点。

与矩形 RuleClick 的区别
----------------------
落点不在一个矩形内散布，而是沿一条包裹结算卷轴下沿的 U 形参考中线，在其两侧
做“切向 + 法向”的二维高斯**厚带**散布，并拒绝落进卷轴主体 / 屏幕边缘；每场
结算整条弧带再做一次会话级 OU 慢漂（呼吸）。

弧只用来界定“安全可行域”，不是要求点严格落在中线上：法向 sigma 取得较大，
点在厚带内近似宽二维分布，避免把“三个固定点簇”换成“一条固定曲线”的低熵特征。

时序（点击间隔 / 按压时长）仍由全局 humanizer 按 target 名（settle 档）处理，
本类只负责空间落点；随机流也复用 humanizer 的时间播种子流，不另起全局随机态。
"""
import numpy as np

from module.atom.click import RuleClick
from module.base.humanize import humanizer, _D_SPACE


class ArcSafeClick(RuleClick):
    def __init__(self,
                 control_points,
                 reject_rects=None,
                 sigma_tangent: float = 28.0,
                 sigma_normal: float = 48.0,
                 drift_clip_x: float = 18.0,
                 drift_clip_y: float = 12.0,
                 drift_rho: float = 0.9,
                 margin: int = 8,
                 top: int = 70,
                 screen=(1280, 720),
                 resample: int = 400,
                 name: str = 'void_safe_arc_reward'):
        """
        Args:
            control_points: U 形弧控制点 [(x, y), ...]（1280x720 基准，沿弧从左到右）。
            reject_rects: 不可点矩形列表 [(x, y, w, h), ...]（卷轴主体等），落入即拒绝重采。
            sigma_tangent: 沿弧切线方向的散布标准差（px），让点不贴锚点。
            sigma_normal:  沿弧法线方向的散布标准差（px），决定弧带“厚度”；朝卷轴
                           内侧的点会被 reject_rects 拒绝，自然只保留卷轴外侧的厚云。
            drift_clip_x/y: 每场弧带整体慢漂的限幅（px）。
            drift_rho: 慢漂 OU 过程的惯性系数。
            margin/top: 屏幕边距与顶部资源栏保护（y<top 不点）。
        """
        cps = np.asarray(control_points, dtype=float)
        self._mid = self._resample(cps, resample)
        deriv = np.gradient(self._mid, axis=0)
        norm = np.linalg.norm(deriv, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        self._tan = deriv / norm
        # 法向量（切线逆时针旋转 90°）
        self._nor = np.stack([-self._tan[:, 1], self._tan[:, 0]], axis=1)

        self._reject = [tuple(r) for r in (reject_rects or [])]
        self.sigma_tangent = float(sigma_tangent)
        self.sigma_normal = float(sigma_normal)
        self.drift_clip_x = float(drift_clip_x)
        self.drift_clip_y = float(drift_clip_y)
        self.drift_rho = float(drift_rho)
        self.margin = int(margin)
        self.top = int(top)
        self.W, self.H = int(screen[0]), int(screen[1])

        # 本场弧带整体偏移（OU 慢漂状态），由 begin_settlement() 每场推进一次
        self._dx = 0.0
        self._dy = 0.0

        # 占位包围盒：coord() 已重写，roi/center 不参与实际点击，仅满足基类接口
        xmin, ymin = cps.min(axis=0)
        xmax, ymax = cps.max(axis=0)
        box = (max(0, int(xmin) - margin), max(0, int(ymin) - margin),
               int(xmax - xmin) + 2 * margin, int(ymax - ymin) + 2 * margin)
        super().__init__(roi_front=box, roi_back=box, name=name)

    @staticmethod
    def _resample(cps: np.ndarray, m: int) -> np.ndarray:
        """控制点分段线性连线后，按等弧长重采样为平滑中线。"""
        seg = np.linalg.norm(np.diff(cps, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        s = np.linspace(0.0, cum[-1], m)
        return np.column_stack([
            np.interp(s, cum, cps[:, 0]),
            np.interp(s, cum, cps[:, 1]),
        ])

    def begin_settlement(self) -> None:
        """每场结算调用一次：让整条弧带做一次会话级慢漂（呼吸），场内补点共享该偏移。"""
        rng = humanizer._rng(_D_SPACE)
        sx = self.drift_clip_x * np.sqrt(1.0 - self.drift_rho ** 2)
        sy = self.drift_clip_y * np.sqrt(1.0 - self.drift_rho ** 2)
        self._dx = float(np.clip(
            self.drift_rho * self._dx + rng.normal(0.0, sx),
            -self.drift_clip_x, self.drift_clip_x))
        self._dy = float(np.clip(
            self.drift_rho * self._dy + rng.normal(0.0, sy),
            -self.drift_clip_y, self.drift_clip_y))

    def _bad(self, x: float, y: float) -> bool:
        if x < self.margin or x > self.W - self.margin:
            return True
        if y < self.top or y > self.H - self.margin:
            return True
        for rx, ry, rw, rh in self._reject:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return True
        return False

    def coord(self) -> tuple:
        """沿弧形厚带采一个安全落点，返回 (x, y)。"""
        rng = humanizer._rng(_D_SPACE)
        mid = self._mid + np.array([self._dx, self._dy])
        m = len(mid)

        def ok(fx, fy):
            # 浮点先判，取整后再复核一次，避免边界点 round 后跨进卷轴 / 越过边距
            if self._bad(fx, fy):
                return None
            ix, iy = int(round(fx)), int(round(fy))
            return (ix, iy) if not self._bad(ix, iy) else None

        for _ in range(32):
            i = int(rng.integers(0, m))
            point = (mid[i]
                     + float(rng.normal(0.0, self.sigma_tangent)) * self._tan[i]
                     + float(rng.normal(0.0, self.sigma_normal)) * self._nor[i])
            hit = ok(float(point[0]), float(point[1]))
            if hit is not None:
                return hit
        # 兜底：沿中线稀疏扫描一个合法点
        for j in range(0, m, 7):
            hit = ok(float(mid[j][0]), float(mid[j][1]))
            if hit is not None:
                return hit
        return int(round(mid[m // 2][0])), int(round(mid[m // 2][1]))

    def coord_more(self) -> tuple:
        return self.coord()
