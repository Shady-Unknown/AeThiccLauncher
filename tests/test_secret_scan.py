"""The scanner itself lives in the private repository.

The public mirror keeps this stub so the suite can run on the exported tree;
the real scanner tests run only in the private repository.
"""
import unittest


@unittest.skip("secret_scan lives in the private repository")
class SecretScanTests(unittest.TestCase):
    def test_private_only(self):
        pass
