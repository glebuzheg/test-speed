"""Measure download throughput using sequential HTTP requests."""

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError


@dataclass(frozen=True)
class MeasurementResult:
    request_count: int
    total_bytes: int
    total_seconds: float

    @property
    def average_seconds(self) -> float:
        return self.total_seconds / self.request_count

    @property
    def speed_mbps(self) -> float:
        return self.total_bytes * 8 / self.total_seconds / 1_000_000


class SpeedTester:
    def __init__(self, url: str, count: int = 10):
        if count <= 0:
            raise ValueError("--count должен быть положительным целым числом")
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in {"http", "https"} and parsed.hostname
            parsed.port  # Validate a supplied port before sending a request.
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("URL должен содержать http:// или https:// и имя сервера")
        self.url = url
        self.count = count

    def measure(
        self, on_progress: Callable[[int, int, float], None] | None = None
    ) -> MeasurementResult:
        """Download sequentially; notify the caller after each completed response."""
        total_bytes = 0
        total_seconds = 0.0
        with requests.Session() as session:
            for number in range(1, self.count + 1):
                downloaded = 0
                started = time.perf_counter()
                with session.get(
                    self.url,
                    stream=True,
                    timeout=30,
                    headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"},
                ) as response:
                    response.raise_for_status()
                    # Count transferred body bytes, even if the server sends compressed data.
                    for chunk in response.raw.stream(64 * 1024, decode_content=False):
                        downloaded += len(chunk)
                elapsed = time.perf_counter() - started
                total_bytes += downloaded
                total_seconds += elapsed
                if on_progress is not None:
                    on_progress(number, downloaded, elapsed)
        return MeasurementResult(self.count, total_bytes, total_seconds)


class ConsoleApp:
    def __init__(self, argv: list[str] | None = None):
        parser = argparse.ArgumentParser(
            description="Скорость загрузки по HTTP/HTTPS в Мбит/с."
        )
        parser.add_argument("url", help="прямая ссылка на большой файл или картинку")
        parser.add_argument(
            "--count", type=int, default=10, help="количество запросов (по умолчанию: 10)"
        )
        args = parser.parse_args(argv)
        try:
            self.tester = SpeedTester(args.url, args.count)
        except ValueError as error:
            parser.error(str(error))

    def print_request(self, number: int, downloaded: int, seconds: float) -> None:
        print(f"Запрос {number}/{self.tester.count}: {downloaded} байт, {seconds:.3f} с")

    def run(self) -> int:
        try:
            result = self.tester.measure(on_progress=self.print_request)
        except (requests.RequestException, HTTPError) as error:
            print(f"Ошибка загрузки: {error}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Замер прерван.", file=sys.stderr)
            return 130

        print(f"\nСреднее время запроса: {result.average_seconds:.3f} с")
        print(f"Всего скачано: {result.total_bytes} байт")
        print(f"Скорость: {result.speed_mbps:.3f} Мбит/с")
        return 0


def main(argv: list[str] | None = None) -> int:
    return ConsoleApp(argv).run()


if __name__ == "__main__":
    sys.exit(main())
