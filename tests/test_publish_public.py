"""Publisher tests live with the private tooling.

The public mirror keeps this stub so the exported suite can run; the real
publisher tests run only in the private repository.
"""
import unittest


@unittest.skip("publish_public lives in the private repository")
class PublisherTests(unittest.TestCase):
    def test_private_only(self):
        pass
