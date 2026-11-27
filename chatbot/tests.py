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

    def test_reply_mentioning_as_an_ai_is_still_replaced(self):
        from chatbot.services import _NO_INFO_REPLIES, _sanitize_reply

        self.assertIn(_sanitize_reply("As an AI, I don't know."), _NO_INFO_REPLIES)
