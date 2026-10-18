# SPDX-License-Identifier: GPL-2.0
"""Numbers, sizes, lengths and dates in Russian, as the programs show them.

    plural(5, "файл", "файла", "файлов")  -> "5 файлов"
    nice_size(2048)                       -> "2.0 КБ"
    nice_duration(3725)                   -> "1:02:05"
    nice_date(mtime)                      -> "17 окт" (with the year if not this one)
"""
import time

MONTHS_OF = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя",
             "дек")
MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
          "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def plural(n, one, few, many):
    tail = n % 100
    if 11 <= tail <= 14 or n % 10 in (0, 5, 6, 7, 8, 9):
        return "%d %s" % (n, many)
    return "%d %s" % (n, one if n % 10 == 1 else few)


def nice_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return ("%d %s" if unit == "Б" else "%.1f %s") % (n, unit)
        n /= 1024.0
    return ""


def nice_duration(sec):
    if sec is None:
        return ""
    sec = int(round(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def nice_date(mtime, now=None):
    t = time.localtime(mtime)
    n = time.localtime(now if now is not None else time.time())
    day = "%d %s" % (t.tm_mday, MONTHS_OF[t.tm_mon - 1])
    return day if t.tm_year == n.tm_year else "%s %d" % (day, t.tm_year)


def month(mtime):
    t = time.localtime(mtime)
    return "%s %d" % (MONTHS[t.tm_mon - 1], t.tm_year)
