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
| `haltia_kit.awake` | `Awake`: не засыпать, пока что-то играет — через `Gtk.Application.inhibit` (портал в Flatpak, менеджер сеанса на рабочем столе), иначе logind |
| `haltia_kit.sound` | `Player`: звук через libmpv, без плейлистов и youtube-dl, не засыпает, пока играет |
| `haltia_kit.photos` | `kind_of`, `media_info`, `thumbnail` (миниатюры freedesktop в `~/.cache/thumbnails`, общие с файловыми менеджерами), `Thumbs`, `Viewer` (фото по одному: ‹ › и смахивание, + −, слайд-шоу) |
| `haltia_kit.formats` | `plural`, `nice_size`, `nice_duration`, `nice_date`, `month` |
| `haltia_kit.paths` | `migrate_dirs(old, new)`: свои каталоги программы под новым именем, один раз, со ссылкой на старом месте |
| `haltia_kit.vault` | `Vault(app, folder)`: секреты (токены, cookies) в Secret Service через libsecret — gnome-keyring, KWallet, портал в Flatpak; где его нет, файл 0600 в каталоге программы; закрытая связка ключей — `Locked`, а не файл |
| `haltia_kit.testing` | `FakePlayer`, `FakeInhibit`, `FakeKeyring` для тестов программ |

Зависимости: Python 3, PyGObject с GTK 3 и libsecret (typelib `Secret-1`); для звука python-mpv (libmpv),
для подробностей о медиа ffmpeg и mutagen — без них просто меньше
подробностей. Чужие файлы (картинки, теги, обложки, видео) разбираются в
отдельном процессе с ограничением памяти и времени (`haltia_kit.worker`):
файл, который роняет или подвешивает разборщик, бросается и запоминается,
программа не страдает. ffmpeg читает только сам файл: без сети и плейлистов.

Установка:
- Ubuntu 24.04 и Debian 13 — пакет `python3-haltia-kit` из
  [репозитория apt Haltia](https://haltia-de.github.io/packages/) (его
  ставят программы как зависимость);
- Alpine и postmarketOS — `py3-haltia-kit` из репозитория apk Haltia;
- Flatpak — модуль `packaging/flatpak/haltia-kit.yml` в манифесте
  программы (kit по тегу, python-mpv, mutagen; libmpv и ffmpeg программа
  добавляет сама, если нужен звук);
- из исходников — `sh install.sh [ROOT]` (в системный site-packages) или
  `pip install .`.

Пути — только через каталоги XDG из GLib: в Flatpak `~` закрыт, а
настройки и данные лежат в `~/.var/app/<id>/`. Проверка:
`python3 tests/test_compat.py` (без экрана, libmpv и звуковой карты).

Решение о разделении на устройство, оболочку и программы:
[ADR 0001](https://github.com/haltia-de/haltia/blob/main/docs/adr/0001-three-layers.md).

Лицензия — GPL-2.0.
