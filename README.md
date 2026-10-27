# kit

Общий код программ [Haltia](https://github.com/haltia-de) для любого
Linux с GTK 3: программы берут из неё вопросы, настройки, звук и фото. Про
оболочку библиотека не знает: вид даёт GTK-тема среды (в Haltia — тема из
оболочки), связь со средой — через стандарты freedesktop.

Модуль `haltia_kit`, пакет `py3-haltia-kit`.

| Модуль | Что даёт |
|---|---|
| `haltia_kit.settings` | `Settings(name)`: `~/.config/haltia/<name>.conf`, раздел `[name]`, да/нет как `yes`/`no` |
| `haltia_kit.dialogs` | `ask`, `choose`, `text`, `info`: вопросы внутри окна (GTK), «Отмена» по умолчанию |
| `haltia_kit.awake` | `Awake`: не засыпать, пока что-то играет — через `Gtk.Application.inhibit` (портал в Flatpak, менеджер сеанса на рабочем столе), иначе logind; `hold_async(done)` — то же без ожидания системной шины в потоке GTK: ответ `done(held)` приходит позже, повторные вызовы присоединяются к запросу, `release()` отменяет его (поздний дескриптор сразу закрывается) |
| `haltia_kit.sound` | `Player`: звук через libmpv, без плейлистов и youtube-dl, не засыпает, пока играет |
| `haltia_kit.photos` | `kind_of`, `media_info`, `thumbnail` (миниатюры freedesktop в `~/.cache/thumbnails`, общие с файловыми менеджерами), `pixbuf_of`, `Thumbs`, `Viewer` (фото по одному: ‹ › и смахивание, + −, слайд-шоу) |
| `haltia_kit.pictures` | картинки через Pillow в процессе-читателе: не больше нужного размера (JPEG сразу уменьшенным), без увеличения, с запретом слишком больших |
| `haltia_kit.gallery` | `Thumbs` и `Viewer` (GTK); они же `photos.Thumbs` и `photos.Viewer` |
| `haltia_kit.camera` | `take_photo(parent)`: фото для любой программы — путь к снимку, который человек оставил, или `None`. Приложение «Камера» запускается с `--take-photo` отдельным экземпляром (`NON_UNIQUE`), печатает абсолютный путь последней строкой и выходит с 0; без пути — отмена; без приложения или при ошибке — диалог в самой программе. `available()` (опрос устройств медленный, ответ помнится 30 с), `Viewfinder` (видоискатель GStreamer: libcamera, иначе V4L2, MJPEG через jpegdec; фронтальная камера зеркальна только в видоискателе; `pause()`/`resume()` — отпустить камеру, когда окно не активно), `capture()` (снимок в «Изображения/Камера»; камера включается заново, поэтому снимок ждёт, пока установятся баланс белого и экспозиция: не меньше 30 кадров и пока средний цвет кадров не перестанет меняться), `Viewfinder.capture()` (следующий кадр работающего видоискателя — цвета как на экране); для `libcamerasrc` — контраст `CONTRAST` = 1,25 против «молочной» картинки soft ISP (`cameras(contrast=…)`, `None` — как у камеры); картинка поворачивается вместе с экраном по файлу оболочки `$XDG_RUNTIME_DIR/haltia/rotation` (`normal`, `90`, `180`, `270` — на сколько устройство повёрнуто по часовой стрелке) и по установке сенсора (Rotation из libcamera): `videoflip` в видоискателе, в `capture()` и в элементе `Camera.make()`, поворот меняется на ходу (следим за папкой файла: оболочка заменяет его переименованием); размер для своей цепочки — `Camera.make(caps="video/x-raw,width=480,height=360")`: фильтр сразу после источника, до `videoflip`, так что этот размер даёт сама камера (при повороте на 90 или 270 ширина и высота для неё меняются местами); без файла (ПК) поворота нет; занятая камера — состояние `BUSY`, а не исключение |
| `haltia_kit.share` | `send(path, parent)`: диалог «Отправить» — по большой кнопке (значок и имя) на каждую программу, которая берёт такой файл, и «Отмена» по умолчанию; спрашивает и при одной программе, при ни одной говорит «Отправить некуда»; не ждёт ответа. `targets(path_or_mime)`: эти программы. Получатель — программа, в `.desktop` которой есть `X-Haltia-Share=true`, `MimeType=` с типом файла (можно `image/*`) и в `Exec=` поле `%U`, `%u`, `%F` или `%f`; сама вызывающая программа (по идентификатору приложения) в список не попадает. Она запускается через `Gio.DesktopAppInfo.launch_uris` с адресом `file://` и получает файл аргументом (в `GApplication` — через `open`). Файл может лежать в папке отправителя: если получателю он нужен надолго, получатель его копирует |
| `haltia_kit.formats` | `plural`, `nice_size`, `nice_duration`, `nice_date`, `month` |
| `haltia_kit.paths` | `migrate_dirs(old, new)`: свои каталоги программы под новым именем, один раз, со ссылкой на старом месте |
| `haltia_kit.vault` | `Vault(app, folder)`: секреты (токены, cookies) в Secret Service через libsecret — gnome-keyring, KWallet, портал в Flatpak; где его нет, файл 0600 в каталоге программы; закрытая связка ключей — `Locked`, а не файл |
| `haltia_kit.crash` | падения программ для haltia-report в `$XDG_STATE_HOME/haltia/crashes`: необработанное исключение (в любом потоке) — запись `<app>-<время>.json` с полным traceback и последними 200 строками журнала; segfault — файл faulthandler `<app>-<время>-<pid>.fault`; хранятся 20 последних. Включается при импорте kit, `install(app_id, version, log)` уточняет имя, версию и журнал; `HALTIA_CRASH=no` выключает. Предупреждения GLib падениями не считаются |
| `haltia_kit.background` | `background(fetch, apply, latest=…, fallback=…, serial=…)`: медленное (команды, файлы, D-Bus) — в отдельном потоке, результат — в `apply` в главном цикле GTK через `GLib.idle_add`; `latest=КЛЮЧ` — применяется только результат последнего вызова с этим ключом, `fallback` — что получит `apply`, если `fetch` упал, `serial=True` — по очереди в одном потоке. Окно не замирает |
| `haltia_kit.testing` | `FakePlayer`, `FakeInhibit`, `FakeKeyring` для тестов программ |

Записи `haltia_kit.crash` читает служба оболочки `haltia-report`. Когда помощник включил отправку, оболочка передаёт отчёт в «Почту».

Зависимости: Python 3, PyGObject с GTK 3 и libsecret (typelib `Secret-1`), Pillow для картинок; для камеры GStreamer
с plugins-base и plugins-good (`v4l2src`), на устройствах с libcamera — её
модуль GStreamer (`libcamerasrc`); для звука python-mpv (libmpv),
для подробностей о медиа ffmpeg и mutagen — без них просто меньше
подробностей. Чужие файлы (картинки, теги, обложки, видео) разбираются в
отдельном процессе с ограничением памяти и времени, без GTK и без экрана
(`haltia_kit.worker`): файл, который роняет или подвешивает разборщик,
бросается и запоминается, программа не страдает. Сама программа картинок не
декодирует, она получает от процесса готовые точки. ffmpeg читает только сам
файл: без сети и плейлистов.

Установка:
- Ubuntu 24.04 и Debian 13 — пакет `python3-haltia-kit` из
  [репозитория apt Haltia](https://haltia-de.github.io/packages/) (его
  ставят программы как зависимость);
- Alpine и postmarketOS — `py3-haltia-kit` из репозитория apk Haltia;
- Flatpak — модуль `packaging/flatpak/haltia-kit.yml` в манифесте
  программы (kit по тегу, Pillow, python-mpv, mutagen; libmpv и ffmpeg программа
  добавляет сама, если нужен звук);
- из исходников — `sh install.sh [ROOT]` (в системный site-packages) или
  `pip install .`.

Пути — только через каталоги XDG из GLib: в Flatpak `~` закрыт, а
настройки и данные лежат в `~/.var/app/<id>/`. Проверка:
`python3 tests/test_compat.py` (без экрана, libmpv и звуковой карты).

Решение о разделении на устройство, оболочку и программы:
[ADR 0001](https://github.com/haltia-de/haltia/blob/main/docs/adr/0001-three-layers.md).

Лицензия — GPL-2.0.
