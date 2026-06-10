from app.content.models import ClassifierClass, QuickPhrase, Source
from app.core.text import normalize_text


cls = ClassifierClass.objects.get(slug="about")
Source.objects.filter(classifier_class=cls).delete()

body = """Центр Красок 1 предлагает уникальные решения для оживления вашего интерьера, представляя более 20 мировых брендов, которые подойдут для любых задач. Наша современная аппаратная система колеровки позволяет создать более 45 000 оттенков, что дает вам возможность найти идеальный цвет для вашего пространства. Мы сотрудничаем только с проверенными временем производителями, обеспечивая надежность и качество.

В нашем ассортименте вы найдете сертифицированные, экологичные и безопасные для здоровья продукты: лаки, масла, пропитки, декоративные штукатурки и краски, грунтовки и инструменты. Мы активно работаем с дизайнерами и представителями строительной сферы, что позволяет нам оставаться в курсе современных тенденций."""

source = Source.objects.create(
    classifier_class=cls,
    body=body,
    source_url="https://centr-krasok.kz/about/",
    is_active=True,
)

phrases = [
    "о компании",
    "кто вы",
    "кто вы такие",
    "что за центр красок",
    "чем занимается центр красок",
    "что предлагает центр красок",
    "что у вас есть",
    "что продаете",
    "какой ассортимент",
    "какие бренды у вас есть",
    "сколько брендов",
    "мировые бренды",
    "колеровка",
    "подбор цвета",
    "сколько оттенков",
    "экологичные материалы",
    "безопасные материалы",
    "сертифицированные материалы",
    "вы работаете с дизайнерами",
    "вы работаете со строителями",
]

seen = set()
created = 0
for priority, phrase in enumerate(phrases, start=1):
    normalized = normalize_text(phrase)
    if not normalized or normalized in seen:
        continue
    seen.add(normalized)
    QuickPhrase.objects.create(
        source=source,
        phrase=phrase,
        normalized_phrase=normalized,
        priority=priority,
        is_active=True,
    )
    created += 1

print("source", source.id)
print("quick", created)
