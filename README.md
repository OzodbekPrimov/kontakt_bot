# Kontakt Bot

Python 3.12+ va aiogram 3 asosidagi Telegram bot. Pastki menyular, raqam kartasi,
Telegram ID ko‘rish, foydalanish statistikasi va admin bilan ikki tomonlama aloqa.
Bitta polling jarayoni va bitta SQLite fayli bilan ishlaydi. Redis, tashqi worker,
cron, domen yoki Nginx talab qilinmaydi. Server qidiruvi sukut bo‘yicha o‘chiq.
Yoqilganda qidiruv shu jarayondagi saqlanadigan navbat orqali bajariladi.

## Foydalanuvchi menyusi

```text
[ 📱 Raqam ma’lumoti ] [ 🆔 Mening ID’im       ]
[ 👤 Boshqa user ID  ] [ 📊 Mening statistikam ]
[ ✉️ Adminga yozish  ] [ ℹ️ Yordam             ]
```

Asosiy menyu `ReplyKeyboardMarkup` bo‘lib, xabar yozish maydoni pastida turadi.
Profil havolalari, ro‘yxat sahifalari va tasdiqlash amallari xabar tagidagi inline tugmalardir.
`Bosh menyu`, `Orqaga` va `/cancel` tugallanmagan yozishmani/draftni bekor qiladi.

- **Raqam ma’lumoti**: raqam yoki kontakt yuboriladi. Davlat kodi, hudud, raqam turi,
  dastlabki operator, ehtimoliy vaqt zonalari va `t.me/+<raqam>?profile` havolasi chiqadi.
- **Mening ID’im**: aynan botga yozayotgan odamning ID, profil nomi va mavjud username’i.
- **Boshqa user ID**: Telegram’ning rasmiy user tanlash oynasi. Telegram qaytargan ID,
  nom va mavjud username chiqadi. Tanlangan odam bot foydalanuvchisi sifatida ro‘yxatga qo‘shilmaydi.
- **Kontakt**: `contact.user_id` mavjud bo‘lsa chiqariladi. Username faqat yuboruvchining
  o‘z kontakti bilan mos kelsa, uning `User` ma’lumotidan olinadi.
- **Mening statistikam**: kontaktlar, matnli raqamlar, user tanlash va natijalar soni.
- **Adminga yozish**: matn yoki bitta rasm, murojaat raqami va admin javobi shu botda.

Sukut bo‘yicha telefonning o‘zi orqali boshqa odamning Telegram ID/username’i aniqlanmaydi.
Server qidiruvi yoqilganda Telegram ruxsat bergan raqamlar bo‘yicha profil tekshiriladi.
Format tekshiruvi SIM faol yoki akkaunt Telegram’da borligini anglatmaydi.
Operator metadata’si dastlabki operatorni ko‘rsatishi mumkin; vaqt zonasi joriy joylashuv emas.
ID borligi bot o‘sha odamga xabar yubora olishini kafolatlamaydi.
Username bo‘lmagan profil havolasi ochilishi Telegram ruxsatlariga bog‘liq.

Mahalliy raqamlar uchun standart davlat UZ. Boshqa davlat raqamini to‘liq `+<davlat kodi>` bilan yuboring.

## Sozlamalar va lokal ishga tushirish

### Server qidiruvi pilot rejimi

`LOOKUP_ENABLED=false` holatida eski raqam kartasi va havola ishlaydi. Pilotni yoqish uchun
o‘zingizga tegishli oddiy Telegram akkaunti va `my.telegram.org` orqali olingan
`api_id`/`api_hash` kerak. Bot tokeni yoki admin ID oddiy akkaunt sessiyasini almashtirmaydi.
`python -m app.create_lookup_session` bu ma’lumotlar, telefon, login kodi va kerak bo‘lsa ikki
bosqichli parolni terminalda so‘rab, sessiyani `data/lookup-session.txt`ga saqlaydi; maxfiy
sessiyani terminalga chiqarmaydi. Faylni Gitga, chatga yoki boshqalarga yubormang. Dockerda
sessiyani volume ichida yaratish buyrug‘i:
`docker compose run --rm -it bot python -m app.create_lookup_session`.
[Telethon sessiya hujjati](https://docs.telethon.dev/en/stable/concepts/sessions.html).

```dotenv
LOOKUP_ENABLED=true
LOOKUP_API_ID=123456
LOOKUP_API_HASH=your-api-hash
LOOKUP_SESSION_FILE=data/lookup-session.txt
LOOKUP_FREE_LIMIT=5
```

Muqobil sozlama: `LOOKUP_SESSION=<StringSession>`. Sozlamalar to‘liq bo‘lmasa yoki sessiya oddiy
user akkauntiga tegishli bo‘lmasa, bot ishga tushmaydi. Sessiya yaratilgach uni faqat bitta
ishlayotgan jarayonga ulang.
Pilot uchun `/lookup_balance` qoldiqni ko‘rsatadi. Birinchi qidiruv 30 kunlik 5 ta bepul limitni
ochadi (`LOOKUP_FREE_LIMIT` orqali o‘zgartiriladi). Admin botga avval `/start` yuborgan userga
`/lookup_plan USER_ID 300 30` orqali yangi 300 ta / 30 kunlik tarifni qo‘lda ochadi.
Faol pullik tarif bor foydalanuvchiga ikkinchisi ochilmaydi. Bepul tarifdan pullikka
o‘tish mumkin; avval navbatga olingan so‘rovlar eski tarif hisobida qoladi.
Admin `/lookup_status` orqali navbat, yetkazilgan va aniqlanmagan natijalar hamda Telegram
kutish muddatini ko‘radi. Bu hisoblar telefon raqamlarini ko‘rsatmaydi.
Bu buyruq to‘lovni tekshirmaydi; faqat admin huquqi bilan ishlaydi. Jamoa/tashkilot tariflari,
to‘lov integratsiyasi va ko‘p akkauntli qidiruv hali yo‘q.

Raqam xalqaro formatga keltiriladi va navbatga yoziladi. Qidiruv `contacts.resolvePhone` bilan
kontaktga saqlamasdan bajariladi. Rasmiy 3 soniyalik oraliq va Telegram qaytargan `FLOOD_WAIT`
muddati saqlanadi. Topilmagan yoki maxfiylik sabab aniqlanmagan raqam uchun limit ayrilmaydi.
Limit faqat profil topilib, bot natijani muvaffaqiyatli yuborganda ayriladi. Tarmoq timeoutida
yuborish natijasi noma’lum bo‘lsa avtomatik qayta yuborilmaydi va limit ayrilmaydi.
Telegram foydalanuvchining profil havolasini ocha olganini botga tasdiqlamaydi.

`data/bot.sqlite3`dagi eskirgan `usage_events.success` qidiruv kvotasi sifatida ishlatilmaydi.
Yangi so‘rovlar, kvota hodisalari va kutish holati alohida saqlanadi. Bazaning versiyasi 2 ga
avtomatik o‘tadi; productionda yoqishdan oldin izchil backup oling. Navbatdagi raqamlar ishlov
tugaguncha bazada ochiq shaklda turadi, yakuniy holatga o‘tgach to‘liq raqam o‘chiriladi.
Shu sabab baza fayli va backuplarga kirishni cheklang; raqamlar Telegram chat tarixida ham qoladi.

`.env.example` namuna, haqiqiy sozlamalar `.env` ichida. Mavjud tokenni almashtirib yubormang.

```dotenv
BOT_TOKEN=your-botfather-token
ADMIN_IDS=[]
```

Adminni sozlash:

1. Botni `ADMIN_IDS=[]` bilan yoqing va unga `/start` yuboring.
2. **Mening ID’im** orqali o‘z ID’ingizni oling.
3. `.env`ga `ADMIN_IDS=[123456789]` yozing; misoldagi sonni o‘z ID’ingizga almashtiring.
4. Botni qayta yoqing va `/start` bosing. **Admin panel** tugmasi chiqadi.

Bir nechta admin: `ADMIN_IDS=[123456789,987654321]`. Har bir admin botga o‘zi `/start`
yuborgan bo‘lishi kerak, aks holda Telegram unga yangi murojaat bildirishnomasini yubormasligi mumkin.
Admin huquqi username yoki tanlangan userga emas, amaldagi jo‘natuvchining Telegram ID’siga qarab tekshiriladi.

Windows / PowerShell:

```powershell
# Faqat yangi virtual muhit kerak bo‘lsa:
py -3.12 -m venv .venv

# .env hali bo‘lmasa namuna nusxasini yarating:
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -u -m app.main
```

`/start`, `/help`, `/privacy`, `/myid`, `/myprofile`, `/stats`, `/admin`, `/cancel` mavjud.
Oddiy raqam yoki kontaktni menyuni ochmasdan ham yuborish mumkin.
Bot shaxsiy chatlar uchun ishlaydi.

## Admin panel

- **Umumiy statistika**: jami user, bugungi yangi/faol, oxirgi 7/30 kunda faol,
  bloklangani ma’lum userlar va foydalanish hodisalari.
- **Foydalanuvchilar**: sahifalangan ro‘yxat, aniq ID yoki @username bo‘yicha qidirish,
  profil havolasi, birinchi/oxirgi faollik, har bir userning kontakt/raqam statistikasi.
- **Murojaatlar**: yangi, javob berilgan, yopilgan murojaatlar; kartochkada oxirgi 5 xabar.
  Javob oldindan ko‘riladi va tasdiqlangach aynan murojaat egasiga yuboriladi.
- **Xabar yuborish**: bitta bot foydalanuvchisiga yoki barcha mavjud foydalanuvchilarga matn/rasm.
  Oldindan ko‘rish va bir martalik tasdiqlash majburiy. Ommaviy xabar qabul qiluvchilari preview
  paytida belgilanadi; keyin qo‘shilgan userlar bu xabarga avtomatik qo‘shilmaydi.
- **Yuborishlar**: oxirgi 10 kampaniya, holatlar, yangilash, to‘xtatish, qolganlarga davom ettirish.

“Faol” — davr ichida bot bilan amal bajargan odam; Telegram online holati emas.
“Bugun” Toshkent vaqti bilan yarim tundan boshlanadi. Jami user hisobiga adminlar ham kiradi.
Kontakt kartochkalari, matndagi raqamlar va user tanlash alohida sanaladi. Takror yetkazilgan
xabarlar hisobni ikki marta oshirmaydi. Avvalgi bazasiz MVP statistikasi ortga tiklanmaydi.

## Xabar yetkazish va restart

Eski havola rejimida foydalanish kvotasi yo‘q. Server qidiruvi rejimida tarif kvotasi bor.
Telegram `429` javobida ko‘rsatilgan `retry_after`
muddati kutiladi; retry soni texnik jihatdan cheklangan. Ommaviy yuborish bitta bot jarayonida,
faqat admin tasdiqlaganda ishlaydigan async vazifadir; alohida worker kerak emas.
Bir vaqtning o‘zida bitta ommaviy yuborish ishlaydi; qolgan saqlanganlarini paneldan boshlash mumkin.

Botni bloklagan user kampaniyani to‘xtatmaydi. Natijalar `sent`, `blocked`, `failed`, `unknown`
ko‘rinishida saqlanadi. Network timeout bo‘lsa xabar allaqachon yetgan bo‘lishi mumkin:
`unknown` natija avtomatik takrorlanmaydi. Exactly-once delivery kafolatlanmaydi.

Servis uzilganda yuborish holati saqlanadi; admin **Yuborishlar** orqali faqat `pending`
qabul qiluvchilarga davom ettiradi. Hali yuborilayotgan xabar `unknown` bo‘ladi.
Tasdiqlanmagan draftlar va menyu holatlari xotirada: restartdan keyin ularni qayta boshlash kerak.

## Saqlanadigan ma’lumotlar

`data/bot.sqlite3` avtomatik yaratiladi. Unda:

- Faqat bot bilan o‘zi muloqot qilgan userlarning ID, nom, username va faolligi.
- Eski havola rejimida so‘rov turi, natijasi va takror sanashdan saqlovchi message ID;
  tekshirilgan telefonning o‘zi yo‘q. Server qidiruvi rejimida navbatdagi telefon ishlov
  tugaguncha saqlanadi va yakuniy holatga o‘tgach o‘chiriladi.
- Adminga ataylab yuborilgan murojaat matni, Telegram rasm file_id’si va admin javoblari.
- Admin yuborishlari, yetkazish holatlari va asosiy admin amallari auditi.

Tanlangan uchinchi shaxslar uchun doimiy kontaktlar katalogi yoki telefon–profil indeksi tuzilmaydi.
Qidiruvlar, tokenlar va murojaat matnlari ilova logiga yozilmaydi.
Telefon havolalari Telegram chat tarixida qoladi.
`.env`, `data/`, WAL fayllar va backup Git/Docker build kontekstiga kirmaydi.

Qo‘lda, ishlayotgan bazadan izchil backup olish:

```bash
.venv/bin/python -m app.backup
```

Windows: `.\.venv\Scripts\python.exe -m app.backup`.
Natija `data/backups/` ichida yaratiladi. Bu cron yoki doimiy background task emas.
WAL yoqilgan ishlayotgan bazaning faqat bitta faylini oddiy nusxalash o‘rniga shu buyruqdan foydalaning.

## Server: bitta systemd service

Python 3.12+ o‘rnatilgan bo‘lishi kerak. Namuna aynan `/home/newuser/contact_bot` va
`newuser` uchun; boshqa joy bo‘lsa `deploy/kontakt-bot.service` ichidagi yo‘llarni moslang.

```bash
cd /home/newuser/contact_bot
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# .env ichida BOT_TOKEN va ADMIN_IDS ni sozlang.
chmod 600 .env

sudo cp deploy/kontakt-bot.service /etc/systemd/system/kontakt-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now kontakt-bot
sudo journalctl -u kontakt-bot -n 50 -f
```

Kod yoki `.env` yangilangach `sudo systemctl restart kontakt-bot` bajaring.
`data/`ni saqlang: unda statistika va murojaatlar bor. Bitta token bilan lokal va serverdagi
pollingni bir vaqtda yoqmang. `Ctrl+C` bilan logdan chiqish service’ni to‘xtatmaydi.

Docker varianti ham mavjud: `docker compose up --build -d`. SQLite uchun `bot_data` nomli
doimiy volume biriktirilgan. Docker va systemd variantlaridan bittasini ishlating.

## Tuzilma va testlar

Runtime kutubxonalari `requirements.txt` ichida sinovdan o‘tgan versiyalar bilan berilgan.
`pyproject.toml` paket metama’lumotlari, mos versiya oraliqlari va dev vositalarini saqlaydi.
Docker ham `requirements.txt` orqali shu runtime versiyalarini o‘rnatadi.

`domain` — qiymatlar va modellar; `application` — servislar va storage/transport kontraktlari;
`infrastructure` — SQLite va Telegram transporti; `presentation` — routerlar, keyboard, FSM va matnlar.
Router tartibi: navigation → admin → support → lookup. Shu sababli menyu matni adminga xabar sifatida ketmaydi.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check app tests
```

Testlar soxta Telegram transporti bilan ishlaydi; haqiqiy token, `.env`, Telegram API yoki
production bazasi ishlatilmaydi. Ruxsatlar, noto‘g‘ri ID bog‘lash, FSM, tasdiqlash, statistika
deduplikatsiyasi, murojaatlar, 429, timeout, backup va restartdan davom etish tekshiriladi.
