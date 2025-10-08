# 文件: fuzzware/emulator/harness/fuzzware_harness/adapt/wait_interceptor.py
import struct

try:
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_CPSR
except Exception:
    UC_HOOK_CODE = 0
    UC_ARM_REG_PC = None
    UC_ARM_REG_CPSR = None

# Thumb encodings for hint instructions on Cortex-M
_WFI = 0xBF30  # Wait For Interrupt
_WFE = 0xBF20  # Wait For Event

def _is_thumb(uc):
    # On M-profile we are always in Thumb. If CPSR not available, assume Thumb.
    if UC_ARM_REG_CPSR is None:
        return True
    try:
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        # T-bit (bit 5) indicates Thumb state on ARMv7
        return bool((cpsr >> 5) & 1)
    except Exception:
        return True

def _read_u16(uc, addr):
    try:
        data = uc.mem_read(addr, 2)
        return struct.unpack("<H", data)[0]
    except Exception:
        return None

def _advance_pc(uc, pc, thumb=True):
    try:
        if UC_ARM_REG_PC is not None:
            uc.reg_write(UC_ARM_REG_PC, pc + (2 if thumb else 4))
    except Exception:
        pass

def _on_wait(uc, address, size, mgr):
    # mark we've seen WFI/WFE at this PC (used to detect “stuck in the same wait”)
    try:
        mgr.saw_wait_at(address)
    except Exception:
        pass

    # Before warm-up ends: do NOTHING (let WFI/WFE actually sleep)
    try:
        if not mgr.ready():
            return False  # don't intercept, run the instruction
    except Exception:
        # be conservative
        return False

    # Only when stalled enough and global spacing satisfied we try an IRQ
    try:
        if mgr.should_try_irq():
            mgr.fire_one_irq()
            mgr.on_irq_fired()
            # Intercept the wait: skip the instruction so we don't actually sleep
            return True
        else:
            # not stalled — consider that “progress”
            mgr.notify_progress()
            return False
    except Exception:
        # If anything goes wrong, don't brick execution — let it run
        return False

class WaitInstrInterceptor:
    """
    Hook that detects WFI/WFE (Thumb 16-bit hints 0xBF30/0xBF20) and optionally
    skips them depending on the manager decision.
    """

    @staticmethod
    def install(uc, mgr):
        def _code_hook(uc_, addr, size_, user_data):
            # thumb-only check
            thumb = _is_thumb(uc_)
            if not thumb:
                return
            val = _read_u16(uc_, addr)
            if val is None:
                return
            if val not in (_WFI, _WFE):
                return

            intercept = _on_wait(uc_, addr, size_, mgr)
            if intercept:
                # skip this instruction to “wake up” immediately
                _advance_pc(uc_, addr, thumb=True)

        try:
            uc.hook_add(UC_HOOK_CODE, _code_hook, None)
        except Exception as e:
            print(f"[ADAPT] failed to hook code for WFI/WFE: {e}")
