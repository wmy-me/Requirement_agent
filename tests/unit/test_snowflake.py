"""雪花 id 生成器单测（无 DB 依赖，全内存）。"""

import threading
import time

from src.common.snowflake import Snowflake


def test_ids_positive_and_within_int63() -> None:
    sf = Snowflake(worker_id=0)
    for _ in range(1000):
        i = sf.next_id()
        assert i > 0
        assert i < 2**63  # BIGINT 上限内


def test_uniqueness_large_batch() -> None:
    gen = Snowflake(worker_id=1)
    seen = set()
    # 加大批次，覆盖同毫秒序列递增
    for _ in range(100_000):
        i = gen.next_id()
        assert i not in seen, f"duplicate id {i}"
        seen.add(i)
    assert len(seen) == 100_000


def test_monotonic_increasing() -> None:
    gen = Snowflake(worker_id=2)
    prev = gen.next_id()
    for _ in range(10_000):
        cur = gen.next_id()
        assert cur > prev
        prev = cur


def test_different_worker_ids_never_collide() -> None:
    gen_a = Snowflake(worker_id=0)
    gen_b = Snowflake(worker_id=1023)
    ids_a = {gen_a.next_id() for _ in range(1000)}
    ids_b = {gen_b.next_id() for _ in range(1000)}
    assert ids_a.isdisjoint(ids_b)


def test_same_millisecond_sequence_advances_and_overflows() -> None:
    """同毫秒序列递增；序列耗尽后自旋等待下一毫秒，id 仍全局单调。"""
    base = 1735689600123
    calls = {"n": 0}

    def clock() -> int:
        calls["n"] += 1
        # 前 4097 次时钟读取返回同一毫秒（覆盖 4096 个序列 + 1 次触发溢出），
        # 之后的读取（溢出后的等待轮询）逐毫秒前进。
        if calls["n"] <= 4097:
            return base
        return base + (calls["n"] - 4097)

    gen = Snowflake(worker_id=3, now_fn=clock)
    prev = gen.next_id()
    for _ in range(5000):
        cur = gen.next_id()
        assert cur > prev  # 含溢出边界也严格递增
        prev = cur


def test_clock_rollback_does_not_decrease_ids() -> None:
    """时钟回拨时沿用上次时间戳，保证 id 不倒退、不重复。"""
    t = [1735689600000]
    gen = Snowflake(worker_id=4, now_fn=lambda: t[0])
    first = gen.next_id()
    # 时钟前进
    t[0] = 1735689600100
    second = gen.next_id()
    assert second > first
    # 时钟回拨到更早
    t[0] = 1735689600050
    third = gen.next_id()
    assert third > second  # 不倒退


def test_worker_id_out_of_range_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        Snowflake(worker_id=1024)
    with pytest.raises(ValueError):
        Snowflake(worker_id=-1)


def test_thread_safety_no_duplicates() -> None:
    """多线程并发生成不重复、不阻塞抛出。"""
    gen = Snowflake(worker_id=5)
    results: list[list[int]] = []
    lock = threading.Lock()

    def produce(n: int) -> None:
        local = [gen.next_id() for _ in range(n)]
        with lock:
            results.append(local)

    threads = [threading.Thread(target=produce, args=(500,)) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    all_ids = [i for sub in results for i in sub]
    assert len(all_ids) == 8 * 500
    assert len(set(all_ids)) == len(all_ids)  # 无重复