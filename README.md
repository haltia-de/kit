# kit

Общий код программ [Haltia](https://github.com/haltia-de) для любого
Linux с GTK 3: программы берут из неё вопросы, настройки, звук и фото. Про
оболочку библиотека не знает: вид даёт GTK-тема среды (в Haltia — тема из
оболочки), связь со средой — через стандарты freedesktop.

Модуль `haltia_kit`, пакет `py3-haltia-kit`. До ADR 0004 это был
`b8k_compat` (`py3-b8k-compat`): до версии 3.0 старое имя модуля работает
как псевдоним, пакет заменяет старый при `apk upgrade`, а настройки из
`~/.config/b8k` переезжают в `~/.config/haltia` при первом чтении.

| Модуль | Что даёт |
|---|---|
| `haltia_kit.settings` | `Settings(name)`: `~/.config/haltia/<name>.conf`, раздел `[name]`, да/нет как `yes`/`no` |
| `haltia_kit.dialogs` | `ask`, `choose`, `text`, `info`: вопросы внутри окна (GTK), «Отмена» по умолчанию |
| `haltia_kit.awake` | `Awake`: не засыпать, пока что-то играет (logind inhibit) |
| `haltia_kit.sound` | `Player`: звук через libmpv, без плейлистов и youtube-dl, не засыпает, пока играет |
| `haltia_kit.photos` | `kind_of`, `media_info`, `thumbnail` (миниатюры freedesktop в `~/.cache/thumbnails`, общие с файловыми менеджерами), `Thumbs`, `Viewer` (фото по одному: ‹ › и смахивание, + −, слайд-шоу) |
| `haltia_kit.formats` | `plural`, `nice_size`, `nice_duration`, `nice_date`, `month` |
| `haltia_kit.paths` | `migrate_dirs(old, new)`: свои каталоги программы под новым именем, один раз, со ссылкой на старом месте |
| `haltia_kit.testing` | `FakePlayer`, `FakeInhibit` для тестов программ |

Зависимости: Python 3, PyGObject с GTK 3; для звука python-mpv (libmpv),
для подробностей о медиа ffmpeg и mutagen — без них просто меньше
подробностей. Чужие файлы (картинки, теги, обложки, видео) разбираются в
отдельном процессе с ограничением памяти и времени (`haltia_kit.worker`):
файл, который роняет или подвешивает разборщик, бросается и запоминается,
программа не страдает. ffmpeg читает только сам файл: без сети и плейлистов.

Установка: `sh install.sh [ROOT]` (в системный site-packages), `pip install .`
или пакет Alpine из `packaging/alpine/APKBUILD`. Проверка:
`python3 tests/test_compat.py` (без экрана, libmpv и звуковой карты).

Решение о разделении на устройство, оболочку и программы:
[ADR 0001](https://github.com/haltia-de/device-lenovo-b8000h/blob/main/docs/adr/0001-three-layers.md).

Лицензия — GPL-2.0.
