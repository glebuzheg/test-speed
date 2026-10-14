"""Measure download throughput using sequential HTTP requests."""

import argparse
import sys
import time
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError


def measure(url: str, count: int) -> tuple[int, float]:
    """Download the URL count times and return total body bytes and seconds."""
    total_bytes = 0
    total_seconds = 0.0
    with requests.Session() as session:
        for number in range(1, count + 1):
            downloaded = 0
            started = time.perf_counter()
            with session.get(
                url,
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
            print(f"Запрос {number}/{count}: {downloaded} байт, {elapsed:.3f} с")
    return total_bytes, total_seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Скорость загрузки по HTTP/HTTPS в Мбит/с."
    )
    parser.add_argument("url", help="прямая ссылка на большой файл или картинку")
    parser.add_argument(
        "--count", type=int, default=10, help="количество запросов (по умолчанию: 10)"
    )
    args = parser.parse_args(argv)
    if args.count <= 0:
        parser.error("--count должен быть положительным целым числом")
    try:
        parsed = urlsplit(args.url)
        valid_url = parsed.scheme in {"http", "https"} and parsed.hostname
        parsed.port  # Validate a supplied port before sending a request.
    except ValueError:
        valid_url = False
    if not valid_url:
        parser.error("URL должен содержать http:// или https:// и имя сервера")

    try:
        total_bytes, total_seconds = measure(args.url, args.count)
    except (requests.RequestException, HTTPError) as error:
        print(f"Ошибка загрузки: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Замер прерван.", file=sys.stderr)
        return 130

    print(f"\nСреднее время запроса: {total_seconds / args.count:.3f} с")
    print(f"Всего скачано: {total_bytes} байт")
    print(f"Скорость: {total_bytes * 8 / total_seconds / 1_000_000:.3f} Мбит/с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
