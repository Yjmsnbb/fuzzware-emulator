# 文件: fuzzware/emulator/harness/fuzzware_harness/adapt/adaptive_irq.py
import time
import random

class AdaptiveIRQManager:
    """
    Adaptive IRQ insertion with:
      - warmup (delay enable)
      - stall gating (only when stuck)
      - global spacing between IRQs
      - per-IRQ cooldown/backoff with simple epsilon-greedy selection
    """

    def __init__(self, emu, uc, cfg):
        self.uc = uc
        self.cfg = cfg or {}

        # === Tunables (safe defaults) ===
        self.warmup_sec = int(self.cfg.get("warmup_sec", 1200))  # 20 minutes warm-up
        self.stall_ms = int(self.cfg.get("stall_ms", 1500))      # 1.5s of no progress => try IRQ
        self.min_between_irqs_ms = int(self.cfg.get("min_between_irqs_ms", 300))
        self.base_cooldown_ms = int(self.cfg.get("cooldown_ms", 4000))
        self.epsilon = float(self.cfg.get("epsilon", 0.20))
        self.take_over_systick = bool(self.cfg.get("take_over_systick", True))

        self.start_ts = time.monotonic()
        self._last_progress = time.monotonic()
        self._last_irq_ts = 0.0

        # Prepare IRQ set
        self.irq_list = self._enumerate_irq_lines()
        self.irq_stats = {n: {"tries": 0, "wins": 0, "cool_until": 0.0} for n in self.irq_list}

        # Track last wait PC to infer “progress”
        self._last_wait_pc = None

    # ---------------- Public API ----------------
    def ready(self):
        return (time.monotonic() - self.start_ts) >= self.warmup_sec

    def notify_progress(self):
        """Call this when you believe the program 'made progress'."""
        self._last_progress = time.monotonic()

    def should_try_irq(self):
        now = time.monotonic()
        stalled = (now - self._last_progress) * 1000.0 >= self.stall_ms
        spaced = (now - self._last_irq_ts) * 1000.0 >= self.min_between_irqs_ms
        return stalled and spaced

    def on_irq_fired(self):
        self._last_irq_ts = time.monotonic()

    def saw_wait_at(self, pc):
        """Called by the wait interceptor each time we hit a wait instruction."""
        # Heuristic: if we keep seeing the same WFI/WFE PC for long, treat as stuck.
        if self._last_wait_pc != pc:
            self._last_wait_pc = pc
            self.notify_progress()

    def fire_one_irq(self):
        irqn = self._pick_irq()
        if irqn is None:
            return False
        ok = self._raise_irq(irqn)
        self.irq_stats[irqn]["tries"] += 1

        # Give a small grace window for progress
        time.sleep(0.01)
        if self._made_progress_recently():
            self.irq_stats[irqn]["wins"] += 1
        else:
            # Exponential backoff on repeated failures
            fails = self.irq_stats[irqn]["tries"] - self.irq_stats[irqn]["wins"]
            cooldown = self.base_cooldown_ms * (2 ** max(0, fails - 1))
            self.irq_stats[irqn]["cool_until"] = time.monotonic() + cooldown / 1000.0
        return ok

    # ---------------- Internals ----------------
    def _made_progress_recently(self):
        return (time.monotonic() - self._last_progress) < 0.10

    def _pick_irq(self):
        now = time.monotonic()
        candidates = [n for n, s in self.irq_stats.items() if now >= s["cool_until"]]
        if not candidates:
            return None
        if random.random() < self.epsilon:
            return random.choice(candidates)
        # choose the highest empirical success rate
        def score(n):
            s = self.irq_stats[n]
            return (s["wins"] / max(1, s["tries"]))
        return max(candidates, key=score)

    def _enumerate_irq_lines(self):
        """
        Determine which IRQs to try.
        Priority:
          1) cfg['irq_whitelist'] (list of ints or strings resolvable by uc)
          2) default 0..63
        Then apply blacklist filter.
        """
        wl = self.cfg.get("irq_whitelist")
        bl = set(self._normalize_irq_list(self.cfg.get("irq_blacklist", [])))
        if wl:
            base = self._normalize_irq_list(wl)
        else:
            max_irq = int(self.cfg.get("max_irq", 64))
            base = list(range(max_irq))

        # drop blacklisted
        base = [n for n in base if n not in bl]
        return base

    def _normalize_irq_list(self, items):
        out = []
        for it in items:
            if isinstance(it, int):
                out.append(it)
            elif isinstance(it, str):
                n = self._resolve_irq_name(it)
                if n is not None:
                    out.append(n)
        return out

    def _resolve_irq_name(self, name):
        """
        Best-effort mapping from a string (e.g., 'USART1', 'SysTick') to a number.
        If uc provides a resolver, use it; otherwise return None.
        """
        # user-provided resolver
        try:
            if hasattr(self.uc, "irq_name_to_num"):
                return int(self.uc.irq_name_to_num(name))
        except Exception:
            pass
        # accept 'IRQxx' pattern
        if name.upper().startswith("IRQ"):
            try:
                return int(name[3:])
            except Exception:
                return None
        return None

    def _raise_irq(self, irqn):
        """
        Best-effort IRQ triggering across different backends.
        Return True if we believe it succeeded, else False.
        """
        # Preferred: dedicated NVIC object
        try:
            if hasattr(self.uc, "nvic"):
                nvic = self.uc.nvic
                for cand in ("set_pending", "raise_irq", "trigger_irq"):
                    fn = getattr(nvic, cand, None)
                    if callable(fn):
                        fn(int(irqn))
                        return True
        except Exception:
            pass

        # Fallback: methods on uc
        for cand in ("set_pending_irq", "raise_irq", "trigger_irq", "irq"):
            try:
                fn = getattr(self.uc, cand, None)
                if callable(fn):
                    fn(int(irqn))
                    return True
            except Exception:
                pass

        # Last resort: nothing we can do — WFI will be skipped by interceptor anyway
        return False
