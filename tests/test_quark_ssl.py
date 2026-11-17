import ssl
import unittest

from toolbox.quark.api import QuarkClient


class QuarkTlsTests(unittest.TestCase):
    def test_https_session_keeps_verification_and_allows_weak_cert_keys(self):
        client = QuarkClient("__pus=fixture; __puus=fixture")
        adapter = client.session.get_adapter(
            "https://drive-pc.quark.cn/1/clouddrive/file/sort")
        context = adapter.poolmanager.connection_pool_kw.get("ssl_context")

        self.assertIsNotNone(context)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.security_level, 1)


if __name__ == "__main__":
    unittest.main()
