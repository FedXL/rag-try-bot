from django.core.management.base import BaseCommand

from app.core.models import ArticleChunk, QAItem, QuickPhrase
from app.core.text import normalize_text


ABOUT_ARTICLE_KEY = "about-company"
ABOUT_TEXT = """Центр Красок 1 предлагает уникальные решения для оживления вашего интерьера, представляя более 20 мировых брендов, которые подойдут для любых задач. Наша современная аппаратная система колеровки позволяет создать более 45 000 оттенков, что дает вам возможность найти идеальный цвет для вашего пространства. Мы сотрудничаем только с проверенными временем производителями, обеспечивая надежность и качество.

В нашем ассортименте вы найдете сертифицированные, экологичные и безопасные для здоровья продукты: лаки, масла, пропитки, декоративные штукатурки и краски, грунтовки и инструменты. Мы активно работаем с дизайнерами и представителями строительной сферы, что позволяет нам оставаться в курсе современных тенденций."""

ABOUT_QUESTIONS = [
    "Чем занимается Центр Красок?",
    "Что предлагает Центр Красок?",
    "Какие решения для интерьера вы предлагаете?",
    "Можно ли у вас подобрать материалы для интерьера?",
    "Сколько брендов представлено в Центре Красок?",
    "Какие бренды у вас представлены?",
    "Вы работаете с мировыми брендами?",
    "Почему у вас большой выбор материалов?",
    "Можно ли у вас подобрать цвет краски?",
    "Есть ли у вас колеровка?",
    "Сколько оттенков можно получить при колеровке?",
    "Можно ли создать индивидуальный оттенок?",
    "Какая у вас система колеровки?",
    "Вы помогаете найти идеальный цвет?",
    "С какими производителями вы работаете?",
    "Почему вашей продукции можно доверять?",
    "Какие товары есть в ассортименте?",
    "Продаете ли вы лаки?",
    "Есть ли у вас масла для дерева?",
    "Можно ли купить пропитки?",
    "Есть ли декоративные штукатурки?",
    "Продаете ли вы краски?",
    "Есть ли грунтовки?",
    "Есть ли инструменты для ремонта?",
    "Ваша продукция сертифицирована?",
    "Материалы безопасны для здоровья?",
    "Есть ли экологичные материалы?",
    "Вы работаете с дизайнерами?",
    "Вы сотрудничаете со строителями?",
    "Почему вы знаете современные тенденции?",
    "Можно ли у вас подобрать материалы под задачу?",
    "Что входит в ассортимент магазина?",
    "Какие продукты можно купить для ремонта?",
    "Подойдут ли ваши материалы для разных задач?",
    "Вы продаете проверенную продукцию?",
    "Какие преимущества у Центра Красок?",
    "Почему стоит выбрать Центр Красок?",
    "Можно ли оживить интерьер с вашими материалами?",
    "Есть ли у вас решения для дизайнеров?",
    "Как вы обеспечиваете качество продукции?",
    "Кто вы такие?",
    "Вы вообще кто?",
    "Чем вы занимаетесь?",
    "Что у вас есть?",
    "Что продаете?",
    "Что можно купить?",
    "Вы магазин красок?",
    "У вас краски есть?",
    "А лаки есть?",
    "А грунтовки есть?",
    "А инструменты есть?",
    "Цвет подобрать можете?",
    "Колеровка есть?",
    "Можете намешать цвет?",
    "Сколько цветов делаете?",
    "У вас нормальные бренды?",
    "Брендов много?",
    "Вы с кем работаете?",
    "Материалы безопасные?",
    "Это не вредно?",
    "Есть эко товары?",
    "Документы на товары есть?",
    "Товары сертифицированы?",
    "Качество нормальное?",
    "Почему вам верить?",
    "Вы для ремонта подходите?",
    "Для интерьера что-то есть?",
    "Поможете с интерьером?",
    "Для дизайнеров работаете?",
    "Со строителями работаете?",
    "Что за Центр Красок?",
    "Это что за компания?",
    "Расскажите про себя",
    "Коротко о магазине",
    "Чем полезны?",
    "Что хорошего у вас?",
    "Почему к вам идти?",
    "У вас широкий ассортимент?",
    "Есть современные материалы?",
    "Вы следите за трендами?",
]


class Command(BaseCommand):
    help = "Seed initial knowledge base and mirror legacy QA items into article chunks."

    def handle(self, *args, **options):
        about_chunk, about_created = ArticleChunk.objects.update_or_create(
            article_key=ABOUT_ARTICLE_KEY,
            chunk_index=0,
            defaults={
                "section": "about",
                "source_url": "https://centr-krasok.kz/about/",
                "title": "О компании Центр Красок",
                "chunk_text": ABOUT_TEXT,
                "normalized_text": normalize_text(f"О компании Центр Красок {ABOUT_TEXT}"),
                "metadata": {"seed": "about"},
                "is_active": True,
            },
        )

        quick_created = 0
        quick_updated = 0
        for priority, phrase in enumerate(ABOUT_QUESTIONS, start=1):
            _, created = QuickPhrase.objects.update_or_create(
                normalized_phrase=normalize_text(phrase),
                section="about",
                article_key=ABOUT_ARTICLE_KEY,
                defaults={
                    "phrase": phrase,
                    "target_chunk_index": 0,
                    "priority": priority,
                    "metadata": {"seed": "about"},
                    "is_active": True,
                },
            )
            if created:
                quick_created += 1
            else:
                quick_updated += 1

        legacy_created = 0
        legacy_updated = 0
        for item in QAItem.objects.order_by("source_number").iterator():
            title = (item.question_ru or f"QA #{item.source_number}")[:500]
            text = f"{item.question_ru}\n\n{item.answer_ru}".strip()
            _, created = ArticleChunk.objects.update_or_create(
                article_key=f"catalog-qaitem-{item.source_number}",
                chunk_index=0,
                defaults={
                    "section": "catalog",
                    "source_url": "",
                    "title": title,
                    "chunk_text": text,
                    "normalized_text": normalize_text(f"{title} {text}"),
                    "metadata": {"legacy_qaitem_id": item.id, "source_number": item.source_number},
                    "is_active": True,
                },
            )
            if created:
                legacy_created += 1
            else:
                legacy_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                "sync_initial_knowledge done: "
                f"about_chunk={'created' if about_created else 'updated'}, "
                f"quick_created={quick_created}, quick_updated={quick_updated}, "
                f"legacy_created={legacy_created}, legacy_updated={legacy_updated}, "
                f"about_chunk_id={about_chunk.id}"
            )
        )
