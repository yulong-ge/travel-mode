"""Event-driven admission, completion and teardown of the open interface."""

from __future__ import annotations

import asyncio

from textual.worker import WorkerCancelled, WorkerFailed


class Lifecycle:
    def _init_lifecycle(self):
        self._interface_closing = False
        self._pending_inputs = set()
        self._action_threads = set()
        self.language_posted = asyncio.Event()
        self.last_language_dispatch = None
        self._worker_error_events = {}

    def worker_error_reported(self, worker):
        return self._worker_error_events.setdefault(worker, asyncio.Event())

    def track_input(self):
        completion = asyncio.get_running_loop().create_future()
        self._pending_inputs.add(completion)
        return completion

    def complete_input(self, completion):
        self._pending_inputs.remove(completion)
        completion.set_result(None)

    async def in_thread(self, fn, *args):
        # Cancelling the await does not stop a Python thread or its system Action.
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
        self._action_threads.add(task)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._action_threads.remove(task)

    async def wait_for_idle(self):
        """Drain admitted button events and the workers they create, without polling."""
        while True:
            pending = tuple(self._pending_inputs)
            if pending:
                await asyncio.gather(*(asyncio.shield(item) for item in pending))
            workers = [w for w in self.workers if not w.is_finished and w.group != "quit"]
            if workers:

                async def finish(worker):
                    try:
                        await worker.wait()
                    except WorkerCancelled:
                        # A superseded/closing read has no result to apply.
                        # WorkerFailed and task cancellation still propagate.
                        if not worker.is_cancelled:
                            raise
                    except WorkerFailed:
                        # A failed operation stays failed; wait for its explicit
                        # receipt/UI notification instead of letting Textual exit.
                        await asyncio.wait_for(self.worker_error_reported(worker).wait(), 30)

                await asyncio.gather(*(finish(worker) for worker in workers))
            if not self._pending_inputs and not any(
                not w.is_finished and w.group != "quit" for w in self.workers
            ):
                return

    async def _drain_interface(self):
        self._interface_closing = True
        self._measurement_cancelled.set()
        if self._battery_timer is not None:
            self._battery_timer.stop()
        self._refresh_generation += 1
        for group in ("origin", "dash", "audit", "battery", "tabfix"):
            self.workers.cancel_group(self, group)
        try:
            await self.wait_for_idle()
        finally:
            # Also cover an Action whose coroutine was cancelled by an external exit.
            threads = tuple(self._action_threads)
            if threads:
                await asyncio.gather(*(asyncio.shield(task) for task in threads))
                self._action_threads.difference_update(threads)

    async def _shutdown(self):
        # Textual's default shutdown prunes screens before closing message pumps.
        # Keep their controls alive until admitted transactions and callbacks finish.
        try:
            await self._drain_interface()
        finally:
            await super()._shutdown()

    def action_quit(self):
        async def quit_when_idle():
            await self._drain_interface()
            self.exit()

        self.run_worker(quit_when_idle, group="quit", exclusive=True)
