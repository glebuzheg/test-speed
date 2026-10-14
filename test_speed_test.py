import contextlib
import gzip
import io
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import requests
from urllib3.exceptions import ProtocolError

import speed_test


class SpeedTests(unittest.TestCase):
    def test_default_and_custom_count(self):
        for arguments, expected in (([], 10), (["--count", "3"], 3)):
            with self.subTest(count=expected), patch(
                "speed_test.measure", return_value=(125_000, 1.0)
            ) as measure, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(speed_test.main(["https://example.com/file", *arguments]), 0)
                measure.assert_called_once_with("https://example.com/file", expected)

    def test_invalid_arguments_do_not_send_requests(self):
        cases = [
            [],
            ["https://example.com", "--count", "0"],
            ["https://example.com", "--count", "-1"],
            ["https://example.com", "--count", "abc"],
            ["ftp://example.com/file"],
            ["https://"],
            ["https://example.com:invalid"],
            ["https://[invalid"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), patch(
                "speed_test.requests.Session"
            ) as session, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    speed_test.main(arguments)
                self.assertEqual(error.exception.code, 2)
                session.assert_not_called()

    def test_downloads_finish_before_next_request_and_metrics_are_correct(self):
        events = []
        clock = [0.0]

        def get_response(*args, **kwargs):
            events.append("get")
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.side_effect = lambda *args: events.append("close")

            def stream(chunk_size, decode_content):
                self.assertFalse(decode_content)
                events.append("body")
                clock[0] += 0.5
                yield b"x" * 62_500
                clock[0] += 0.5
                yield b"x" * 62_500
                events.append("finished")

            response.raw.stream.side_effect = stream
            return response

        output = io.StringIO()
        with patch("speed_test.requests.Session") as session_class, patch(
            "speed_test.time.perf_counter", side_effect=lambda: clock[0]
        ), contextlib.redirect_stdout(output):
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = get_response
            self.assertEqual(speed_test.main(["https://example.com", "--count", "2"]), 0)
        self.assertEqual(events, ["get", "body", "finished", "close"] * 2)
        self.assertIn("Среднее время запроса: 1.000 с", output.getvalue())
        self.assertIn("Всего скачано: 250000 байт", output.getvalue())
        self.assertIn("Скорость: 1.000 Мбит/с", output.getvalue())

    def test_failures_do_not_print_success_summary(self):
        for error in (requests.Timeout("timeout"), ProtocolError("truncated")):
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(error=error), patch(
                "speed_test.measure", side_effect=error
            ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(speed_test.main(["https://example.com"]), 1)
            self.assertNotIn("Скорость:", output.getvalue())
            self.assertIn("Ошибка загрузки:", errors.getvalue())


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = b"download test\n" * 10_000
        cls.compressed = gzip.compress(cls.payload)
        cls.paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.paths.append(self.path)
                if self.path == "/error":
                    self.send_error(500)
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/file")
                    self.end_headers()
                    return
                body = cls.compressed if self.path == "/gzip" else cls.payload
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                if self.path == "/gzip":
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                self.wfile.flush()
                if self.path == "/slow":
                    time.sleep(0.1)
                if self.path == "/truncated":
                    self.wfile.write(body[:10])
                    self.close_connection = True
                else:
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_real_downloads_with_default_count(self):
        output = io.StringIO()
        before = len(self.paths)
        with contextlib.redirect_stdout(output):
            self.assertEqual(speed_test.main([self.url + "/file"]), 0)
        self.assertEqual(self.paths[before:], ["/file"] * 10)
        self.assertIn(f"Всего скачано: {len(self.payload) * 10} байт", output.getvalue())

    def test_compression_redirect_and_delayed_body(self):
        for path, expected in (
            ("/gzip", len(self.compressed)),
            ("/redirect", len(self.payload)),
            ("/slow", len(self.payload)),
        ):
            with self.subTest(path=path), contextlib.redirect_stdout(io.StringIO()):
                downloaded, seconds = speed_test.measure(self.url + path, 1)
            self.assertEqual(downloaded, expected)
            if path == "/slow":
                self.assertGreaterEqual(seconds, 0.09)

    def test_http_error_and_truncated_body_stop_the_run(self):
        for path in ("/error", "/truncated"):
            output, errors = io.StringIO(), io.StringIO()
            before = len(self.paths)
            with self.subTest(path=path), contextlib.redirect_stdout(
                output
            ), contextlib.redirect_stderr(errors):
                self.assertEqual(speed_test.main([self.url + path, "--count", "3"]), 1)
            self.assertEqual(self.paths[before:], [path])
            self.assertNotIn("Скорость:", output.getvalue())
            self.assertIn("Ошибка загрузки:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
