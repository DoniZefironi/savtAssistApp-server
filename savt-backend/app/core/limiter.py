from slowapi import Limiter
from slowapi.util import get_remote_address

# Дефолт действует на все роуты без своего @limiter.limit — то есть почти на
# всю admin/operator-панель (там лимиты нигде явно не переопределены, в
# отличие от auth.py). 200/мин на IP там оказался мал: один открытый
# оператором экран разом бьёт по dashboard + список чатов + ШУ + по 3 запроса
# на каждый открытый чат (pinned/settings/messages), плюс рефетч всех
# активных запросов при возврате в вкладку/переподключении сети. Если за этим
# IP несколько операторов (общий офисный NAT) — бюджет на всех один. Роуты со
# своим @limiter.limit (auth.py — защита от брутфорса, telemetry — потоковая
# нагрузка) от этого значения не зависят, у них override_defaults=True.
limiter = Limiter(key_func=get_remote_address, default_limits=["1000/minute"])
