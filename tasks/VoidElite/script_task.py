# This Python file uses the following encoding: utf-8
"""
虚无精锐活动自动挑战（dev-act）

极简循环，战前/战斗复用探索同款通用战斗组件 GeneralBattle 的战前准备，
战斗结算按活动界面定制：
    活动主界面(挑战按钮) -> 点挑战 -> 准备/战斗 -> 胜利 ->
    识别背景“胜利”大字的“利”字上半 -> 沿弹窗外围弧形安全带点一次关闭 ->
    回到活动主界面 -> 再挑战 …… 直到达到“挑战次数”上限。

与探索的差别：
  1. 探索在 MAIN 场景滑动找怪，本活动“挑战”按钮固定在右下角，直接点；
  2. 活动结算不是通用胜利徽章/“获得奖励”按钮，而是卷轴弹窗，以背景固定的
     “利”字上半为结算标志，在包裹卷轴下沿的弧形安全带（厚带二维散布）点击关闭；
  3. 本版不做战斗内随机点滑（random_click_swipt 关闭）。

所有点击均走 self.* 规则通道，继承 humanize 偏移与随机；所有循环均带硬上限。
启动时需手动停在虚无精锐活动主界面（不做活动入口导航）。
"""
import random
import time
from time import sleep

from module.exception import TaskEnd, GameStuckError
from module.logger import logger

from tasks.GameUi.game_ui import GameUi
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.VoidElite.assets import VoidEliteAssets


class ScriptTask(GameUi, GeneralBattle, VoidEliteAssets):

    # 连续识别不到“挑战”且不在战斗/结算的最大轮数
    MAX_UNKNOWN = 40
    # 连续无法开战 / 战斗失败的最大次数（票或饭团耗尽、异常弹窗等）
    MAX_FAIL = 3
    # 单场战斗（含加载、准备、结算弹窗出现）的硬超时，秒
    BATTLE_TIMEOUT = 240
    # 关闭结算弹窗后等待回到活动主界面（挑战按钮重现）的最长时间，秒
    BACK_TO_MAIN_TIMEOUT = 20
    # 结算弹窗最多补点几次（正常一次即可）
    MAX_DISMISS_CLICK = 3

    def run(self):
        # 固定阵容直接点准备，不切预设、不加 buff
        self.config.void_elite.general_battle_config.lock_team_enable = True
        general_battle_config = self.config.void_elite.general_battle_config
        target_count = self.config.void_elite.void_elite_config.battle_count
        self.current_count = 0

        logger.hr('VoidElite')
        logger.info(f'VoidElite start, target battle count = {target_count}')

        self.screenshot()
        unknown_cnt = 0
        fail_cnt = 0

        while self.current_count < target_count:
            self.screenshot()

            # 1) 已在战斗准备 / 战斗中：接管打完（启动时已在战斗等兜底路径）
            if self.is_in_prepare(False) or self.is_in_battle(False):
                win = self.run_void_battle(general_battle_config,
                                           do_before=self.is_in_prepare(False))
                fail_cnt = self._count_result(win, fail_cnt)
                unknown_cnt = 0
                if fail_cnt >= self.MAX_FAIL:
                    raise GameStuckError(
                        f'VoidElite battle failed {fail_cnt} times in a row')
                continue

            # 2) 活动主界面：挑战按钮出现 -> 点开打一场
            if self.appear(self.I_VOID_CHALLENGE):
                win = self.fire_challenge(general_battle_config)
                fail_cnt = self._count_result(win, fail_cnt)
                unknown_cnt = 0
                if fail_cnt >= self.MAX_FAIL:
                    raise GameStuckError(
                        f'VoidElite challenge failed {fail_cnt} times in a row')
                continue

            # 3) 结算残留兜底：活动卷轴 / 通用胜利 / 通用奖励
            if self.appear(self.I_VOID_REWARD):
                self.dismiss_void_reward()
                unknown_cnt = 0
                continue
            if self.appear_then_click(self.I_WIN, interval=1.0):
                unknown_cnt = 0
                continue
            if (self.appear_then_click(self.I_REWARD, interval=1.5) or
                    self.appear_then_click(self.I_REWARD_GOLD, interval=1.5)):
                unknown_cnt = 0
                continue

            # 4) 未知界面：有界等待，绝不盲点（尤其不点左上角，避免误触头像/返回）
            unknown_cnt += 1
            logger.info(f'VoidElite unknown scene, wait ({unknown_cnt})')
            sleep(random.uniform(0.6, 1.1))
            if unknown_cnt >= self.MAX_UNKNOWN:
                raise GameStuckError(
                    f'VoidElite stuck: no challenge button and not in battle '
                    f'for {unknown_cnt} rounds')

        logger.info(f'VoidElite finished, total count = {self.current_count}')
        self.set_next_run(task='VoidElite', success=True, finish=False)
        raise TaskEnd('VoidElite')

    @staticmethod
    def _count_result(win, fail_cnt: int) -> int:
        return fail_cnt + 1 if win is False else 0

    def fire_challenge(self, general_battle_config) -> bool:
        """
        点“挑战”直到按钮消失（进入准备/战斗），随后打完一场并收活动结算。
        参照探索 BaseExploration.fire()：按钮点不掉说明没进战斗，返回 False。
        """
        self.ui_click_until_disappear(self.I_VOID_CHALLENGE, interval=2)
        self.screenshot()
        if self.appear(self.I_VOID_CHALLENGE):
            logger.warning('Challenge button still there after click, did not enter battle')
            return False
        return self.run_void_battle(general_battle_config, do_before=True)

    def run_void_battle(self, general_battle_config, do_before: bool) -> bool:
        """战前准备（复用 GeneralBattle）+ 活动定制的战斗等待与收奖励。"""
        self.current_count += 1
        logger.hr('General battle start', 2)
        logger.info(f'Current count: {self.current_count}')
        if do_before:
            # 锁阵容时 battle_before 直接点准备；不切预设、不加 buff
            self.battle_before(None, general_battle_config)
        return self.void_battle_wait()

    def void_battle_wait(self) -> bool:
        """
        等战斗结束并处理活动结算。
        BATTLE_STATUS_S 在设备长等待白名单内（300s），战斗中不会被误判卡死；
        另以 BATTLE_TIMEOUT 作为本活动单场硬上限。
        """
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        logger.info('Start battle process')
        deadline = time.time() + self.BATTLE_TIMEOUT

        while time.time() < deadline:
            self.screenshot()

            # 失败
            if self.appear(self.I_FALSE, threshold=0.8):
                logger.info('Battle result is false')
                self.appear_then_click(self.I_FALSE, threshold=0.6)
                sleep(1.0)
                return False

            # 战斗等待阶段不再补点“准备”：battle_before 内部已负责在准备页循环点
            # 准备；其超时返回后若在此再补点，会与战斗加载/界面切换竞争，且每次点击
            # 会清空 BATTLE_STATUS_S 长等待标记，反而把正常加载误判成卡死（对齐上游
            # a413ea6f 撤回“battle_before 超时补点准备”）。真没进战斗时交由
            # BATTLE_TIMEOUT 超时按失败处理，由上层重启 / 导航自恢复。

            # 活动结算：出现“获得奖励”卷轴 -> 安全区点掉 -> 回主界面
            if self.appear(self.I_VOID_REWARD):
                logger.info('Void reward appears, dismiss it in safe area')
                return self.dismiss_void_reward()

            # 通用胜利徽章 / 通用奖励兜底（活动一般不出现）
            if self.appear(self.I_WIN, threshold=0.8):
                action = random.choice([self.C_WIN_1, self.C_WIN_2, self.C_WIN_3])
                if self.appear_then_click(self.I_WIN, action=action, interval=0.6):
                    sleep(0.8)
                    continue
            if self.appear(self.I_REWARD, threshold=0.6):
                action = random.choice([self.C_REWARD_1, self.C_REWARD_2, self.C_REWARD_3])
                if self.appear_then_click(self.I_REWARD, action=action, interval=1.2):
                    sleep(0.8)
                    continue
            if self.appear_then_click(self.I_REWARD_GOLD, interval=1.2):
                sleep(0.8)
                continue

            # 战斗中 / 加载 / 过渡：等待（本版不做战斗内随机点滑）
            sleep(0.4)

        raise GameStuckError('VoidElite battle/settlement timeout '
                             f'after {self.BATTLE_TIMEOUT}s')

    def dismiss_void_reward(self) -> bool:
        """
        在结算弹窗外围安全随机位置点击关闭（正常一次即可），
        然后等待回到活动主界面（挑战按钮重现）。
        """
        arc = self.C_VOID_SAFE_ARC
        # 本场结算的弧带整体慢漂一次；同一场若需补点，共享该偏移、各自在厚带内采样
        arc.begin_settlement()
        for click_i in range(self.MAX_DISMISS_CLICK):
            self.screenshot()
            if not self.appear(self.I_VOID_REWARD):
                break
            logger.info(f'Click void reward safe arc, try {click_i + 1}')
            self.appear_then_click(self.I_VOID_REWARD, action=arc, interval=1.0)
            sleep(1.2)

        self.screenshot()
        if self.appear(self.I_VOID_CHALLENGE):
            logger.info('Back to void elite main page')
            return True

        ok = self.wait_until_appear(self.I_VOID_CHALLENGE,
                                   wait_time=self.BACK_TO_MAIN_TIMEOUT)
        if not ok:
            logger.warning('Void reward dismissed but challenge button not back')
            return False
        logger.info('Back to void elite main page')
        return True


if __name__ == '__main__':
    import sys

    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas')
    device = Device(config)
    task = ScriptTask(config, device)
    # 试跑：命令行传次数，如  python -m tasks.VoidElite.script_task 1
    # 不传则用配置里的 999
    if len(sys.argv) > 1:
        try:
            config.void_elite.void_elite_config.battle_count = int(sys.argv[1])
        except ValueError:
            pass
    try:
        task.run()
    except TaskEnd:
        logger.info('VoidElite TaskEnd')
