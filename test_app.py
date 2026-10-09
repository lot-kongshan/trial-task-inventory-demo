from concurrent.futures import ThreadPoolExecutor

from app import Store


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_concurrent_reserve_never_oversells() -> None:
    s = Store()
    s.init("A", 10)

    def try_reserve(_: int) -> bool:
        try:
            s.reserve("A", 1, 60)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(32) as pool:
        ok = sum(pool.map(try_reserve, range(200)))
    assert ok == 10
    assert s.available["A"] == 0


def test_expired_reservation_released_and_cannot_confirm() -> None:
    clock = FakeClock()
    s = Store(clock=clock)
    s.init("A", 2)
    r = s.reserve("A", 2, 30)
    clock.t += 31
    assert s.history("A")[0]["kind"] == "release_timeout"
    assert s.available["A"] == 2
    try:
        s.confirm(r.id)
        raise AssertionError("expired reservation confirmed")
    except ValueError as exc:
        assert str(exc) == "released"


def test_confirm_keeps_stock_deducted() -> None:
    s = Store()
    s.init("A", 3)
    r = s.reserve("A", 2, 60)
    s.confirm(r.id)
    assert s.available["A"] == 1
    assert [m["kind"] for m in s.history("A")][:2] == ["confirm", "reserve"]
