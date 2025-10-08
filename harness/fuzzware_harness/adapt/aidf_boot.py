# -*- coding: utf-8 -*-
from __future__ import annotations
import os, logging
from . import bb_logger
log = logging.getLogger("emulator")

def boot(uc):
    # 1) 永远先启用 BB 记录（除非 AIDF_BB_LOG=0）
    bb_logger.enable(
        uc,
        csv_path=os.getenv("BB_LOG_CSV") or None,
        interval_ms=int(os.getenv("AIDF_BB_LOG_INTERVAL_MS", "1500")),
        mode=os.getenv("MODE", "unknown"),
    )
    # 2) 只有 adaptive 模式且 ADAPT_ENABLE=1 才装自适应触发（不影响 baseline）
    if os.getenv("MODE","").lower()=="adaptive" and os.getenv("ADAPT_ENABLE","0")=="1":
        try:
            from .adaptive_irq import AdaptiveIRQManager, WaitInstrInterceptor, CrashFilter
            cfg = {
                "min_init_ms": int(os.getenv("ADAPT_MIN_INIT_MS","700")),
                "stall_ms":    int(os.getenv("ADAPT_STALL_MS","140")),
                "irq_cooldown_ms": int(os.getenv("ADAPT_IRQ_COOLDOWN_MS","180")),
                "max_irqs_per_stall": int(os.getenv("ADAPT_MAX_IRQS_PER_STALL","1")),
            }
            mgr = AdaptiveIRQManager(emu=None, uc=uc, config=cfg)
            try:
                WaitInstrInterceptor(emu=None, uc=uc, adapt_mgr=mgr).install()
            except Exception:
                pass
            try:
                CrashFilter(emu=None, uc=uc, adapt_mgr=mgr, tag_dir=os.getcwd())
            except Exception:
                pass
            log.info("[AidFuzz] adaptive IRQ installed.")
        except Exception as e:
            log.warning(f"[AidFuzz] adaptive install failed: {e!r}")
