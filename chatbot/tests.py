from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

FAKE_RESULT = {"reply": "Returns are accepted within 30 days.", "sources": ["returns.txt"]}


class ChatbotTests(TestCase):
    def test_chat_rejects_empty_question(self):
        resp = self.client.post(
            reverse("chatbot_chat"),
            {"message": ""},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("reply", data)


class SanitizeReplyTests(TestCase):
    def test_reply_with_cannot_is_returned_unchanged(self):
        from chatbot.services import _sanitize_reply

        reply = "Sorry, I cannot accept returns on worn items, but you can exchange them within 30 days."
        self.assertEqual(_sanitize_reply(reply), reply)

    def test_reply_mentioning_ai_is_returned_unchanged(self):
        from chatbot.services import _sanitize_reply

        reply = "As an AI assistant, we can only answer from our store policies, but our support team can check your order."
        self.assertEqual(_sanitize_reply(reply), reply)

    def test_reply_saying_no_access_is_still_replaced(self):
        from chatbot.services import _NO_INFO_REPLIES, _sanitize_reply

        self.assertIn(_sanitize_reply("We don't have access to your account."), _NO_INFO_REPLIES)


class PromptTests(TestCase):
    def test_prompt_describes_an_automated_assistant(self):
        from chatbot.services import RAG_PROMPT_TEMPLATE

        prompt = RAG_PROMPT_TEMPLATE.lower()
        self.assertIn("automated support assistant", prompt)
        self.assertNotIn("never say you are an ai", prompt)
        self.assertNotIn("real person", prompt)
        self.assertNotIn("human support agent", prompt)

    def test_prompt_keeps_grounding_rules(self):
        from chatbot.services import RAG_PROMPT_TEMPLATE

        self.assertIn("Use only the search results below", RAG_PROMPT_TEMPLATE)
        self.assertIn("1 to 3 short sentences", RAG_PROMPT_TEMPLATE)
        self.assertIn("support team", RAG_PROMPT_TEMPLATE)
        self.assertIn("$search_results$", RAG_PROMPT_TEMPLATE)
        self.assertIn("$query$", RAG_PROMPT_TEMPLATE)


class WidgetDisclosureTests(TestCase):
    def test_widget_shows_automated_assistant_notice(self):
        resp = self.client.get(reverse("products:home"))
        self.assertContains(resp, "Automated assistant. Answers come from our store policies.")


@patch("chatbot.views.query_chat", return_value=FAKE_RESULT)
class ChatCsrfTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)

    def test_post_without_csrf_token_is_rejected(self, mock_query):
        resp = self.client.post(
            reverse("chatbot_chat"), {"message": "Can I return shoes?"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 403)
        mock_query.assert_not_called()

    def test_post_with_csrf_token_from_page_works(self, mock_query):
        # Loading a page sets the csrftoken cookie, as the widget relies on.
        self.client.get(reverse("products:home"))
        token = self.client.cookies["csrftoken"].value
        resp = self.client.post(
            reverse("chatbot_chat"),
            {"message": "Can I return shoes?"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["reply"], FAKE_RESULT["reply"])


@override_settings(
    CHATBOT_RATE_LIMIT_MESSAGES=3,
    CHATBOT_RATE_LIMIT_IP_MESSAGES=5,
    CHATBOT_RATE_LIMIT_WINDOW_SECONDS=600,
)
@patch("chatbot.views.query_chat", return_value=FAKE_RESULT)
class ChatRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _send(self, client, ip="10.0.0.1", message="Can I return shoes?"):
        return client.post(
            reverse("chatbot_chat"), {"message": message}, content_type="application/json", REMOTE_ADDR=ip
        )

    def test_session_over_limit_gets_429_with_friendly_reply(self, mock_query):
        from chatbot.services import RATE_LIMIT_REPLY

        client = Client()
        for _ in range(3):
            self.assertEqual(self._send(client).status_code, 200)
        resp = self._send(client)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["reply"], RATE_LIMIT_REPLY)
        self.assertEqual(mock_query.call_count, 3)

    def test_sessions_have_separate_limits(self, mock_query):
        first, second = Client(), Client()
        for _ in range(3):
            self._send(first)
        self.assertEqual(self._send(first).status_code, 429)
        self.assertEqual(self._send(second).status_code, 200)

    def test_new_sessions_from_one_ip_hit_ip_limit(self, mock_query):
        # A script that drops its cookies gets a new session every time.
        for _ in range(5):
            self.assertEqual(self._send(Client()).status_code, 200)
        self.assertEqual(self._send(Client()).status_code, 429)
        self.assertEqual(self._send(Client(), ip="10.0.0.2").status_code, 200)

    def test_empty_question_does_not_count(self, mock_query):
        client = Client()
        for _ in range(5):
            self.assertEqual(self._send(client, message="").status_code, 400)
        self.assertEqual(self._send(client).status_code, 200)

    def test_limit_resets_after_window(self, mock_query):
        client = Client()
        for _ in range(3):
            self._send(client)
        self.assertEqual(self._send(client).status_code, 429)
        # Expiring the cache keys stands in for the window passing.
        cache.clear()
        self.assertEqual(self._send(client).status_code, 200)

    @override_settings(CHATBOT_RATE_LIMIT_MESSAGES=1)
    def test_limit_comes_from_settings(self, mock_query):
        client = Client()
        self.assertEqual(self._send(client).status_code, 200)
        self.assertEqual(self._send(client).status_code, 429)
