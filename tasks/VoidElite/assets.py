from module.atom.image import RuleImage
from tasks.VoidElite.arc_click import ArcSafeClick


class VoidEliteAssets:
    # Image Rule Assets
    # 虚无精锐活动主界面右下角的“挑战”木牌按钮（1280x720 基准）
    I_VOID_CHALLENGE = RuleImage(
        roi_front=(1100, 560, 128, 134),
        roi_back=(1075, 535, 170, 175),
        threshold=0.8,
        method="Template matching",
        file="./tasks/VoidElite/res/void_elite_challenge.png")

    # 战斗结算背景板上“胜利”大字的“利”字上半部分，作为结算画面出现的标志。
    # 不用卷轴上的“获得奖励”标题：标题会随奖励格数（一行/两行）缩放并上下漂移，
    # 而背景“胜利”大字的位置与尺寸固定，不受奖励卷轴内容/入场动画影响。
    I_VOID_REWARD = RuleImage(
        roi_front=(869, 105, 148, 88),
        roi_back=(814, 70, 258, 158),
        threshold=0.8,
        method="Template matching",
        file="./tasks/VoidElite/res/void_reward.png")

    # 结算弹窗外围的“弧形安全带”：一条包裹奖励卷轴下沿的 U 形中线，落点在其两侧
    # 厚带内二维散布、每场整体慢漂，并拒绝落进卷轴主体 / 顶部资源栏 / 屏幕边缘
    # （替代原先左 / 右 / 底三个固定矩形点簇，避免“每次都点同几处中心”的机械特征）。
    # 点一次即“点击屏幕继续”关闭弹窗回主界面。控制点由用户手绘 U 弧提取（1280x720）。
    C_VOID_SAFE_ARC = ArcSafeClick(
        control_points=[
            (111.4, 414.4), (182.4, 509.0), (244.1, 563.2), (345.3, 618.9),
            (471.8, 648.1), (602.8, 650.7), (733.7, 647.7), (864.4, 642.7),
            (991.0, 614.9), (1047.7, 557.4), (1123.4, 452.5),
        ],
        # 卷轴 / 弹窗主体不可点区 (x, y, w, h)
        reject_rects=[(245, 150, 760, 425)],
        name="void_safe_arc_reward")
