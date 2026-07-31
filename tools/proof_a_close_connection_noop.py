"""P3 Proof A — does close_connection() on the REAL installed SDK object
actually no-op when its private __ws_object is None?

No mocking of the SDK. We construct a real fyers_apiv3.FyersWebsocket
.data_ws.FyersDataSocket instance (no network call — __init__ does no I/O)
and call close_connection() before connect() ever runs, so __ws_object is
at its real __init__-time default. Then we probe real, disclosed evidence:
did restart_flag change? did any exception fire? does the method return
early?
"""
import fyers_apiv3.FyersWebsocket.data_ws as m

sock = m.FyersDataSocket(
    access_token="dummy:dummy",
    log_path="/tmp/s120/p3",
    litemode=True,
    write_to_file=False,
    reconnect=True,
    on_message=lambda msg: None,
    on_error=lambda msg: None,
    on_connect=lambda: None,
    on_close=lambda msg: None,
)

ws_object_before = sock._FyersDataSocket__ws_object
restart_flag_before = sock.restart_flag
print(f"BEFORE call: __ws_object={ws_object_before!r} restart_flag={restart_flag_before!r}")

# Real call to the real, unmodified method.
result = sock.close_connection()

ws_object_after = sock._FyersDataSocket__ws_object
restart_flag_after = sock.restart_flag
print(f"AFTER call:  __ws_object={ws_object_after!r} restart_flag={restart_flag_after!r} return={result!r}")

noop_confirmed = (restart_flag_before == restart_flag_after == True) and ws_object_after is None and ws_object_before is None
print()
print(f"NO-OP CONFIRMED: {noop_confirmed}")
print("Evidence: close_connection()'s entire body is guarded by `if self.__ws_object:`.")
print("With __ws_object still None (the real __init__ default, unchanged because connect()")
print("was never called), the guard is False, so the method body — including")
print("`self.restart_flag = False` — never executes. restart_flag stayed True.")
print("No exception, no return value, no visible signal that the close request was dropped.")
