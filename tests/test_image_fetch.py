import unittest

import httpx

from toolbox.image_fetch import fetch_image


class ImageFetchTests(unittest.TestCase):
    def test_sends_headers_and_returns_bytes(self):
        seen = {}

        def handler(request):
            seen["ua"] = request.headers.get("user-agent")
            seen["referer"] = request.headers.get("referer")
            return httpx.Response(200, content=b"image-bytes")

        data = fetch_image("https://example.com/a.jpg",
                           user_agent="test-ua", referer="https://example.com/",
                           transport=httpx.MockTransport(handler))
        self.assertEqual(data, b"image-bytes")
        self.assertEqual(seen["ua"], "test-ua")
        self.assertEqual(seen["referer"], "https://example.com/")

    def test_http_error_is_raised(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        with self.assertRaises(httpx.HTTPStatusError):
            fetch_image("https://example.com/missing.jpg", transport=transport)


if __name__ == "__main__":
    unittest.main()
