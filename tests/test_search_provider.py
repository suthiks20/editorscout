import unittest
from unittest.mock import Mock, patch

from config import Config
from search_provider import SearchAPIError, SearchResponseError, search_videos, search_web


class SearchProviderVideoTests(unittest.TestCase):
    @patch("search_provider.requests.post")
    @patch.object(Config, "SERPER_API_KEY", "unit-test-key")
    def test_search_videos_uses_video_endpoint_and_caps_count(self, mock_post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "videos": [
                {
                    "title": "Short film editing",
                    "link": "https://youtube.com/watch?v=example",
                    "snippet": "An editor channel",
                    "channel": "Example Studio",
                    "date": "2 weeks ago",
                }
            ]
        }
        mock_post.return_value = response
        results = search_videos("video editor", num=20)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://google.serper.dev/videos")
        self.assertEqual(kwargs["json"]["num"], 10)
        self.assertEqual(results[0]["channel"], "Example Studio")
        self.assertEqual(results[0]["date"], "2 weeks ago")
        self.assertEqual(results[0]["snippet"], "An editor channel")

    @patch("search_provider.requests.post")
    @patch.object(Config, "SERPER_API_KEY", "unit-test-key")
    def test_search_videos_maps_provider_errors(self, mock_post):
        response = Mock()
        response.ok = False
        response.status_code = 429
        mock_post.return_value = response
        with self.assertRaises(SearchAPIError) as error:
            search_videos("video editor")
        self.assertEqual(error.exception.status_code, 429)

        response.ok = True
        response.json.return_value = {"organic": []}
        with self.assertRaises(SearchResponseError):
            search_videos("video editor")

    @patch("search_provider.requests.post")
    @patch.object(Config, "SERPER_API_KEY", "unit-test-key")
    def test_search_web_uses_optional_country_code(self, mock_post):
        response = Mock()
        response.ok = True
        response.json.return_value = {"organic": []}
        mock_post.return_value = response

        search_web("video editor", gl="US")
        self.assertEqual(mock_post.call_args.kwargs["json"]["gl"], "us")
        search_web("video editor", gl=None)
        self.assertNotIn("gl", mock_post.call_args.kwargs["json"])


if __name__ == "__main__":
    unittest.main()
