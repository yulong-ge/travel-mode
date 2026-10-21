"""Concurrent effects with isolated action state and one checkpoint writer.

Workers synchronously publish deltas before their commands can run. Only the
calling thread merges them into live state and persists recovery checkpoints.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from copy import copy, deepcopy
from pathlib import Path
from queue import Queue
from threading import Event

from ..state import STATE_LOCK, StateError, save_state
from .base import Context
from .journal import command_scope, preparing_command


def resources(action):
    """Keep operations for one app/service/profile in one serial lane."""
    path = getattr(action, "path", None)
    if path:
        return {("target", path), ("app", Path(path).stem)}
    spec = getattr(action, "spec", None)
    if spec:
        return {("app", spec.name), *(("target", p) for p in (*spec.system_daemons, *spec.user_agents))}
    if callable(getattr(action, "command", None)):
        command = action.command()
        if command[:1] == ["pmset"]:
            # Battery/AC/global settings share one preferences store. Concurrent
            # read-modify-write requests can overwrite another domain's change.
            return {("pmset",)}
        if command[:2] in (["launchctl", "load"], ["launchctl", "unload"]):
            return {("target", command[-1])}
        if command[:1] == ["open"]:
            return {("target", command[-1])}
        return {tuple(command[:2])}
    return {("exclusive",)}


def merge(current, previous, updated, path=()):
    """Merge independent action deltas; reject competing scalar writes explicitly."""
    for key in previous.keys() | updated.keys():
        old, new = previous.get(key), updated.get(key)
        if old == new:
            continue
        location = (*path, key)
        if key not in updated:
            if current.get(key) != old:
                raise StateError(f"batch state conflict: {'.'.join(location)}")
            current.pop(key, None)
        elif isinstance(new, dict) and (old is None or isinstance(old, dict)):
            merge(current.setdefault(key, {}), old or {}, new, location)
        elif isinstance(new, list) and (old is None or isinstance(old, list)):
            values = current.setdefault(key, [])
            for item in old or []:
                if item not in new and item in values:
                    values.remove(item)
            for item in new:
                if item not in (old or []) and item not in values:
                    values.append(deepcopy(item))
        elif location[:2] == ("session", "actions"):
            current[key] = current.get(key, 0) + new - (old or 0)
        elif current.get(key) != old and current.get(key) != new:
            raise StateError(f"batch state conflict: {'.'.join(location)}")
        else:
            current[key] = deepcopy(new)


def execute_batch(executor, actions, context, completed=None, ordered=False, clock=None):
    from .. import failures, sessions
    from ..system.shell import is_dry_run

    actions = list(actions)
    if not actions:
        return []
    from ..system.sudo import confirm_batch

    confirm_batch(actions)
    if not ordered and executor.batch_workers > 1:
        waves = []
        remaining = list(enumerate(actions))
        while remaining:
            occupied, wave, deferred = set(), [], []
            for index, action in remaining:
                keys = resources(action)
                if keys & occupied:
                    deferred.append((index, action))
                else:
                    wave.append((index, action))
                    occupied.update(keys)
            waves.append(wave)
            remaining = deferred
        if len(waves) > 1:
            results = [None] * len(actions)
            for wave in waves:

                def done(index, result, wave=wave):
                    original = wave[index][0]
                    results[original] = result
                    if completed:
                        completed(original, result)

                wave_results = execute_batch(
                    executor,
                    [action for _, action in wave],
                    context,
                    done if completed else None,
                    clock=clock,
                )
                for (index, _), result in zip(wave, wave_results, strict=True):
                    results[index] = result
            return results
    if context.state is None or is_dry_run() or len(actions) == 1:
        results = []
        for index, action in enumerate(actions):
            result = executor.execute(action, context)
            results.append(result)
            if completed:
                completed(index, result)
        return results
    state, save = context.state, context.save or save_state
    # Initialize the shared session exactly once, before making worker snapshots.
    if executor.count_actions:
        save(state)
        sessions.begin(state)
        save(state)
    queue = Queue()
    baseline = deepcopy(state)

    notices = [[] for _ in actions]
    outcomes = [None] * len(actions)
    gates = [Event() for _ in range(len(actions) + 1)]
    gates[0].set()

    def action_worker(index, action):
        local = deepcopy(baseline)
        checkpoint = deepcopy(local)

        def publish(value):
            nonlocal checkpoint
            response = Future()
            snapshot = deepcopy(value)
            queue.put(("save", (checkpoint, snapshot, response, preparing_command.get())))
            response.result()  # Intent must reach disk before the worker resumes.
            checkpoint = snapshot

        isolated = copy(action)
        if getattr(isolated, "state", None) is state:
            isolated.state = local
        if hasattr(isolated, "save"):
            isolated.save = publish
        # No global execution lock: each worker owns its context and Executor.
        isolated_executor = type(executor)(count_actions=executor.count_actions)
        from contextlib import nullcontext

        from ..system.shell import ordered_start

        with (
            failures.isolated_results() as observed,
            failures.reporter(lambda kind, message: notices[index].append((kind, message))),
            ordered_start(gates[index].wait, gates[index + 1].set) if ordered else nullcontext(),
        ):
            outcomes[index] = observed
            return isolated_executor.execute(isolated, Context(local, publish))

    def worker(index, action):
        try:
            result = action_worker(index, action)
        except BaseException as error:
            queue.put(("done", (index, None, error)))
            raise
        else:
            queue.put(("done", (index, result, None)))
        finally:
            from ..system.apps import finish_quit_request, quit_target

            if quit_target(action):
                finish_quit_request()
            if ordered:
                gates[index + 1].set()

    results, errors = [None] * len(actions), []
    stopping_error = None
    from ..system.apps import quit_batch, quit_target

    workers = min(executor.batch_workers, len(actions))
    if workers > 1:
        # Every app must be able to issue/retire its request before any refused
        # target waits on the shared verification deadline.
        workers = max(workers, sum(quit_target(action) for action in actions))
    with (
        quit_batch(actions, clock, enabled=workers > 1) as quitting,
        ThreadPoolExecutor(max_workers=workers, thread_name_prefix="travel-action") as pool,
    ):
        futures = []
        for i, action in enumerate(actions):
            try:
                futures.append(pool.submit(copy_context().run, worker, i, action))
            except BaseException as error:  # noqa: BLE001 - stop admission, drain submitted work, then raise
                errors.append(error)
                stopping_error = error
                if quitting is not None:
                    quitting.retire(sum(quit_target(item) for item in actions[i:]))
                break
        remaining = len(futures)
        while remaining:
            try:
                kind, payload = queue.get()
            except (KeyboardInterrupt, SystemExit) as error:
                errors.append(error)
                stopping_error = stopping_error or error
                continue
            if kind == "save":
                previous, updated, response, command = payload
                if stopping_error is not None:
                    response.set_exception(stopping_error)
                    continue
                try:
                    with STATE_LOCK, command_scope(command):
                        merge(state, previous, updated)
                        save(state)
                except BaseException as error:  # noqa: BLE001 - returned to the blocked worker, then raised below
                    stopping_error = error
                    response.set_exception(error)
                else:
                    response.set_result(None)
            else:
                index, result, error = payload
                remaining -= 1
                results[index] = result
                if error is not None:
                    errors.append(error)
                    stopping_error = stopping_error or error
                elif completed:
                    try:
                        for kind, message in notices[index]:
                            failures.notify(kind, message)
                        completed(index, result)
                    except BaseException as error:  # noqa: BLE001 - drain in-flight effects before re-raising
                        errors.append(error)
        # Observe every future; exceptions propagate only after all in-flight
        # mutations have settled and all their recovery checkpoints are drained.
        for future in futures:
            try:
                future.result()
            except BaseException as error:  # noqa: BLE001 - every captured worker error propagates below
                if error not in errors:
                    errors.append(error)
    for observed in outcomes:
        if observed is not None:
            failures.merge_results(observed)
    if completed is None:
        for messages in notices:
            for kind, message in messages:
                failures.notify(kind, message)
    if errors:
        if executor.count_actions:
            counts = state["session"]["actions"]
            counts["failed"] = counts["attempted"] - counts["succeeded"]
            from ..i18n import t

            failures.record(
                state,
                t("failure.worker", group="batch"),
                str(errors[0]),
                error=errors[0],
                kind="state" if isinstance(errors[0], StateError) else "internal",
                modifying=True,
            )
            if not isinstance(errors[0], StateError):
                save(state)
        raise errors[0]
    return results
