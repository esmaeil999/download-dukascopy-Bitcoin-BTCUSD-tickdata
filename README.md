# Dukascopy Tick Downloader | دانلودکننده تیک‌دیتا | Загрузчик тиков | تحميل بيانات التيك | Descargador de ticks

[English](#english) | [فارسی](#فارسی) | [Русский](#русский) | [العربية](#العربية) | [Español](#español)

---


<a name="english"></a>
## 🇬🇧 English

### What it does
Downloads historical tick data from the public **Dukascopy** datafeed using GitHub Actions, converts it to the JForex-style CSV format, zips it, and publishes it as a **GitHub Release** — no local machine needed.

### Repository files
- `.github/workflows/main.yml` — the workflow (download → convert → fill gaps → gap check → zip → release)
- `download.py` — the downloader (fetches hourly ticks from the Dukascopy API with per-chunk progress logs)
- `convert.py` — converts raw CSV to the target format (`python convert.py INPUT.csv -o OUTPUT.csv`)
- `fill_gaps.py` — recovers hours the main API missed from Dukascopy's classic bi5 feed
- `gapcheck.py` — checks the output for missing hours/days and fails the run if gaps are found
- `debug_hours.py` — probes Dukascopy endpoints for specific hours (troubleshooting)
- `LICENSE` — MIT license

### How to use
1. Go to **Actions → Download Dukascopy tick data → Run workflow**.
2. Set **Instrument**, **Start** and **End** (GMT).
3. When the run finishes, download the zip from the **Releases** page.

### Output CSV format (GMT)
GmtTime,Bid,Ask,BidVolume,AskVolume
2024-01-01 22:00:12.108,42103.5,42105.1,0.02,0.02

### Importing into MetaTrader
**MetaTrader 5 (recommended):**
1. `View → Symbols (Ctrl+U)` → **Custom** → **Create Custom Symbol** (e.g. `BTCUSD.ticks`).
2. Open the symbol → **Ticks** tab → **Import Ticks**.
3. Use the custom symbol in the **Strategy Tester** (Every tick based on real ticks).

**MetaTrader 4:** MT4 cannot import tick CSVs natively. Use **TickStory** or **Birt's CSV2FXT** to convert the CSV into `.fxt`/`.hst` files for 99% modelling-quality backtests.

---

<a name="فارسی"></a>
## 🇮🇷 فارسی

### عملکرد
این پروژه تیک‌دیتای تاریخی را از فید عمومی **Dukascopy** با گیت‌هاب اکشن دانلود می‌کند، به فرمت CSV استایل JForex تبدیل می‌کند، زیپ می‌کند و در بخش **Releases** منتشر می‌کند — بدون نیاز به سیستم شخصی.

### فایل‌های ریپو
- `.github/workflows/main.yml` — ورک‌فلو (دانلود → تبدیل → بازیابی ساعت‌های جاافتاده → بررسی گپ → زیپ → ریلیز)
- `download.py` — دانلودر (تیک‌های ساعتی را با لاگ پیشرفت chunk‌به‌chunk از API دوکاسکوپی می‌گیرد)
- `convert.py` — تبدیل CSV خام به فرمت نهایی (`python convert.py INPUT.csv -o OUTPUT.csv`)
- `fill_gaps.py` — ساعت‌هایی را که API اصلی جا انداخته از فید کلاسیک bi5 دوکاسکوپی بازیابی می‌کند
- `gapcheck.py` — خروجی را از نظر ساعت/روز جاافتاده بررسی می‌کند و در صورت وجود گپ، اجرا را ناموفق می‌کند
- `debug_hours.py` — بررسی endpointهای دوکاسکوپی برای ساعت‌های مشخص (عیب‌یابی)
- `LICENSE` — مجوز MIT

### نحوه استفاده
1. به مسیر **Actions → Download Dukascopy tick data → Run workflow** بروید.
2. **نماد**، **شروع** و **پایان** (به وقت GMT) را وارد کنید.
3. پس از اتمام، فایل زیپ را از صفحه **Releases** دانلود کنید.

### فرمت خروجی (به وقت GMT)
​
GmtTime,Bid,Ask,BidVolume,AskVolume
2024-01-01 22:00:12.108,42103.5,42105.1,0.02,0.02
- `GmtTime`: زمان تیک با دقت میلی‌ثانیه
- `Bid` / `Ask`: قیمت خرید و فروش
- `BidVolume` / `AskVolume`: حجم به میلیون واحد

### انتقال دیتا به متاتریدر
**متاتریدر ۵ (پیشنهادی):**
1. از منوی `View → Symbols (Ctrl+U)` بخش **Custom** یک **نماد سفارشی** بسازید (مثلاً `BTCUSD.ticks`).
2. نماد را باز کنید → تب **Ticks** → **Import Ticks**.
3. سپس در **Strategy Tester** حالت *Every tick based on real ticks* را انتخاب کنید.

**متاتریدر ۴:** امکان ایمپورت مستقیم تیک CSV ندارد؛ با ابزارهایی مثل **TickStory** یا **CSV2FXT** فایل را به `.fxt`/`.hst` تبدیل کنید تا بک‌تست با کیفیت مدلینگ ۹۹٪ بگیرید.

---

<a name="русский"></a>
## 🇷🇺 Русский

### Что делает
Скачивает исторические тиковые данные с открытого фида **Dukascopy** через GitHub Actions, конвертирует в CSV формата JForex, архивирует и публикует в **Releases**.

### Файлы репозитория
- `.github/workflows/main.yml` — workflow (скачивание → конвертация → заполнение пропусков → проверка → архив → релиз)
- `download.py` — загрузчик (скачивает почасовые тики из API Dukascopy с логом прогресса по чанкам)
- `convert.py` — конвертер CSV (`python convert.py INPUT.csv -o OUTPUT.csv`)
- `fill_gaps.py` — восстанавливает часы, пропущенные основным API, из классического фида bi5 Dukascopy
- `gapcheck.py` — проверяет результат на пропущенные часы/дни и завершает запуск ошибкой при наличии пропусков
- `debug_hours.py` — проверяет эндпоинты Dukascopy для конкретных часов (диагностика)
- `LICENSE` — лицензия MIT

### Как использовать
1. Откройте **Actions → Download Dukascopy tick data → Run workflow**.
2. Укажите **инструмент**, **начало** и **конец** (по GMT).
3. После завершения скачайте zip со страницы **Releases**.

### Формат CSV (GMT)
​
GmtTime,Bid,Ask,BidVolume,AskVolume
2024-01-01 22:00:12.108,42103.5,42105.1,0.02,0.02

### Импорт в MetaTrader
**MetaTrader 5 (рекомендуется):**
1. `Вид → Символы (Ctrl+U)` → **Custom** → создайте **пользовательский символ** (например, `BTCUSD.ticks`).
2. Откройте символ → вкладка **Тики** → **Импорт тиков**.
3. Запускайте тесты в **Тестере стратегий** в режиме *Каждый тик на основе реальных тиков*.

**MetaTrader 4:** прямой импорт тиков не поддерживается. Используйте **TickStory** или **CSV2FXT** для создания файлов `.fxt`/`.hst` (качество моделирования 99%).

---

<a name="العربية"></a>
## 🇸🇦 العربية

### ماذا يفعل
يقوم بتنزيل بيانات التيك التاريخية من المصدر العام لـ **Dukascopy** عبر GitHub Actions، ويحوّلها إلى صيغة CSV بأسلوب JForex، ثم يضغطها وينشرها في قسم **Releases**.

### ملفات المستودع
- `.github/workflows/main.yml` — سير العمل (تنزيل → تحويل → استرجاع الساعات الناقصة → فحص الفجوات → ضغط → نشر)
- `download.py` — أداة التنزيل (تجلب التيكات ساعة بساعة من واجهة Dukascopy مع سجل تقدم لكل مقطع)
- `convert.py` — سكربت تحويل CSV (`python convert.py INPUT.csv -o OUTPUT.csv`)
- `fill_gaps.py` — يسترجع الساعات التي فاتتها الواجهة الرئيسية من خلاصة bi5 الكلاسيكية من Dukascopy
- `gapcheck.py` — يفحص البيانات الناتجة بحثًا عن ساعات/أيام ناقصة ويفشل التشغيل عند وجود فجوات
- `debug_hours.py` — يفحص نقاط نهاية Dukascopy لساعات محددة (استكشاف الأخطاء)
- `LICENSE` — ترخيص MIT

### طريقة الاستخدام
1. اذهب إلى **Actions → Download Dukascopy tick data → Run workflow**.
2. أدخل **الأداة** و**البداية** و**النهاية** (بتوقيت GMT).
3. بعد الانتهاء، حمّل الملف المضغوط من صفحة **Releases**.

### صيغة ملف CSV (بتوقيت GMT)
​
GmtTime,Bid,Ask,BidVolume,AskVolume
2024-01-01 22:00:12.108,42103.5,42105.1,0.02,0.02

### الاستيراد إلى MetaTrader
**MetaTrader 5 (موصى به):**
1. من `View → Symbols (Ctrl+U)` أنشئ **رمزًا مخصصًا** في قسم **Custom** (مثلاً `BTCUSD.ticks`).
2. افتح الرمز → تبويب **Ticks** → **Import Ticks**.
3. ثم استخدم **Strategy Tester** بوضع *Every tick based on real ticks*.

**MetaTrader 4:** لا يدعم الاستيراد المباشر للتيك. استخدم أدوات مثل **TickStory** أو **CSV2FXT** لتحويل الملف إلى `.fxt`/`.hst` للحصول على اختبار بجودة نمذجة 99%.

---

<a name="español"></a>
## 🇪🇸 Español

### Qué hace
Descarga datos históricos de ticks desde el feed público de **Dukascopy** mediante GitHub Actions, los convierte al formato CSV estilo JForex, los comprime y los publica en **Releases**.

### Archivos del repositorio
- `.github/workflows/main.yml` — flujo de trabajo (descarga → conversión → relleno de huecos → comprobación → zip → release)
- `download.py` — el descargador (obtiene los ticks por hora de la API de Dukascopy con logs de progreso por bloques)
- `convert.py` — script de conversión de CSV (`python convert.py INPUT.csv -o OUTPUT.csv`)
- `fill_gaps.py` — recupera del feed clásico bi5 de Dukascopy las horas que la API principal omitió
- `gapcheck.py` — comprueba si faltan horas/días en el resultado y hace fallar la ejecución si hay huecos
- `debug_hours.py` — sondea los endpoints de Dukascopy para horas concretas (diagnóstico)
- `LICENSE` — licencia MIT

### Cómo usarlo
1. Ve a **Actions → Download Dukascopy tick data → Run workflow**.
2. Indica el **instrumento**, el **inicio** y el **fin** (en GMT).
3. Al terminar, descarga el zip desde la página **Releases**.

### Formato del CSV (GMT)
​
GmtTime,Bid,Ask,BidVolume,AskVolume
2024-01-01 22:00:12.108,42103.5,42105.1,0.02,0.02

### Importar a MetaTrader
**MetaTrader 5 (recomendado):**
1. En `Ver → Símbolos (Ctrl+U)` crea un **símbolo personalizado** en **Custom** (p. ej. `BTCUSD.ticks`).
2. Abre el símbolo → pestaña **Ticks** → **Importar ticks**.
3. Después usa el **Probador de estrategias** con el modo *Cada tick basado en ticks reales*.

**MetaTrader 4:** no permite importar CSV de ticks directamente. Usa **TickStory** o **CSV2FXT** para generar archivos `.fxt`/`.hst` y obtener backtests con 99% de calidad de modelado.

---

### Notes | نکات | Примечания | ملاحظات | Notas
- Times are always **GMT** (matching Dukascopy/JForex). | زمان‌ها همیشه **GMT** است. | Время всегда **GMT**. | الأوقات دائمًا بتوقيت **GMT**. | Las horas son siempre **GMT**.
- Each GitHub Actions job is limited to ~6 hours; split long ranges. | هر Job حدود ۶ ساعت محدودیت دارد؛ بازه‌های طولانی را تقسیم کنید. | Лимит задачи ~6 часов; делите длинные диапазоны. | حد كل مهمة ٦ ساعات تقريبًا؛ قسّم النطاقات الطويلة. | Cada job tiene un límite de ~6 horas; divide rangos largos.
- Weekend/holiday hours simply contain no ticks. | ساعت‌های آخر هفته و تعطیل تیکی ندارند. | В выходные и праздники тиков нет. | لا توجد تيكات في عطلات نهاية الأسبوع والأعياد. | Los fines de semana y festivos no hay ticks.
- Failed hourly downloads are retried automatically, and hours the main API misses are recovered from the classic bi5 feed when possible; if the gap check still fails, open the failed run in **Actions** and click **Re-run failed jobs**. | دانلود ساعت‌های ناموفق خودکار تکرار می‌شود و ساعت‌هایی که API اصلی جا بیندازد در صورت وجود از فید کلاسیک bi5 بازیابی می‌شوند؛ اگر با این حال بررسی گپ ناموفق بود، در تب **Actions** ران ناموفق را باز کنید و **Re-run failed jobs** را بزنید. | Неудачные загрузки повторяются автоматически, а часы, пропущенные основным API, по возможности восстанавливаются из классического фида bi5; если проверка всё равно не проходит, откройте запуск во вкладке **Actions** и нажмите **Re-run failed jobs**. | تتم إعادة محاولة الساعات الفاشلة تلقائيًا، وتُسترجع الساعات التي تفوتها الواجهة الرئيسية من خلاصة bi5 الكلاسيكية عند الإمكان؛ وإذا استمر فشل الفحص، افتح التشغيل في **Actions** واضغط **Re-run failed jobs**. | Las descargas fallidas se reintentan automáticamente y las horas que la API principal omite se recuperan del feed clásico bi5 cuando es posible; si la comprobación sigue fallando, abre la ejecución en **Actions** y pulsa **Re-run failed jobs**.
- Telegram notifications are optional: set the **TELEGRAM_BOT_TOKEN** and **TELEGRAM_CHAT_ID** repository secrets to enable them; otherwise that step is skipped. | اعلان‌های تلگرام اختیاری است: برای فعال‌سازی، سکرت‌های **TELEGRAM_BOT_TOKEN** و **TELEGRAM_CHAT_ID** را در تنظیمات ریپو قرار دهید؛ در غیر این صورت آن مرحله رد می‌شود. | Уведомления Telegram опциональны: задайте секреты репозитория **TELEGRAM_BOT_TOKEN** и **TELEGRAM_CHAT_ID**; иначе этот шаг будет пропущен. | إشعارات تيليجرام اختيارية: عيّن السرّين **TELEGRAM_BOT_TOKEN** و**TELEGRAM_CHAT_ID** في إعدادات المستودع لتفعيلها، وإلا يتم تخطي تلك الخطوة. | Las notificaciones de Telegram son opcionales: define los secretos **TELEGRAM_BOT_TOKEN** y **TELEGRAM_CHAT_ID** en el repositorio; si no, ese paso se omite.
