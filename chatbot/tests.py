from django.test import TestCase
from django.urls import reverse


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
