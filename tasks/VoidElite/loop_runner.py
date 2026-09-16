# -*- coding: utf-8 -*-
"""
虚无精锐长跑启动器（dev-act）

每打满一批（999 场）后，按“打完那一刻系统时钟的分钟数”休息对应分钟数：
例如 03:27 打完就休息 27 分钟；整点 00 分按 60 分钟计（避免不休息）。
休息结束自动开始下一批 999 场，如此循环，直到手动关闭窗口。

每一批都用独立子进程运行 tasks.VoidElite.script_task，设备连接 / OCR / 计数
相互隔离，单批崩溃不会拖垮长跑；若连续 MAX_CONSECUTIVE_FAIL 批异常退出则停止，
避免资源耗尽或异常时空转。
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

BATTLE_PER_BATCH = 999
MAX_CONSECUTIVE_FAIL = 3


def project_root() -> str:
    # tasks/VoidElite/loop_runner.py 上三级即项目根目录
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pause_minutes(now: datetime) -> int:
    """按当前时钟分钟数决定休息分钟数；整点 00 分按 60 分钟，避免不休息。"""
    minute = now.minute
    return minute if minute > 0 else 60


def run_one_batch(root: str) -> int:
    env = dict(os.environ)
    env['PYTHONPATH'] = root + os.pathsep + env.get('PYTHONPATH', '')
    proc = subprocess.run(
        [sys.executable, '-u', '-m', 'tasks.VoidElite.script_task', str(BATTLE_PER_BATCH)],
        cwd=root,
        env=env,
    )
    return proc.returncode


def main() -> int:
    root = project_root()
    print(f'project root: {root}', flush=True)
    print(f'VoidElite runner: {BATTLE_PER_BATCH} battles per batch, then pause by '
          f'current minute, loop until manually closed.', flush=True)

    fail_streak = 0
    batch = 0
    while True:
        batch += 1
        start = datetime.now()
        print('=' * 60, flush=True)
        print(f'[batch {batch}] start {start:%Y-%m-%d %H:%M:%S}, '
              f'target {BATTLE_PER_BATCH} battles', flush=True)

        code = run_one_batch(root)
        end = datetime.now()
        print(f'[batch {batch}] subprocess exit code {code}, end {end:%H:%M:%S}',
              flush=True)

        if code == 0:
            fail_streak = 0
        else:
            fail_streak += 1
            print(f'[warn] abnormal batch exit, consecutive fail '
                  f'{fail_streak}/{MAX_CONSECUTIVE_FAIL}', flush=True)
            if fail_streak >= MAX_CONSECUTIVE_FAIL:
                print('[stop] reached max consecutive failed batches, runner stops. '
                      'Please check manually.', flush=True)
                return 1

        wait_min = pause_minutes(end)
        resume = end + timedelta(minutes=wait_min)
        print(f'[pause] current minute {end.minute:02d}, pause {wait_min} min, '
              f'resume at {resume:%Y-%m-%d %H:%M:%S}', flush=True)
        time.sleep(wait_min * 60)


if __name__ == '__main__':
    sys.exit(main())
