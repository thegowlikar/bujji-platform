import sys
sys.path.insert(0, "/opt/bujji/app")

from bujji.production_runtime.composition_root import build_composition_root
from bujji.production_runtime.config import (
    RuntimeConfig, RUNTIME_MODE_SHADOW, RUNTIME_MODE_PRODUCTION_READY, RUNTIME_MODE_READ_ONLY,
)
from bujji.broker.paper import PaperBroker
from bujji.broker.fyers import FyersBroker

print("=== Q1: Can SHADOW mode construct a real FyersBroker? ===")
root = build_composition_root(RuntimeConfig(mode=RUNTIME_MODE_SHADOW, broker_name="fyers"))
print(f"Construction succeeded: mode={root.config.mode}, broker_name={root.config.broker_name}, broker type={type(root.broker).__name__}")
print(f"Is FyersBroker: {isinstance(root.broker, FyersBroker)}")
print(f"Is disable_live_execution applied? place_order is guarded stub: {getattr(root.broker.place_order, '__qualname__', None)}")
print()

print("=== Q2: Can broker_name='paper' construct under PRODUCTION_READY mode? ===")
root2 = build_composition_root(RuntimeConfig(mode=RUNTIME_MODE_PRODUCTION_READY, broker_name="paper"))
print(f"Construction succeeded: mode={root2.config.mode}, broker type={type(root2.broker).__name__}")
print(f"Is PaperBroker: {isinstance(root2.broker, PaperBroker)}")
print()

print("=== Q3: Does run_shadow() reject a root whose broker is not PaperBroker? ===")
from bujji.production_runtime.runtime import run_shadow, PipelineInput
import inspect
src = inspect.getsource(run_shadow)
has_broker_type_check = "PaperBroker" in src or "isinstance(root.broker" in src
print(f"run_shadow() source contains any broker-type check: {has_broker_type_check}")
print("(If False, run_shadow() will execute identically regardless of root.broker's real type --")
print(" the SHADOW mode broker=fyers root from Q1 could be passed to run_shadow() and it would")
print(" proceed exactly as normal, right up to calling executor.submit_and_confirm() against the")
print(" REAL FyersBroker's place_order(). NOT executed here -- construction-only evidence, per the")
print(" sprint's explicit 'do not touch live broker' rule.)")
print()

print("=== Q4: RuntimeConfig field validation -- does it cross-check mode vs broker_name? ===")
import inspect as insp
config_src = insp.getsource(RuntimeConfig.__post_init__)
print(config_src)
