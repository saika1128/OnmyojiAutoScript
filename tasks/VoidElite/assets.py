from module.atom.image import RuleImage
from module.atom.click import RuleClick


class VoidEliteAssets:
    # Image Rule Assets
    # 虚无精锐活动主界面右下角的“挑战”木牌按钮（1280x720 基准）
    I_VOID_CHALLENGE = RuleImage(
        roi_front=(1100, 560, 128, 134),
        roi_back=(1075, 535, 170, 175),
        threshold=0.8,
        method="Template matching",
        file="./tasks/VoidElite/res/void_elite_challenge.png")

    # 战斗结算卷轴上的“获得奖励”标题，作为结算弹窗出现的标志
    I_VOID_REWARD = RuleImage(
        roi_front=(450, 175, 355, 100),
        roi_back=(380, 150, 470, 150),
        threshold=0.75,
        method="Template matching",
        file="./tasks/VoidElite/res/void_reward.png")

    # 结算弹窗外围的安全点击区（用户红圈标注：左 / 右 / 底部暗色空白处，
    # 避开中间奖励卷轴与顶部资源栏，点一次即“点击屏幕继续”关闭弹窗回主界面）
    C_VOID_SAFE_LEFT = RuleClick(
        roi_front=(25, 150, 210, 380), roi_back=(25, 150, 210, 380),
        name="void_safe_left")
    C_VOID_SAFE_RIGHT = RuleClick(
        roi_front=(1025, 150, 230, 380), roi_back=(1025, 150, 230, 380),
        name="void_safe_right")
    C_VOID_SAFE_BOTTOM = RuleClick(
        roi_front=(300, 600, 680, 85), roi_back=(300, 600, 680, 85),
        name="void_safe_bottom")
