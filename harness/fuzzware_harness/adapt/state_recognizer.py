# -*- coding: utf-8 -*-
"""
State recognizer (paste-over version)

目标：
- 识别“疑似空转/等待/自旋”态，为 ADAPT 提供更靠谱的“可注入时机”信号
- 不依赖具体 ISA，仅基于 PC 序列与 wait 事件（如 WFI/WFE）启发式
暴露：
  - on_step(pc)                 : 每一步/每若干步调用均可（频度越高越准）
  - on_wait(pc=None)            : 观察到 WFI/WFE/idle 时机（可选）
  - idle_score ∈ [0,1]          : 越高越像“空转/等待”
  - idle_now (bool)             : 当前是否判断为空转
  - stalled_like() -> bool      : 是否“像是停滞” （含最近 wait、低多样性、自旋）
可调环境变量：
  SR_WIN                         窗口大小（默认 512）
  SR_RECENT                      近端窗口用于“短期多样性”的长度（默认 64）
  SR_DIVERSITY_MAX               SR_RECENT 内唯一 PC 的最大数（默认 8）
  SR_TOP_HIT_RATIO               顶部 PC 占 SR_RECENT 比例阈值（默认 0.55）
  SR_PINGPONG_MIN                两地址来回切换达到此次数视为自旋（默认 12）
  SR_IDLE_SCORE_WAIT_BONUS_MS    最近 wait 内（毫秒）加成（默认 200）
"""

from __future__ import annotations
import os
import time
from collections import deque, Counter
from typing import Optional


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class StateRecognizer:
    def __init__(self, wait=None, cfg: Optional[dict] = None):
        env = os.environ
        cfg = cfg or {}
        self.win = int(env.get('SR_WIN', cfg.get('win', 512)))
        self.recent_len = int(env.get('SR_RECENT', cfg.get('recent', 64)))
        self.diversity_max = int(env.get('SR_DIVERSITY_MAX', cfg.get('diversity_max', 8)))
        self.top_hit_ratio = float(env.get('SR_TOP_HIT_RATIO', cfg.get('top_hit_ratio', 0.55)))
        self.pingpong_min = int(env.get('SR_PINGPONG_MIN', cfg.get('pingpong_min', 12)))
        self.wait_bonus_ms = int(env.get('SR_IDLE_SCORE_WAIT_BONUS_MS',
                                         cfg.get('idle_score_wait_bonus_ms', 200)))

        self.wait = wait
        self._pcs = deque(maxlen=max(8, self.win))
        self._recent = deque(maxlen=max(8, self.recent_len))
        self._pingpong_cnt = 0
        self._idle_score = 0.0

    # ----- 外部钩子 -----
    def on_step(self, pc: int) -> None:
        """每步（或每 N 步）调用。"""
        pc = int(pc)
        # ping-pong：A,B,A,B,...（检测简单两点往返）
        if len(self._pcs) >= 2:
            if self._pcs[-1] == pc and len(self._pcs) >= 3 and self._pcs[-3] == pc:
                self._pingpong_cnt += 1
            elif len(self._pcs) >= 1 and self._pcs[-1] != pc:
                # 发生变化但不满足 A,B,A：轻微衰减
                self._pingpong_cnt = max(0, self._pingpong_cnt - 1)
        self._pcs.append(pc)
        self._recent.append(pc)
        self._update_idle_score()

    def on_wait(self, pc: Optional[int] = None) -> None:
        """可由 wait_interceptor 转发；非必须。"""
        # 只由 wait_interceptor 维护时间戳；这里无需额外逻辑
        return

    # ----- 对外属性/方法 -----
    @property
    def idle_score(self) -> float:
        return max(0.0, min(1.0, float(self._idle_score)))

    @property
    def idle_now(self) -> bool:
        return self.idle_score >= 0.6

    def stalled_like(self) -> bool:
        """更宽松的判定：像“停滞/空转”就算 True。"""
        return self.idle_score >= 0.45

    # ----- 内部评分 -----
    def _update_idle_score(self) -> None:
        if not self._recent:
            self._idle_score = 0.0
            return

        # 1) 近端多样性：唯一 PC 越少越像循环
        uniq = len(set(self._recent))
        div_part = 1.0 - min(1.0, float(uniq) / float(max(1, self.diversity_max)))

        # 2) 顶部热点比例：近端出现最多的 PC 占比
        c = Counter(self._recent)
        top_hits = c.most_common(1)[0][1]
        top_part = min(1.0, float(top_hits) / float(len(self._recent)))
        top_part = max(0.0, (top_part - self.top_hit_ratio) / max(1e-6, (1.0 - self.top_hit_ratio)))

        # 3) ping-pong 自旋
        pingpong_part = min(1.0, float(self._pingpong_cnt) / float(max(1, self.pingpong_min)))

        # 4) 最近 wait 加成（时间窗内线性衰减）
        wait_bonus = 0.0
        if self.wait is not None:
            ts = getattr(self.wait, 'last_wait_ts', None)
            if ts:
                dt = _now_ms() - int(ts)
                if 0 <= dt <= self.wait_bonus_ms:
                    wait_bonus = 0.25 * (1.0 - float(dt) / float(max(1, self.wait_bonus_ms)))

        # 汇总（裁剪在[0,1]）
        raw = 0.55 * div_part + 0.25 * top_part + 0.20 * pingpong_part + wait_bonus
        self._idle_score = max(0.0, min(1.0, raw))
