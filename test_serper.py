import unittest
from unittest.mock import Mock, patch

from config import Config
from search_provider import search_web


class SerperSearchSmokeTest(unittest.TestCase):
    @patch("search_provider.requests.post")
    @patch.object(Config, "SERPER_API_KEY", "unit-test-key")
    def test_search_web_uses_mocked_serper_response(self, mock_post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "organic": [
                {
                    "title": "Video editor",
                    "link": "https://linkedin.com/in/editor-123",
                    "snippet": "Edits video.",
                }
            ]
        }
        mock_post.return_value = response
        results = search_web("video editor", num=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Video editor")


if __name__ == "__main__":
    unittest.main()
