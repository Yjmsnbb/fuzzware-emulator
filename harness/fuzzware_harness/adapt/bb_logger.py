# 文件: fuzzware/emulator/harness/fuzzware_harness/adapt/bb_logger.py
import csv
import threading
import time

try:
    from unicorn.arm_const import UC_ARM_REG_PC
except Exception:
    UC_ARM_REG_PC = None

def _read_pc(uc):
    try:
        if UC_ARM_REG_PC is not None:
            return uc.reg_read(UC_ARM_REG_PC)
    except Exception:
        pass
    return None

def enable(uc, csv_path, interval_ms=1000, mode="pc"):
    """
    Very simple sampler: every interval_ms, write (ts, pc) to csv.
    Not performance critical (seconds-level default).
    """
    stop_flag = {"stop": False}

    def worker():
        with open(csv_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "pc"])
            while not stop_flag["stop"]:
                pc = _read_pc(uc)
                ts = time.time()
                w.writerow([f"{ts:.6f}", f"0x{pc:08x}" if pc is not None else "NA"])
                f.flush()
                time.sleep(max(0.001, interval_ms / 1000.0))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # stash stop flag on uc so you can stop it if needed
    setattr(uc, "_bb_logger_stop", stop_flag)
