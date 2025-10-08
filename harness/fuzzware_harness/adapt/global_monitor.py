# -*- coding: utf-8 -*-
"""
Global monitor (paste-over version)

把各适配器“松耦合”组织起来，供 harness 以极少改动进行接入：
- BBLogger           (bb_logger.BBLogger)
- WaitInterceptor    (wait_interceptor.WaitInterceptor)
- StateRecognizer    (state_recognizer.StateRecognizer)
- AdaptiveIRQManager (adaptive_irq.AdaptiveIRQManager)
- CrashFilter        (crash_filter.CrashFilter)

用法（任意一个都足够）：
  1) 逐个实例化并自行调用其 on_*（你现在已具备）
  2) 从这里拿一个“全家桶”，只需在合适位置喂 pc/coverage/eps：
       mon = GlobalMonitor.default(inject_fn=..., ready_irqs_fn=...)
       ...
       mon.on_step(pc)
       mon.on_coverage(total_blocks)
       mon.on_execs_per_sec(eps)
       mon.tick()              # 由它内部决定是否 consider_injection()

环境变量：
  GM_TICK_EVERY_STEPS    每多少次 on_step 尝试 tick 一次（默认 64）
  ADAPT_DEBUG             非 0 则打印少量诊断（默认 0）
"""

from __future__ import annotations
import os
import time
from typing import Optional, Iterable, Callable

from .bb_logger import BBLogger
from .wait_interceptor import WaitInterceptor
from .state_recognizer import StateRecognizer
from .adaptive_irq import AdaptiveIRQManager
from .crash_filter import CrashFilter


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class GlobalMonitor:
    def __init__(self,
                 logger: BBLogger,
                 waiter: WaitInterceptor,
                 srec: StateRecognizer,
                 adapt: AdaptiveIRQManager,
                 cflt: CrashFilter):
        self.logger = logger
        self.waiter = waiter
        self.srec = srec
        self.adapt = adapt
        self.cflt = cflt

        self._step_count = 0
        self._tick_every = int(os.environ.get('GM_TICK_EVERY_STEPS', '64'))
        self._debug = int(os.environ.get('ADAPT_DEBUG', '0')) != 0

    # ---- 透传常见回调（harness 可以只调用 GlobalMonitor） ----
    def on_step(self, pc: int) -> None:
        self._step_count += 1
        self.srec.on_step(pc)
        if self._step_count % max(1, self._tick_every) == 0:
            self.tick()

    def on_wait(self, pc: Optional[int] = None) -> None:
        self.waiter.on_wait(pc)
        self.srec.on_wait(pc)
        # wait 时来一个 tick（可能允许注入）
        self.tick()

    def on_coverage(self, total_blocks: int) -> None:
        self.logger.on_coverage(total_blocks)

    def on_new_basic_block(self) -> None:
        self.logger.on_new_basic_block()

    def on_execs_per_sec(self, eps: float) -> None:
        self.logger.on_execs_per_sec(eps)

    def on_crash(self, info: Optional[dict] = None) -> None:
        if not self.cflt.should_report(info or {}):
            if self._debug:
                print("[GM] crash filtered:", info)
            return
        self.cflt.record(info or {})
        self.adapt.on_crash()

    # ---- 节拍：是否考虑注入 ----
    def tick(self) -> bool:
        # 只有在“像是停滞/等待”的时候才尝试（进一步降低对主线的扰动）
        if not (self.srec.stalled_like() or self.srec.idle_now):
            return False
        did = self.adapt.consider_injection()
        if self._debug and did:
            print("[GM] injected at tick; cov_total=", self.logger.cov_total)
        return did

    # ---- 便利构造 ----
    @classmethod
    def default(cls,
                inject_fn: Optional[Callable[[int], bool]] = None,
                ready_irqs_fn: Optional[Callable[[], Iterable[int]]] = None) -> "GlobalMonitor":
        logger = BBLogger()
        waiter = WaitInterceptor()
        srec = StateRecognizer(wait=waiter)
        adapt = AdaptiveIRQManager(stats=logger, wait=waiter,
                                   inject_fn=inject_fn, ready_irqs_fn=ready_irqs_fn)
        cflt = CrashFilter()
        return cls(logger, waiter, srec, adapt, cflt)
