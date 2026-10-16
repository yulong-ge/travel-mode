# Event-triggered automation research

Read-only investigation, 2026-10-15. No agent was installed or loaded, no
notification was posted, and no sleep or power setting was changed.

## Scope decision

Implement only active power-source changes. Apple publishes
`com.apple.system.powersources.source` for AC/battery/UPS transitions. Its public
header recommends this narrower notification instead of `timeremaining` because
it runs clients less often. Neither `timeremaining` nor the all-battery-data
notification belongs in this agent. The handler must read the current source;
the event is an invalidation signal, not a transition log.
[Apple IOPowerSources.h, notification definitions](https://github.com/apple-oss-distributions/IOKitUser/blob/main/ps.subproj/IOPowerSources.h#L104-L162).

Do not implement lid/sleep/wake triggers this round. Public sleep/wake APIs need
an already-running observer. No public, documented, third-party user-agent
LaunchEvent with reliable lid/sleep semantics was established by this research.
This is a scope decision based on the evidence below, not a claim that no private
notification exists.

## LaunchEvents and required consumption

The local `man 5 launchd.plist` applies to system and per-user agents. Its
`LaunchEvents` section specifies a dictionary of stream dictionaries and requires
event consumption through `xpc_set_event_stream_handler`. `man 3 xpc_events`
explicitly supports `com.apple.notifyd.matching`; the event descriptor is
`{"Notification": "<notify name>"}`. It says each matching event launches the job
on demand if it is not already running. Apple's online API overview likewise
describes XPC events as on-demand IOKit events and notifications.
[Apple XPC events documentation](https://developer.apple.com/documentation/xpc/xpc-events).

The local `xpc_events(3)` manual requires registration during initialization.
An unconsumed event can cause failed future delivery, unexpected relaunches, or
performance problems. Thus a plist that directly invokes ordinary Python CLI
code and immediately exits is insufficient, even without `KeepAlive`. Pending
events are queued until the handler is registered; delivery to the handler
consumes the event. BSD notification events have no additional payload.

The installed SDK's `usr/include/xpc/xpc.h`, under
`xpc_set_event_stream_handler`, specifies that a null target queue uses the
default libdispatch target queue, repeated registration for one stream is
undefined, and exit can race delivery and lose an event. There is no pause API.
[Apple API reference](https://developer.apple.com/documentation/xpc/xpc_set_event_stream_handler(_:_:_:)).

Implementation recommendation: register once in a private launch-event entry,
consume the queued event through a small native bridge, wait once with a hard
deadline, reconcile current power state, and exit. Do not implement a polling
loop or `notifyutil -w` resident helper. Preserve the bridge's callback, Block
descriptor, and other referenced objects until process exit. Treat rapid
transitions as reconciliation of current state, not exactly-once delivery.

Clang's Apple Block ABI specifies the literal layout (`isa`, flags, reserved,
invoke pointer, descriptor), `_NSConcreteGlobalBlock`, and the global flag
`1 << 28`. The callback receives the Block pointer before its declared
parameters. A `ctypes` global-Block bridge must follow these definitions; an
ordinary C callback pointer is not a Block. This supports a finite bridge without
requiring a compiler at installation time. Its Python/native lifetime and error
handling still need tests.
[Clang: Apple Block implementation specification](https://clang.llvm.org/docs/Block-ABI-Apple.html#high-level).

## Power names and local queries

The installed SDK's `IOKit.framework/.../Headers/ps/IOPowerSources.h` agrees with
Apple's public source on the names and semantics. Its `kIOPSNotifyAttach` is about
power-source devices appearing/disappearing, not AC cable changes. It is therefore
not an alternative to `.source`.

These read-only queries completed successfully on this host:

```text
/usr/bin/notifyutil -g com.apple.system.powersources.source
com.apple.system.powersources.source 0

/usr/bin/notifyutil -g com.apple.system.powersources.timeremaining
com.apple.system.powersources.timeremaining 72057594043170815
```

These values are observations, not proof of launchd delivery or a portable
encoding of power state. `notifyutil(1)` says state defaults to zero, and a key's
state exists only while it has registrations. Do not decode the private numeric
state in product code. No blocking listener or posted notification was used.

## Sleep, wake, and lid limitations

Apple QA1340 documents `NSWorkspace` observer registration and
`IORegisterForSystemPower` followed by a scheduled run-loop source. These receive
callbacks in a running process. Sleep callbacks can require acknowledgement;
failure can delay sleep. Closing a laptop lid causes forced sleep, which is not
the same as an IORegistry node appearing.
[Apple QA1340: sleep/wake notifications](https://developer.apple.com/library/archive/qa/qa1340/_index.html).

The local public `IOPMLib.h` confirms the live registration, notification-port,
and acknowledgement requirements. The `com.apple.iokit.matching` LaunchEvent
described by `xpc_events(3)` is a node-appearance match, not a property-change
subscription to `AppleClamshellState`.

Apple does expose the spelling
`com.apple.powermanagement.systempowerstate` in **IOPMLibPrivate.h**. It fires at
the start of sleep, wake, and maintenance wake; the header recommends a different
connection API. This private, broad notification does not establish a stable
third-party lid-close contract or guarantee that a newly started Python process
can finish before sleep. Do not install it.
[Apple IOPMLibPrivate.h](https://github.com/apple-oss-distributions/IOKitUser/blob/main/pwr_mgt.subproj/IOPMLibPrivate.h#L46-L60).

## Throttling, coalescing, and bounded work

The local `launchd.plist(5)` manual states that the default spawn throttle is
10 seconds; `ThrottleInterval` overrides this. A 30-second interval is a launch
rate limit, not a timer that periodically wakes the job. It does not replace
application-level deduplication.
[Apple launchd source manual, ThrottleInterval](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5).

`notify(3)` allows rapid posts for one name to be coalesced into one delivery.
Mach and file queues are finite, and events may be lost. The application must
therefore compare the actual current source with its last recorded observation,
not count events or promise every unplug/replug transition. Avoid ignoring a
changed source merely because another different transition happened recently;
only repeated observations should be deduplicated. Keep event logs bounded and
never wait for an interactive sudo password.
[Apple Libnotify notify(3) source](https://github.com/apple-oss-distributions/Libnotify/blob/main/notify.3#L72-L107).

## Verification boundary

The research establishes documented mechanism and names. It does not establish
actual delivery in this installation: no `bootstrap`, `load`, installation,
posting, or real notification display was performed. A maintainer must validate
an isolated test label, consume a real power event, then use `launchctl print`
to verify the job returns to a non-running state and uninstall it. Simulated
handler CPU/wall time is useful but must be labeled as simulation; it cannot
prove native event-delivery overhead or idle-zero behavior on a loaded agent.

## Implementation and measured overhead

The implementation uses only `.source`, with a 30-second `ThrottleInterval`, one
native XPC semaphore wait (10 seconds maximum), and a 45-second command deadline.
The native entry flushes output and exits after its scopes finish; it does not
run CPython finalizers while libxpc can still invoke the retained global Block.
The library symbols required by this bridge were loaded and inspected read-only
on this host; no event handler was registered during that inspection.

A single simulated notification handler on this host took 0.006203667 seconds
wall time, 0.005247 seconds process CPU and 0 child CPU seconds. Method:
`uv run python scripts/measure_event_overhead.py`; `time.perf_counter` and
`resource.getrusage` bracket one event after imports, temporary config/state,
with a strict simulated Runner. Two logical calls were recorded (power-source
query and osascript notification), and neither ran on the host. These figures
exclude interpreter startup, native delivery and real notification overhead.

A read-only `launchctl print gui/<uid>/io.github.yulong-ge.travel-mode.events`
returned exit 113 (service not found). This confirms the production agent is
not loaded on the development host. It is not evidence that a newly installed
agent exits correctly after a real event. That remains a maintainer check using
`uv run python scripts/e2e_automation.py --plan` and the isolated test label.
