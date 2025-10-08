# -*- coding: utf-8 -*-
"""
Crash filter (paste-over version)

目标：
- 过滤显然“无价值/可预期”的崩溃（例如空模型引起的 MMIO 访问失败、已知假阳性）
- 将有效崩溃上报给外层（保持原行为）

暴露：
  - should_report(info: dict) -> bool    是否值得上报
  - record(info: dict)                   记录一次崩溃（可选）
  - reset()                              清理内部状态（可选）

传入的 info 字段（都可选，缺失时做保守处理）：
  {
    "pc": 0x...,              # 崩溃点
    "reason": "sigsegv" | "sigbus" | "timeout" | "assert" | ...,
    "addr": 0x...,            # 访问地址（如有）
    "is_mmio": bool,          # 是否为 MMIO 空洞导致（如有判定）
    "extra": {...},           # 其它扩展
  }

可调环境变量：
  CF_IGNORE_MMIO_FAULTS      非 0 则忽略“显然是 MMIO”导致的 fault（默认 1）
  CF_IGNORE_TIMEOUT          非 0 则忽略超时类 crash（默认 1）
  CF_ADDR_MMIO_PREFIX        视为 MMIO 的地址高 8 位（默认 0x40000000）
  CF_ADDR_MMIO_MASK          与之比较的掩码（默认 0xF0000000）
"""

from __future__ import annotations
import os
from typing import Optional, Dict, Any


class CrashFilter:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        env = os.environ
        cfg = cfg or {}
        self.ignore_mmio_faults = int(env.get('CF_IGNORE_MMIO_FAULTS',
                                              cfg.get('ignore_mmio_faults', 1))) != 0
        self.ignore_timeout = int(env.get('CF_IGNORE_TIMEOUT',
                                          cfg.get('ignore_timeout', 1))) != 0
        self.mmio_prefix = int(env.get('CF_ADDR_MMIO_PREFIX',
                                       cfg.get('mmio_prefix', 0x40000000)), 0) \
            if isinstance(env.get('CF_ADDR_MMIO_PREFIX', None), str) else cfg.get('mmio_prefix', 0x40000000)
        self.mmio_mask = int(env.get('CF_ADDR_MMIO_MASK',
                                     cfg.get('mmio_mask', 0xF0000000)), 0) \
            if isinstance(env.get('CF_ADDR_MMIO_MASK', None), str) else cfg.get('mmio_mask', 0xF0000000)

        self._seen: int = 0  # 可用于节流等简易策略

    # ---- 判定 API ----
    def should_report(self, info: Dict[str, Any]) -> bool:
        reason = str(info.get('reason', '')).lower()
        addr = info.get('addr', None)
        is_mmio = bool(info.get('is_mmio', False))

        # 1) 超时类
        if self.ignore_timeout and ('timeout' in reason or 'hang' in reason):
            return False

        # 2) MMIO 空洞类
        if self.ignore_mmio_faults:
            if is_mmio:
                return False
            if isinstance(addr, int):
                if (addr & int(self.mmio_mask)) == int(self.mmio_prefix):
                    return False

        # 其它类型：保守上报
        return True

    # ---- 记录/重置（可选） ----
    def record(self, info: Dict[str, Any]) -> None:
        self._seen += 1

    def reset(self) -> None:
        self._seen = 0
