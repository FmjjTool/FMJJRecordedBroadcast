import unittest
from unittest import mock

import tickets


class ShowStartRoutingTests(unittest.TestCase):
    def test_known_artist_uses_artist_page(self):
        event = tickets.ShowEvent(platform="showstart", event_id="306602",
                                  title="测试演出")
        with mock.patch.object(tickets, "parse_showstart_artist",
                               return_value=[event]) as artist_page, \
                mock.patch.object(tickets, "_showstart_search_keyword") as keyword_search:
            events, diag = tickets.search_tickets_by_keyword(
                "封茗囧菌", platform="showstart")
        artist_page.assert_called_once_with("8982311")
        keyword_search.assert_not_called()
        self.assertEqual(diag["showstart"], 1)
        self.assertEqual([ev.event_id for ev in events], ["306602"])

    def test_unknown_keyword_uses_keyword_search(self):
        with mock.patch.object(tickets, "parse_showstart_artist") as artist_page, \
                mock.patch.object(tickets, "_showstart_search_keyword",
                                  return_value=[]) as keyword_search:
            events, diag = tickets.search_tickets_by_keyword(
                "无关关键词", platform="showstart")
        keyword_search.assert_called_once_with("无关关键词")
        artist_page.assert_not_called()
        self.assertEqual(events, [])
        self.assertEqual(diag["showstart"], "秀动全国无在售演出")


if __name__ == "__main__":
    unittest.main()
