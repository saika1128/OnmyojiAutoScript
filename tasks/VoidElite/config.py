from pydantic import BaseModel, Field

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class VoidEliteConfig(BaseModel):
    # 挑战次数上限（活动票/饭团耗尽前按此循环，达到即停）
    battle_count: int = Field(title='挑战次数', default=999, ge=1, le=999,
                              description='void_elite_battle_count_help')


class VoidElite(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    void_elite_config: VoidEliteConfig = Field(default_factory=VoidEliteConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
