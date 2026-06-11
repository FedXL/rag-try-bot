from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from app.color_selection.service import answer_color_selection_message
from app.content.models import CLASS_DEFINITIONS, ClassifierClass, QuickPhrase, Source
from app.products.models import Product
from app.products.service import answer_product_message

from . import llm
from .classifier import classify_rule
from .models import ChatMessage, LLMRequestLog, TelegramUser
from .pipeline import answer_user_message
from .search import ensure_pg, search
from .tasks import send_telegram_tech_message_task
from .text import normalize_text


def seed_classes() -> dict[str, ClassifierClass]:
    result = {}
    for item in CLASS_DEFINITIONS:
        result[item["slug"]], _ = ClassifierClass.objects.update_or_create(
            slug=item["slug"],
            defaults={
                "title": item["title"],
                "description": item["description"],
                "kind": item["kind"],
            },
        )
    return result


class ClassifierTests(TestCase):
    def setUp(self):
        self.classes = seed_classes()

    def test_product_query_classifies_to_product(self):
        result = classify_rule("Dulux SKU12345 price?")

        self.assertTrue(result["need_search"])
        self.assertEqual(result["class_slug"], "product")
        self.assertEqual(result["section"], "product")
        self.assertEqual(result["intent"], "product_lookup")

    def test_greeting_does_not_search(self):
        result = classify_rule("/start")

        self.assertFalse(result["need_search"])
        self.assertEqual(result["class_slug"], "none")
        self.assertEqual(result["section"], "none")

    @patch("app.core.llm.chat")
    def test_llm_classifier_uses_dynamic_classes(self, chat_mock):
        chat_mock.return_value = """
        {
          "need_search": true,
          "need_rewrite": false,
          "query_type": "knowledge_base",
          "class_slug": "contacts",
          "section": "contacts",
          "intent": "pickup_location",
          "slots": {"city": "Алматы"},
          "rewritten_query": "где забрать заказ в Алматы адреса магазинов",
          "confidence": 0.91,
          "reason": "место получения"
        }
        """

        with patch("app.core.llm.XAI_API_KEY", "token"):
            result = llm.classify_message("А где забрать краску в Алматы", [], request_id="dynamic-classes")

        self.assertEqual(result["class_slug"], "contacts")
        self.assertEqual(result["section"], "contacts")
        self.assertEqual(result["engine"], "llm")
        prompt = chat_mock.call_args.args[0][1]["content"]
        self.assertIn("- contacts", prompt)
        self.assertIn("- product", prompt)
        self.assertIn("А где забрать краску в Алматы", prompt)

    @patch("app.core.llm.chat")
    def test_llm_classifier_does_not_include_inactive_class(self, chat_mock):
        self.classes["contacts"].is_active = False
        self.classes["contacts"].save(update_fields=["is_active"])
        chat_mock.return_value = '{"need_search": true, "class_slug": "product", "section": "product", "intent": "product_lookup", "slots": {}, "rewritten_query": "Dulux", "confidence": 0.8, "reason": "товар"}'

        with patch("app.core.llm.XAI_API_KEY", "token"):
            llm.classify_message("есть краска Dulux в Алматы", [], request_id="inactive-class")

        prompt = chat_mock.call_args.args[0][1]["content"]
        self.assertNotIn("- contacts", prompt)
        self.assertIn("- product", prompt)

    def test_rule_fallback_does_not_return_inactive_class(self):
        self.classes["product"].is_active = False
        self.classes["product"].save(update_fields=["is_active"])

        result = llm.enforce_active_class(
            {
                "need_search": True,
                "class_slug": "product",
                "section": "product",
                "intent": "product_lookup",
                "reason": "rule result",
            }
        )

        self.assertNotEqual(result["class_slug"], "product")
        self.assertEqual(result["intent"], "inactive_class")

    @patch("app.core.llm.chat")
    def test_unknown_llm_class_slug_normalizes_to_mixed(self, chat_mock):
        chat_mock.return_value = '{"need_search": true, "class_slug": "delivery", "section": "delivery", "intent": "delivery", "slots": {}, "rewritten_query": "доставка", "confidence": 0.8, "reason": "bad class"}'

        with patch("app.core.llm.XAI_API_KEY", "token"):
            result = llm.classify_message("как оформить доставку", [], request_id="unknown-class")

        self.assertEqual(result["class_slug"], "mixed")
        self.assertEqual(result["section"], "mixed")

    @patch("app.core.llm.chat")
    def test_start_bypasses_llm_classifier(self, chat_mock):
        with patch("app.core.llm.XAI_API_KEY", "token"):
            result = llm.classify_message("/start", [], request_id="start-bypass")

        self.assertEqual(result["class_slug"], "none")
        chat_mock.assert_not_called()

    def test_color_selection_class_has_custom_kind(self):
        self.assertEqual(self.classes["color_selection"].kind, "color_selection")


class SearchTests(TestCase):
    def setUp(self):
        ensure_pg()
        self.classes = seed_classes()

    def test_class_source_search_returns_sources_without_quick_phrase(self):
        source = Source.objects.create(
            classifier_class=self.classes["help"],
            body="Delivery is available in the city.",
        )

        result = search("Do you deliver?", classification={"class_slug": "help"}, request_id="test-quick")

        self.assertEqual(result["route"], "class_sources")
        self.assertEqual(result["decision"], "FOUND")
        self.assertEqual(result["top_candidate"]["content_type"], "source")
        self.assertEqual(result["top_candidate"]["class_slug"], "help")
        self.assertEqual(result["quick_phrase"], None)
        self.assertEqual(result["top_candidate"]["source_id"], source.id)

    def test_class_source_search_limits_sources_to_three(self):
        for index in range(4):
            Source.objects.create(
                classifier_class=self.classes["contacts"],
                body=f"Contacts article {index}",
            )

        result = search("where can I buy paint in Almaty", classification={"class_slug": "contacts"}, request_id="test-source-limit")

        self.assertEqual(result["route"], "class_sources")
        self.assertEqual(result["decision"], "FOUND")
        self.assertEqual(len(result["candidates"]), 3)
        self.assertEqual(result["retriever_breakdown"]["class_sources"], 3)

    def test_class_source_search_returns_not_found_without_sources(self):
        result = search("ideas", classification={"class_slug": "inspiration"}, request_id="test-empty-source-class")

        self.assertEqual(result["route"], "class_sources")
        self.assertEqual(result["decision"], "NOT_FOUND")
        self.assertEqual(result["candidates"], [])

    def test_product_search_returns_product_candidate(self):
        Product.objects.create(
            product_key="product-1",
            name="Dulux white paint",
            sku="SKU12345",
            brand="Dulux",
            category_name="Paint",
            price_kzt=12000,
            description="Dulux white paint. SKU: SKU12345. Price: 12000 KZT.",
            normalized_text=normalize_text("Dulux white paint SKU12345 price 12000 KZT"),
        )

        result = search("Dulux SKU12345 price", classification={"class_slug": "product"}, request_id="test-product")

        self.assertEqual(result["decision"], "FOUND")
        self.assertEqual(result["route"], "product")
        self.assertEqual(result["top_candidate"]["content_type"], "product")
        self.assertEqual(result["top_candidate"]["sku"], "SKU12345")

    def test_classifier_none_skips_search(self):
        result = search("/start", classification={"need_search": False, "class_slug": "none"}, request_id="test-none")

        self.assertEqual(result["decision"], "SKIPPED")
        self.assertEqual(result["route"], "classifier")
        self.assertEqual(result["candidates"], [])


class ProductFlowTests(TestCase):
    def setUp(self):
        self.llm_patch = patch("app.products.service.llm.has_llm", return_value=False)
        self.llm_patch.start()

    def tearDown(self):
        self.llm_patch.stop()

    def product(self, **kwargs):
        defaults = {
            "product_key": kwargs.get("sku", "product-1"),
            "name": "Краска Dulux Ultra Resist Для детской BC 2,25",
            "sku": "5811071",
            "brand": "Dulux",
            "category_name": "Интерьерные краски",
            "top_category": "Интерьерные краски",
            "leaf_category": "Интерьерные краски",
            "price_kzt": 24190,
            "currency": "KZT",
            "availability": "in_stock",
            "can_buy": True,
            "stock_by_city": {"Алматы": "9 шт.", "Астана": "2 шт."},
            "description": "Краска для стен.",
            "normalized_text": normalize_text("Dulux Ultra Resist детская интерьерные краски стены 5811071 Алматы Астана"),
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)

    def test_product_price_by_sku_is_deterministic(self):
        self.product()

        result = answer_product_message("цена артикула 5811071", {"class_slug": "product"})

        self.assertIn("Цена: 24 190 KZT", result["answer"])
        self.assertEqual(result["metadata"]["intent"], "price_check")
        self.assertEqual(result["metadata"]["sku"], "5811071")

    def test_product_stock_by_city(self):
        self.product()

        result = answer_product_message("есть ли артикул 5811071 в Алматы", {"class_slug": "product"})

        self.assertIn("Алматы: 9 шт.", result["answer"])
        self.assertEqual(result["metadata"]["intent"], "availability_check")

    def test_unknown_sku_does_not_fuzzy_match_other_product(self):
        self.product(sku="30-0033", product_key="roller-1", name="Сменный валик 30-0033")

        result = answer_product_message("цена артикула 5811071", {"class_slug": "product"})

        self.assertIn("5811071", result["answer"])
        self.assertEqual(result["metadata"]["product_ids"], [])

    def test_hyphenated_sku_keeps_full_article(self):
        self.product(sku="30-0232", product_key="roller-2", name="Сменный валик 30-0232")

        result = answer_product_message("цена артикула 30-0232", {"class_slug": "product"})

        self.assertEqual(result["metadata"]["sku"], "30-0232")
        self.assertEqual(len(result["metadata"]["product_ids"]), 1)

    def test_compare_brands_requires_category(self):
        result = answer_product_message("сравни Dulux и Marshall", {"class_slug": "product"})

        self.assertIn("Уточните", result["answer"])
        self.assertEqual(result["metadata"]["intent"], "compare_products")

    def test_compare_brands_with_category(self):
        self.product(name="Dulux Easy 5л", sku="DULUX1", price_kzt=20000, normalized_text=normalize_text("dulux интерьерные краски стены"))
        self.product(
            product_key="marshall-1",
            name="Marshall Maestro 5л",
            sku="MARSH1",
            brand="Marshall",
            price_kzt=15000,
            normalized_text=normalize_text("marshall интерьерные краски стены"),
        )

        result = answer_product_message("сравни Dulux и Marshall интерьерные краски", {"class_slug": "product"})

        self.assertIn("Dulux", result["answer"])
        self.assertIn("Marshall", result["answer"])
        self.assertIn("15 000 KZT", result["answer"])

    def test_budget_requires_area(self):
        result = answer_product_message("посчитай бюджет на краску Dulux", {"class_slug": "product"})

        self.assertIn("площадь", result["answer"])
        self.assertEqual(result["metadata"]["intent"], "budget_estimate")

    def test_budget_estimate_uses_price_and_package(self):
        self.product(name="Dulux Easy 5л", sku="DULUX5", price_kzt=20000, normalized_text=normalize_text("dulux стены интерьерные краски"))

        result = answer_product_message("посчитай бюджет Dulux для стен 20 м2", {"class_slug": "product"})

        self.assertIn("20", result["answer"])
        self.assertIn("20 000 KZT", result["answer"])

    @patch("app.core.pipeline.llm.grounded_answer")
    @patch("app.core.pipeline.llm.classify_message")
    @patch("app.core.pipeline.notify_tech")
    def test_pipeline_uses_product_branch_without_grounded_llm(self, notify_tech, classify_message, grounded_answer):
        self.product()
        user = TelegramUser.objects.create(telegram_id=555, username="buyer")
        classify_message.return_value = {
            "need_search": True,
            "class_slug": "product",
            "section": "product",
            "intent": "product_price",
            "rewritten_query": "цена артикула 5811071",
            "engine": "test",
        }

        result = answer_user_message(user, "цена артикула 5811071", request_id="product-branch")

        self.assertEqual(result["metadata"]["route"], "product")
        self.assertIn("Цена: 24 190 KZT", result["answer"])
        grounded_answer.assert_not_called()


    @patch("app.core.pipeline.search")
    @patch("app.core.pipeline.answer_color_selection_message")
    @patch("app.core.pipeline.llm.grounded_answer")
    @patch("app.core.pipeline.llm.classify_message")
    @patch("app.core.pipeline.notify_tech")
    def test_pipeline_uses_color_selection_branch_without_search(
        self,
        notify_tech,
        classify_message,
        grounded_answer,
        answer_color_selection_message,
        search_mock,
    ):
        user = TelegramUser.objects.create(telegram_id=556, username="color")
        classify_message.return_value = {
            "need_search": True,
            "class_slug": "color_selection",
            "section": "color_selection",
            "intent": "color_selection",
            "rewritten_query": "pick wall color",
            "engine": "test",
        }
        answer_color_selection_message.return_value = {
            "answer": "Color answer",
            "metadata": {"route": "color_selection", "intent": "color_selection", "engine": "test"},
            "debug": {"route": "color_selection"},
        }

        result = answer_user_message(user, "pick wall color", request_id="color-branch")

        self.assertEqual(result["metadata"]["route"], "color_selection")
        self.assertEqual(result["answer"], "Color answer")
        answer_color_selection_message.assert_called_once()
        search_mock.assert_not_called()
        grounded_answer.assert_not_called()


class ColorSelectionFlowTests(TestCase):
    def test_general_color_question_returns_three_site_links(self):
        result = answer_color_selection_message("как подобрать цвет краски", {"intent": "color_selection"})

        self.assertEqual(result["metadata"]["engine"], "link_flow")
        self.assertEqual(result["metadata"]["option"], "all")
        self.assertIn("Здорово, у нас есть несколько приятных способов подобрать цвет.", result["answer"])
        self.assertIn("https://centr-krasok.kz/tinting/", result["answer"])
        self.assertIn("https://centr-krasok.kz/colors/psychotype/", result["answer"])
        self.assertIn("https://centr-krasok.kz/tints/", result["answer"])
        self.assertNotIn("напишите 1", result["answer"].lower())

    def test_psychotype_request_returns_only_psychotype_link(self):
        result = answer_color_selection_message("давай по психотипу", {"intent": "color_selection_by_psychotype"})

        self.assertEqual(result["metadata"]["option"], "psychotype")
        self.assertIn("Подбор цвета по психотипу", result["answer"])
        self.assertIn("https://centr-krasok.kz/colors/psychotype/", result["answer"])
        self.assertNotIn("https://centr-krasok.kz/tinting/", result["answer"])
        self.assertNotIn("https://centr-krasok.kz/tints/", result["answer"])

    def test_request_for_shades_sends_to_palettes_without_inventing_colors(self):
        result = answer_color_selection_message(
            "хочу 3-4 оттенка",
            {"intent": "suggest_shades"},
            [{"role": "assistant", "content": "Здорово, у нас есть несколько приятных способов подобрать цвет."}],
        )

        self.assertEqual(result["metadata"]["option"], "palettes")
        self.assertIn("Палитры оттенков", result["answer"])
        self.assertIn("https://centr-krasok.kz/tints/", result["answer"])


class SearchApiTests(TestCase):
    @patch("app.core.views.llm.classify_message")
    def test_manual_search_uses_classifier_and_can_skip_database_search(self, classify_message):
        classify_message.return_value = {
            "need_search": False,
            "class_slug": "none",
            "section": "none",
            "intent": "greeting",
            "engine": "test",
        }

        response = self.client.post("/api/search/", {"query": "/start"}, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["decision"], "SKIPPED")
        self.assertEqual(payload["route"], "classifier")
        self.assertEqual(payload["classification"]["class_slug"], "none")
        classify_message.assert_called_once()


class ChatApiDebugTests(TestCase):
    @patch("app.core.views.answer_user_message")
    def test_chat_debug_is_not_requested_for_regular_user(self, answer_user_message_mock):
        TelegramUser.objects.create(telegram_id=716336613, username="regular", is_superuser=False)
        answer_user_message_mock.return_value = {"answer": "ok", "metadata": {"route": "direct"}}

        response = self.client.post(
            "/api/chat/",
            {
                "request_id": "regular-debug",
                "telegram_id": 716336613,
                "message": "test",
                "debug": True,
            },
            content_type="application/json",
            HTTP_X_INTERNAL_API_TOKEN=settings.INTERNAL_API_TOKEN,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(answer_user_message_mock.call_args.kwargs["debug_requested"])

    @patch("app.core.views.answer_user_message")
    def test_chat_debug_is_requested_for_superuser(self, answer_user_message_mock):
        TelegramUser.objects.create(telegram_id=1001, username="admin", is_superuser=True)
        answer_user_message_mock.return_value = {"answer": "ok", "metadata": {"route": "direct"}}

        response = self.client.post(
            "/api/chat/",
            {
                "request_id": "superuser-debug",
                "telegram_id": 1001,
                "message": "test",
                "debug": True,
            },
            content_type="application/json",
            HTTP_X_INTERNAL_API_TOKEN=settings.INTERNAL_API_TOKEN,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(answer_user_message_mock.call_args.kwargs["debug_requested"])


class TelegramTechNotificationTests(TestCase):
    @patch("app.core.tasks.TelegramNotifier")
    def test_tech_message_task_skips_regular_user_messages(self, notifier_class):
        TelegramUser.objects.create(telegram_id=1001, username="admin", is_superuser=True)
        TelegramUser.objects.create(telegram_id=1002, username="regular", is_superuser=False)
        notifier = notifier_class.return_value
        notifier.enabled.return_value = True
        notifier.send_message.return_value = 1

        result = send_telegram_tech_message_task.run(
            {
                "request_id": "test-tech",
                "stage": "pipeline",
                "event": "start",
                "telegram_id": 1002,
                "username": "regular",
                "details": {"text_len": 10},
            }
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "source_user_not_superuser")
        notifier.send_message.assert_not_called()

    @patch("app.core.tasks.TelegramNotifier")
    def test_tech_message_task_sends_superuser_messages_to_source_user(self, notifier_class):
        TelegramUser.objects.create(telegram_id=1001, username="admin", is_superuser=True)
        TelegramUser.objects.create(telegram_id=1002, username="other_admin", is_superuser=True)
        notifier = notifier_class.return_value
        notifier.enabled.return_value = True
        notifier.send_message.return_value = 1

        result = send_telegram_tech_message_task.run(
            {
                "request_id": "test-tech",
                "stage": "pipeline",
                "event": "start",
                "telegram_id": 1001,
                "username": "admin",
                "details": {"text_len": 10},
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recipients"], 1)
        self.assertEqual(result["sent"], 1)
        notifier.send_message.assert_called_once()
        self.assertEqual(notifier.send_message.call_args.args[0], 1001)


class LLMRequestLogTests(TestCase):
    @patch("app.core.llm.client")
    def test_chat_logs_request_and_response(self, client_factory):
        response = SimpleNamespace(
            id="resp-1",
            created=123,
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer text"))],
        )
        client_factory.return_value.chat.completions.create.return_value = response

        with patch("app.core.llm.XAI_API_KEY", "token"), patch("app.core.llm.XAI_MODEL", "test-model"):
            result = llm.chat(
                [{"role": "user", "content": "hello"}],
                temperature=0.1,
                request_id="llm-log-test",
                purpose="unit_test",
            )

        self.assertEqual(result, "answer text")
        row = LLMRequestLog.objects.get(request_id="llm-log-test")
        self.assertEqual(row.purpose, "unit_test")
        self.assertEqual(row.model, "test-model")
        self.assertEqual(row.status, "success")
        self.assertEqual(row.request_messages[0]["content"], "hello")
        self.assertEqual(row.response_text, "answer text")


class ChatHistoryTests(TestCase):
    @patch("app.core.pipeline.llm.classify_message")
    @patch("app.core.pipeline.notify_tech")
    def test_start_resets_existing_user_history(self, notify_tech, classify_message):
        user = TelegramUser.objects.create(telegram_id=777, username="u")
        ChatMessage.objects.create(user=user, role=ChatMessage.ROLE_USER, text="old question")
        ChatMessage.objects.create(user=user, role=ChatMessage.ROLE_ASSISTANT, text="old answer")

        result = answer_user_message(user, "/start", request_id="start-reset")

        self.assertIn("answer", result)
        messages = list(ChatMessage.objects.filter(user=user).order_by("created_at"))
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].text, "/start")
        self.assertEqual(messages[0].role, ChatMessage.ROLE_USER)
        self.assertEqual(messages[1].role, ChatMessage.ROLE_ASSISTANT)
        classify_message.assert_not_called()
