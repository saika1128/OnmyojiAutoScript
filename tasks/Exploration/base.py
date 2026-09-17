# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import re
import numpy as np
import random
from enum import Enum
from cached_property import cached_property
from datetime import timedelta, datetime

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.ReplaceShikigami.replace_shikigami import ReplaceShikigami
from tasks.Exploration.assets import ExplorationAssets
from tasks.Exploration.config import ChooseRarity, AutoRotate, AttackNumber, UpType
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_exploration, page_shikigami_records, page_main
from tasks.RealmRaid.script_task import ScriptTask as RealmRaidScriptTask
from tasks.Utils.config_enum import ShikigamiClass

from module.logger import logger
from module.base.timer import Timer
from module.exception import RequestHumanTakeover, TaskEnd, GameStuckError
from module.atom.image_grid import ImageGrid
from module.atom.animate import RuleAnimate
from module.base.utils import load_image

class Scene(Enum):
    UNKNOWN = 0  #
    WORLD = 1  # 探索大世界
    ENTRANCE = 2  # 入口弹窗
    MAIN = 3  # 探索里面
    BATTLE_PREPARE = 4  # 战斗准备
    BATTLE_FIGHTING = 5  # 战斗中
    TEAM = 6  # 组队




class BaseExploration(GameUi, GeneralBattle, GeneralRoom, GeneralInvite, ReplaceShikigami, SwitchSoul, ExplorationAssets):
    minions_cnt = 0

    @cached_property
    def _config(self):
        self.config.exploration.general_battle_config.lock_team_enable = True
        limit_time = self.config.exploration.exploration_config.limit_time
        self.limit_time: timedelta = timedelta(
            hours=limit_time.hour,
            minutes=limit_time.minute,
            seconds=limit_time.second
        )
        return self.config.model.exploration

    @cached_property
    def _match_end(self):
        return RuleAnimate(self.I_SWIPE_END)

    def get_current_scene(self, reuse_screenshot: bool = True) -> Scene:
        if not reuse_screenshot:
            self.screenshot()

        if self.appear(self.I_CHECK_EXPLORATION) and not self.appear(self.I_E_SETTINGS_BUTTON):
            return Scene.WORLD
        elif self.appear(self.I_E_EXPLORATION_CLICK) and self.appear(self.I_EXP_CREATE_TEAM):
            # 新版章节详情弹窗无红色返回(I_UI_BACK_RED失配)，改用"探索+组队"双菱形按钮识别
            return Scene.ENTRANCE
        elif self.appear(self.I_E_SETTINGS_BUTTON) or self.appear(self.I_E_AUTO_ROTATE_ON) or self.appear(self.I_E_AUTO_ROTATE_OFF):
            return Scene.MAIN
        elif self.is_in_prepare():
            return Scene.BATTLE_PREPARE
        elif self.is_in_battle():
            return Scene.BATTLE_FIGHTING
        elif self.is_in_room() or self.appear(self.I_CREATE_ENSURE):
            return Scene.TEAM

        logger.info("Unknown scene")
        return Scene.UNKNOWN

    def pre_process(self):
        explorationConfig = self._config
        if explorationConfig.switch_soul_config.enable:
            self.ui_get_current_page()
            self.ui_goto(page_shikigami_records)
            self.run_switch_soul(explorationConfig.switch_soul_config.switch_group_team)

        if explorationConfig.switch_soul_config.enable_switch_by_name:
            self.ui_get_current_page()
            self.ui_goto(page_shikigami_records)
            self.run_switch_soul_by_name(explorationConfig.switch_soul_config.group_name,
                                         explorationConfig.switch_soul_config.team_name)

        # 开启加成
        con = self.config.exploration.exploration_config
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.ui_get_current_page()
            self.ui_goto(page_main)
            self.open_buff()
            if con.buff_gold_50_click:
                self.gold_50()
            if con.buff_gold_100_click:
                self.gold_100()
            if con.buff_exp_50_click:
                self.exp_50()
            if con.buff_exp_100_click:
                self.exp_100()
            self.close_buff()

        self.ui_get_current_page()
        # 探索页面
        self.ui_goto(page_exploration)

    def post_process(self):
        # 新版详情弹窗无红色返回：用探索+组队双按钮识别，命中则点左上金黄返回回大世界，避免空等50s
        self.screenshot()
        if self.appear(self.I_E_EXPLORATION_CLICK) and self.appear(self.I_EXP_CREATE_TEAM):
            self.appear_then_click(self.I_BACK_YOLLOW, interval=3.5)
            self.sleep(1)
        self.ui_get_current_page()
        self.ui_goto(page_main)
        con = self._config.exploration_config
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.open_buff()
            self.gold_50(is_open=False)
            self.gold_100(is_open=False)
            self.exp_50(is_open=False)
            self.exp_100(is_open=False)
            self.close_buff()
        self.set_next_run(task='Exploration', success=True, finish=False)
        raise TaskEnd

    # 打开指定的章节：先检测目标章是否在当前列表，在则点；不在才下滑，且全程有界
    def open_expect_level(self):
        MAX_SWIPE = 15          # 总滑动硬上限（28章、每次跨约2行，最多8次到尾，15为冗余）
        MAX_STALE = 3           # 连续多少次滑动后列表毫无变化即熔断（手势没作用于列表）
        MAX_SELECT = 8          # 选中章节循环硬上限
        swipeCount = 0
        staleCount = 0
        lastSig = None
        # 目标章节归一化核心（"第二十八章"->"二十八章"），免疫OCR把"第"误识成"名"等
        level_name = self.config.exploration.exploration_config.exploration_level
        _m = re.search(r'([零〇一二三四五六七八九十百千两0-9]+章)', level_name)
        target_core = _m.group(1) if _m else level_name
        while 1:
            # 探索的 config
            explorationConfig = self.config.exploration

            # 判断有无目标章节
            self.screenshot()
            # 获取当前章节名
            results = self.O_E_EXPLORATION_LEVEL_NUMBER.detect_and_ocr(self.device.image)
            text1 = [result.ocr_text for result in results]
            # 判断当前章节有无目标章节：归一化到"数字+章"核心再比对，
            # 免疫OCR把"第"误识成"名"（如"名二十八章"）导致永远匹配不上
            hit = False
            for t in text1:
                mm = re.search(r'([零〇一二三四五六七八九十百千两0-9]+章)', t)
                if mm and mm.group(1) == target_core:
                    hit = True
                    break
            # 有则跳出检测
            if self.appear(self.I_E_EXPLORATION_CLICK) or hit:
                break
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            # 列表指纹：与上一轮可见章节比对，相同说明上一次滚动没让列表移动
            sig = tuple(sorted(text1))
            staleCount = staleCount + 1 if (lastSig is not None and sig == lastSig) else 0
            lastSig = sig
            # MuMu对阴阳师章节列表有鼠标滚轮优化：后台投递滚轮(向下=章号更大=找第28章)，
            # 不移动系统真实鼠标、不抢前台，更贴近PC真人；非Windows通道才回退由下向上触屏滑
            if hasattr(self, 'wheel_window_message'):
                self.wheel_window_message(1150, 400, ticks=1, down=True)
            else:
                self.swipe([1150, 500], [1150, 300])
            swipeCount += 1
            logger.info(f"Scroll level list {swipeCount} times (stale {staleCount}), levels: {text1}")
            # 连续多次纹丝不动：手势未作用于列表，提前熔断而非空转
            if staleCount >= MAX_STALE:
                raise GameStuckError(
                    f"Level list not moving after {swipeCount} swipes ({MAX_STALE}x stale), "
                    f"stuck in exploration level selection"
                )
            # 总次数硬上限，绝不无限滑动
            if swipeCount >= MAX_SWIPE:
                raise GameStuckError(
                    f"Swiped too many times ({swipeCount}), seems stuck in exploration level selection"
                )
            time.sleep(1)

        # 选中对应章节（同样有界）
        selectCount = 0
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            # 用"数字+章"核心做包含匹配，OCR把"第"误识成"名"也能命中该行并点击
            self.O_E_EXPLORATION_LEVEL_NUMBER.keyword = target_core
            if self.ocr_appear_click(self.O_E_EXPLORATION_LEVEL_NUMBER):
                self.wait_until_appear(self.I_E_EXPLORATION_CLICK, wait_time=3)
            if self.appear(self.I_E_EXPLORATION_CLICK):
                break
            if self.is_in_room():
                break
            selectCount += 1
            if selectCount >= MAX_SELECT:
                raise GameStuckError(
                    f"Clicked target level {MAX_SELECT} times but detail dialog did not appear"
                )

        return True

    # 候补：
    def enter_settings_and_do_operations(self):
        # 打开设置
        while 1:
            self.screenshot()
            if self.appear(self.I_E_OPEN_SETTINGS):
                logger.info("Open settings")
                break
            if self.is_in_battle():
                logger.warning('Opening settings failed due to now in battle')
                return
            if self.click(self.C_CLICK_SETTINGS, interval=2):
                continue

        # 候补出战数量识别
        self.screenshot()
        if not self.appear(self.I_E_OPEN_SETTINGS):
            logger.warning('Opening settings failed due to now in battle')
            return
        cu, res, total = self.O_E_ALTERNATE_NUMBER.ocr(self.device.image)
        if cu >= 10:
            logger.info("Alternate number is enough")
            self.ui_click_until_disappear(self.I_E_SURE_BUTTON)
            return
        else:
            self.add_shiki()

    def _read_alternate_count(self):
        """读取候补出战当前数量，OCR失败返回None（不抛异常打断流程）"""
        try:
            cu, res, total = self.O_E_ALTERNATE_NUMBER.ocr(self.device.image)
            return cu
        except Exception as e:
            logger.warning('Read alternate number failed: %s' % e)
            return None

    def _switch_rarity_arc(self, rarity) -> bool:
        """
        新版周年庆UI：左下角只有一个"全部"按钮，点它后稀有度沿弧形展开。
        已选中目标 -> 返回；弧形目标可见 -> 点它；否则点"全部"展开。带硬上限，绝不死循环。
        """
        if rarity == ShikigamiClass.N:
            img_selected, img_arc = self.I_E_N_RARITY, self.I_E_ARC_N
        else:
            img_selected, img_arc = self.I_E_S_RARITY, self.I_E_ENTER_CHOOSE_RARITY
        for _ in range(8):
            self.screenshot()
            # 左下筛选钮已是目标稀有度 = 已选中
            if self.appear(img_selected):
                logger.info('Rarity selected: %s' % rarity)
                return True
            if rarity == ShikigamiClass.N:
                if self.appear(img_arc):
                    self.click(self.C_CLICK_N_SHIKI, interval=1)      # 点弧形N（区域随机点）
                else:
                    self.click(self.C_CLICK_ALL_SHIKI, interval=1)    # 点"全部"展开弧形
            else:
                if not self.appear_then_click(img_arc, interval=1):
                    self.click(self.C_CLICK_ALL_SHIKI, interval=1)
            time.sleep(0.8)
        logger.error('Switch rarity failed after 8 attempts: %s' % rarity)
        return False

    # 添加式神
    def add_shiki(self, screenshot=True):
        if screenshot:
            self.screenshot()
            if not self.appear(self.I_E_OPEN_SETTINGS):
                logger.warning('Opening settings failed due to now in battle')
                return

        # 1. 先点"候补出战"标题把编辑焦点切到候补（点安全区，避免压到已候补卡而误移除）
        self.click(self.C_CLICK_STANDBY_TEAM)

        # 2. 新版弧形菜单选择稀有度（N卡/素材），失败则放弃本次添加
        choose_rarity = self._config.exploration_config.choose_rarity
        rarity = ShikigamiClass.N if choose_rarity == ChooseRarity.N else ShikigamiClass.MATERIAL
        if not self._switch_rarity_arc(rarity):
            logger.warning('Switch rarity failed, abort add_shiki')
            return

        # 3. 结果反馈式补狗粮：
        #    长按固定槽约2.8s可批量加入10个同种；灰色(已选)/满级/上锁的卡长按无增量，
        #    此时左滑一格换下一张，直到数量>=目标。所有循环都有硬上限。
        TARGET_COUNT = 40       # 候补补到该数量即停
        MAX_LONGPRESS = 12      # 长按次数硬上限
        MAX_NO_GAIN = 10        # 连续多少次滑动仍无增量即认为列表到头
        no_gain = 0
        for ops in range(MAX_LONGPRESS):
            time.sleep(0.5)
            self.screenshot()
            if not self.appear(self.I_E_OPEN_SETTINGS):
                logger.warning('Opening settings failed due to now in battle')
                return
            before = self._read_alternate_count()
            if before is not None and before >= TARGET_COUNT:
                break

            # 长按固定槽批量加入（RuleLongClick：区域随机点+随机按压时长，走humanize防封）
            self.click(self.L_ROTATE_1)
            self.device.click_record_clear()
            time.sleep(0.6)

            self.screenshot()
            after = self._read_alternate_count()
            if after is not None and before is not None and after > before:
                # 成功加入同种，继续长按（余量充足时可继续批量加）
                no_gain = 0
                continue

            # 无增量：当前槽位是灰色已选/满级/上锁卡，左滑一格换下一张
            self.swipe(self.S_SWIPE_SHIKI_TO_LEFT_ONE)
            no_gain += 1
            if no_gain >= MAX_NO_GAIN:
                logger.warning('No gain after %d swipes, stop adding (count=%s)' % (no_gain, after))
                break

        self.appear_then_click(self.I_E_SURE_BUTTON)

    # 找up按钮
    def search_up_fight(self, up_type: UpType = None):
        if up_type is None:
            up_type = self._config.exploration_config.up_type
        
        # 1. 如果选择了特定的 UP 类型 (比如达摩)
        if up_type != UpType.ALL:
            match up_type:
                case UpType.EXP:
                    find_flag = self.I_UP_EXP
                case UpType.COIN:
                    find_flag = self.I_UP_COIN
                case UpType.DARUMAA:
                    find_flag = self.I_UP_DARUMA
                case _:
                    find_flag = self.I_UP_EXP
            
            # 尝试寻找 UP 图标
            if self.appear(find_flag):
                # 获取 UP 图标的坐标和中心点
                x, y, w, h = find_flag.roi_front
                x_center, y_center = find_flag.front_center()
                
                logger.info(f'Found up type: {up_type} at {find_flag.roi_front}')

                # 缩小搜索范围 (ROI)
                # 原来左右各扩 160-200，太宽了容易甚至把隔壁怪算进来
                # 现在改为左右各扩 50-80，强制只找垂直线附近的战斗图标
                roi_back_y = max(0, y - 300)      # 向上找300像素
                roi_back_h = y - 20 - roi_back_y  #直到UP图标上方20像素截止
                
                # 左右范围缩窄：防止误触旁边的怪
                roi_back_x = max(0, x - 60)       
                roi_back_w = min(1280, x + w + 60) - roi_back_x
                
                logger.info(f'Searching sword icon in narrowed area: {roi_back_x, roi_back_y, roi_back_w, roi_back_h}')
                
                matches = self.I_NORMAL_BATTLE_BUTTON.match_all(
                    image=self.device.image,
                    threshold=0.9,
                    roi=[roi_back_x, roi_back_y, roi_back_w, roi_back_h]
                )
                
                if matches:
                    distances = []
                    for match in matches:
                        # 这里假设 match[1], match[2] 是 x, y
                        x_match = match[1] + match[3] / 2  # 战斗图标中心 X
                        y_match = match[2] + match[4] / 2  # 战斗图标中心 Y
                        
                        # 这样能完美避开“距离很近但属于隔壁怪”的情况
                        x_diff = abs(x_center - x_match)
                        y_diff = abs(y_center - y_match)
                        weighted_distance = (x_diff * 3) + y_diff
                        
                        distances.append((weighted_distance, match))
                    
                    # 按加权距离排序，取最正对着的一个
                    distances.sort(key=lambda x: x[0], reverse=False)
                    match = distances[0][1]
                    
                    roi_front = list(match[1:])  # x,y,w,h
                    self.I_NORMAL_BATTLE_BUTTON.roi_front = roi_front
                    logger.info(f"Target locked: sword at {roi_front} (aligned with UP icon)")
                    return self.I_NORMAL_BATTLE_BUTTON
            else:
                # 没找到 UP 图标，返回 None 让外层逻辑去处理(滑动或退出)
                return None

        # 2. 如果是默认情况 (UpType.ALL)，则只要有怪就打
        if self.appear(self.I_NORMAL_BATTLE_BUTTON):
            return self.I_NORMAL_BATTLE_BUTTON
            
        return None

    def activate_realm_raid(self, con_scrolls, con) -> None:
        # 判断是否开启突破票检测
        if not con_scrolls.scrolls_enable:
            return
        if self.appear(self.I_E_EXPLORATION_CLICK) and self.appear(self.I_EXP_CREATE_TEAM):
            cu, res, total = self.O_REALM_RAID_NUMBER1.ocr(self.device.image)
        else:
            cu, res, total = self.O_REALM_RAID_NUMBER.ocr(self.device.image)
        # 判断突破票数量
        if cu < con_scrolls.scrolls_threshold:
            return

        # 关闭加成
        if self.appear(self.I_RED_CLOSE):
            self.ui_click_until_disappear(self.I_RED_CLOSE)
        if self.appear(self.I_UI_CANCEL):
            self.ui_click_until_disappear(self.I_UI_CANCEL)
        if self.appear(self.I_UI_CANCEL_SAMLL):
            self.ui_click_until_disappear(self.I_UI_CANCEL_SAMLL)
        self.ui_goto(page_main)
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.open_buff()
            self.gold_50(is_open=False)
            self.gold_100(is_open=False)
            self.exp_50(is_open=False)
            self.exp_100(is_open=False)
            self.close_buff()

        # 设置下次执行行时间
        logger.info("RealmRaid and Exploration  set_next_run !")
        next_run = datetime.now() + con_scrolls.scrolls_cd
        self.set_next_run(task='Exploration', success=False, finish=False, target=next_run)
        self.set_next_run(task='RealmRaid', success=False, finish=False, target=datetime.now())
        self.set_next_run(task='MemoryScrolls', success=False, finish=False, target=datetime.now())
        raise TaskEnd

    #
    def check_exit(self) -> bool:
        # True 表示要退出这个任务
        if self.minions_cnt >= self._config.exploration_config.minions_cnt:
            logger.info('Minions count is enough, exit')
            return True
        if datetime.now() - self.start_time >= self.limit_time:
            logger.info('Exploration time limit out')
            return True
        self.activate_realm_raid(self._config.scrolls, self._config.exploration_config)
        return False

    def quit_explore(self):
        logger.info('Quit explore')
        boss_timer = Timer(15)
        boss_timer.start()
        click_yellow_button = 0 #用于保证只点一次左上返回按钮，不要直接触发连点回到主界面
        
        while 1:
            self.screenshot()
            
            #探索章节标题界面（新版无红色返回，用探索+组队双按钮识别详情弹窗）
            if self.appear(self.I_E_EXPLORATION_CLICK) and self.appear(self.I_EXP_CREATE_TEAM):
                break
            
            #探索大世界界面
            if self.appear(self.I_CHECK_EXPLORATION) and not self.appear(self.I_E_SETTINGS_BUTTON):
                break
  
            # 防止BOSS打完箱子刚落地，脚本就手快点退出了
            if self.appear_then_click(self.I_BATTLE_REWARD, interval=1.5):
                logger.info("Found battle reward during exit, picking it up.")
                boss_timer.reset()
                continue

            if boss_timer.reached():
                logger.warning('Exit timeout, force clicking back button')
                boss_timer.reset()
                self.click(self.I_UI_BACK_BLUE)
                continue

            if self.appear_then_click(self.I_E_EXIT_CONFIRM, interval=0.8):
                continue
            
            if click_yellow_button == 0:
                if self.appear_then_click(self.I_BACK_YOLLOW, interval=3.5):
                    click_yellow_button = 1
                    continue
            
            if self.appear(self.I_EXPLORATION_TITLE) or self.appear(self.I_CHECK_EXPLORATION):
                continue

    def fire(self, button) -> bool:
        self.ui_click_until_disappear(button, interval=3)
        self.screenshot()
        if (self.appear(self.I_E_SETTINGS_BUTTON) or
                self.appear(self.I_E_AUTO_ROTATE_ON) or
                self.appear(self.I_E_AUTO_ROTATE_OFF)):
            # 如果还在探索说明，这个是显示滑动导致挑战按钮不在范围内
            logger.warning('Fire button disappear, but still in exploration')
            return False
        self.run_general_battle(self._config.general_battle_config)
        self.minions_cnt += 1
        return True


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    t = BaseExploration(config, device)
    t.screenshot()

    # IMAGE_FILE = r"C:\Users\萌萌哒\Desktop\QQ20240818-163854.png"
    # image = load_image(IMAGE_FILE)
    # t.device.image = image
    while 1:
    # print(t.search_up_fight(UpType.EXP))
        t.screenshot()
        print(t.I_UP_DARUMA.test_match(t.device.image))
        time.sleep(0.2)
    from PIL import Image
    # Image.fromarray(t.device.image.astype(np.uint8)).show()

