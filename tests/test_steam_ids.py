"""Shared Steam id conversions."""
import unittest

from core import steam_ids


class SignedAppidTests(unittest.TestCase):
    def test_plain_appid_is_unchanged(self):
        self.assertEqual(steam_ids.signed_appid(870780), 870780)
        self.assertEqual(steam_ids.signed_appid("870780"), 870780)

    def test_high_bit_becomes_negative(self):
        self.assertEqual(steam_ids.signed_appid(3509768200), -785199096)
        self.assertEqual(steam_ids.signed_appid(0x80000000), -2147483648)

    def test_negative_is_kept(self):
        self.assertEqual(steam_ids.signed_appid(-785199096), -785199096)

    def test_wraps_out_of_range_values(self):
        self.assertEqual(steam_ids.signed_appid(1 << 32), 0)

    def test_invalid_is_none(self):
        self.assertIsNone(steam_ids.signed_appid(None))
        self.assertIsNone(steam_ids.signed_appid(""))
        self.assertIsNone(steam_ids.signed_appid("not-an-id"))


class UnsignedAppidTests(unittest.TestCase):
    def test_negative_becomes_unsigned(self):
        self.assertEqual(steam_ids.unsigned_appid(-785199096), 3509768200)

    def test_plain_appid_is_unchanged(self):
        self.assertEqual(steam_ids.unsigned_appid(870780), 870780)

    def test_roundtrip(self):
        for appid in (0, 1, 870780, 0x7FFFFFFF, 0x80000000, 3509768200):
            self.assertEqual(
                steam_ids.unsigned_appid(steam_ids.signed_appid(appid)), appid
            )


class SteamAccountIdTests(unittest.TestCase):
    def test_account_folder_from_steamid64(self):
        self.assertEqual(
            steam_ids.account_id_from_steamid64(76561197988281094),
            28015366,
        )

    def test_steamid64_from_account_folder(self):
        self.assertEqual(
            steam_ids.steamid64_from_account_id(28015366),
            76561197988281094,
        )

    def test_roundtrip(self):
        for account in (1, 28015366, 139312182):
            self.assertEqual(
                steam_ids.account_id_from_steamid64(
                    steam_ids.steamid64_from_account_id(account)
                ),
                account,
            )

    def test_invalid_is_none(self):
        self.assertIsNone(steam_ids.account_id_from_steamid64("x"))
        self.assertIsNone(steam_ids.steamid64_from_account_id(None))


if __name__ == "__main__":
    unittest.main()
