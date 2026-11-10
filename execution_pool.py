"""Bounded local execution pool and cancellable stage limits."""
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager


class ExecutionQueueFull(ValueError):
    pass


class ExecutionPool:
    def __init__(self, workers, queue_size):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="deploy-build")
        self.capacity = workers + queue_size
        self.lock = threading.RLock()
        self.futures = {}

    def available(self):
        with self.lock:
            return self.capacity - len(self.futures)

    def submit(self, function, execution_id):
        with self.lock:
            if execution_id in self.futures:
                return self.futures[execution_id]
            if len(self.futures) >= self.capacity:
                raise ExecutionQueueFull("发布队列已满，请等待已有任务完成后重试")
            future = self.executor.submit(function, execution_id)
            self.futures[execution_id] = future

            def finished(_future):
                with self.lock:
                    self.futures.pop(execution_id, None)

            future.add_done_callback(finished)
            return future

    def cancel(self, execution_id):
        with self.lock:
            future = self.futures.get(execution_id)
            return bool(future and future.cancel())

    def shutdown(self, wait=True):
        self.executor.shutdown(wait=wait, cancel_futures=True)


@contextmanager
def stage_slot(semaphore, check_active):
    while True:
        check_active()
        if semaphore.acquire(timeout=0.2):
            break
    try:
        check_active()
        yield
    finally:
        semaphore.release()
