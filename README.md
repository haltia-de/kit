# b8k-compat

Общий код программ для планшета Lenovo YOGA Tablet 10 (B8000-H) и любого
Linux с GTK 3: программы [trudenboy/b8k-*](https://github.com/trudenboy)
берут из неё вопросы, настройки, звук и фото. Про оболочку планшета
библиотека не знает: крупный вид даёт GTK-тема среды (у планшета — тема
`b8k` из оболочки), связь со средой — через стандарты freedesktop.

| Модуль | Что даёт |
|---|---|
| `b8k_compat.settings` | `Settings(name)`: `~/.config/b8k/<name>.conf`, раздел `[name]`, да/нет как `yes`/`no` |
| `b8k_compat.dialogs` | `ask`, `choose`, `text`, `info`: вопросы внутри окна (GTK), «Отмена» по умолчанию |
| `b8k_compat.awake` | `Awake`: не засыпать, пока что-то играет (logind inhibit) |
| `b8k_compat.sound` | `Player`: звук через libmpv, без плейлистов и youtube-dl, не засыпает, пока играет |
| `b8k_compat.photos` | `kind_of`, `media_info`, `thumbnail` (миниатюры freedesktop в `~/.cache/thumbnails`, общие с файловыми менеджерами), `Thumbs`, `Viewer` (фото по одному: ‹ › и смахивание, + −, слайд-шоу) |
| `b8k_compat.formats` | `plural`, `nice_size`, `nice_duration`, `nice_date`, `month` |
| `b8k_compat.testing` | `FakePlayer`, `FakeInhibit` для тестов программ |

Зависимости: Python 3, PyGObject с GTK 3; для звука python-mpv (libmpv),
для подробностей о медиа ffmpeg и mutagen — без них просто меньше
подробностей. ffmpeg читает только сам файл: без сети и плейлистов.

Установка: `sh install.sh [ROOT]` (в системный site-packages), `pip install .`
или пакет Alpine из `packaging/alpine/APKBUILD`. Проверка:
`python3 tests/test_compat.py` (без экрана, libmpv и звуковой карты).

Решение о разделении на устройство, оболочку и программы:
[ADR 0001](https://github.com/trudenboy/lenovo-b8000h-linux/blob/main/docs/adr/0001-three-layers.md).

Лицензия — GPL-2.0.
