# exchange-mcp

[English](README.md)

اتصال مستقیم Claude Desktop و Claude Code به **Exchange سازمانی** (نسخه‌های 2016، 2019 و SE) از طریق **EWS**.

کانکتور رسمی Microsoft 365 فقط با Exchange Online کار می‌کند. گزینه‌های موجود برای Exchange داخلی هم یا ایمیل‌ها را از سرور شخص ثالث عبور می‌دهند یا درایور پولی لازم دارند. exchange-mcp روی همین کامپیوتر اجرا می‌شود و مستقیم با سرور Exchange خودتان حرف می‌زند؛ هیچ سرویس میانی وجود ندارد. اگر این سیستم به OWA دسترسی دارد، این سرور هم به Exchange می‌رسد.

## چه کارهایی از Claude برمی‌آید

| ابزار | کار | تغییر می‌دهد؟ |
|---|---|---|
| `mailbox_info` | بررسی اتصال: آدرس، نسخه‌ی Exchange، منطقه‌ی زمانی، تعداد ایمیل‌های Inbox | نه |
| `list_folders` | پوشه‌ها با تعداد خوانده‌نشده و کل | نه |
| `search_emails` | جستجو، جدیدترین اول؛ متن آزاد (AQS) یا فیلترهای ساخت‌یافته | نه |
| `get_email` | متن کامل ایمیل، گیرنده‌ها و فهرست پیوست‌ها؛ برای دعوت‌نامه، زمان جلسه | نه |
| `save_attachment` | ذخیره‌ی پیوست در پوشه‌ی Downloads؛ فایل متنی را مستقیم هم برمی‌گرداند | فایل محلی |
| `mark_as_read` | علامت خوانده / خوانده‌نشده | صندوق |
| `create_draft` | ذخیره‌ی ایمیل جدید در Drafts، بدون ارسال | صندوق |
| `create_reply_draft` | ذخیره‌ی پاسخ یا پاسخ به همه در Drafts، در همان رشته‌ی گفتگو | صندوق |
| `list_events` | رویدادهای تقویم در یک بازه؛ جلسه‌های تکرارشونده باز می‌شوند | نه |
| `get_event` | جزئیات جلسه، پاسخ شرکت‌کننده‌ها و دستور جلسه | نه |
| `get_availability` | زمان آزاد و اشغال همکارها، به‌علاوه‌ی بازه‌هایی که همه آزادند | نه |
| `find_people` | جستجو در دفترچه‌ی آدرس سازمان (GAL) و مخاطبان | نه |
| `create_event` | ثبت رویداد در تقویم؛ با شرکت‌کننده، دعوت‌نامه می‌فرستد (فقط اگر ارسال فعال باشد) | صندوق / ارسال |
| `send_email`، `send_draft` | فقط وقتی ارسال فعال است دیده می‌شوند | ارسال |

در نتایج جستجو هر مورد نوعش را هم دارد (`email`، `meeting_request`، `meeting_cancellation` و…)، چون صندوق ورودی علاوه بر ایمیل، دعوت‌نامه و لغو جلسه هم نگه می‌دارد.

متن فارسی ایمیل‌ها خودکار راست‌به‌چپ تنظیم می‌شود تا در Outlook درست نمایش داده شود.

## نصب از Release (پیشنهادی)

از صفحه‌ی [Releases](../../releases) فایل مناسب سیستم‌تان را بردارید:

| سیستم | فایل |
|---|---|
| ویندوز | `exchange-mcp-<نسخه>-windows-x64.mcpb` |
| مک با تراشه‌ی Apple (M1 به بعد) | `exchange-mcp-<نسخه>-macos-arm64.mcpb` |
| مک اینتل | `exchange-mcp-<نسخه>-macos-x64.mcpb` |

1. روی فایل `.mcpb` دو بار کلیک کنید (یا در Claude Desktop از بخش Settings ← Extensions نصبش کنید).
2. فرم نصب را پر کنید: آدرس ایمیل، آدرس EWS و رمز عبور. آدرس EWS همان آدرس OWA است که به‌جای `/owa` در انتهایش `/EWS/Exchange.asmx` می‌آید؛ مثلاً `https://mail.company.com/EWS/Exchange.asmx`.
3. رمز را Claude Desktop در فضای امن سیستم‌عامل نگه می‌دارد، نه در فایل تنظیمات.

برای نصب به Python نیازی نیست؛ فایل اجرایی همه‌چیز را همراه دارد.

فایل‌های اجرایی امضای دیجیتال ندارند. ویندوز ممکن است هنگام دانلود هشدار SmartScreen بدهد. در مک، افزونه‌ی `.mcpb` پرچم قرنطینه‌ی Gatekeeper را خودش برمی‌دارد؛ اگر فایل اجرایی مستقل را دانلود کرده‌اید، یک بار این دستور را بزنید:

```bash
xattr -d com.apple.quarantine exchange-mcp-*-macos-* && chmod +x exchange-mcp-*-macos-*
```

## نصب از سورس

برای توسعه یا وقتی می‌خواهید تنظیمات را دستی کنترل کنید:

```powershell
cd D:\Projects\Packages\exchange-mcp
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

رمز را یک بار در Windows Credential Manager ذخیره کنید:

```powershell
.\.venv\Scripts\exchange-mcp.exe set-password --username "you@company.com"
```

اتصال را امتحان کنید. این دستور اطلاعات صندوق و سه مورد آخر Inbox را نشان می‌دهد، یا دلیل خطا را توضیح می‌دهد:

```powershell
$env:EXCHANGE_EMAIL = "you@company.com"
$env:EXCHANGE_EWS_URL = "https://mail.company.com/EWS/Exchange.asmx"
.\.venv\Scripts\exchange-mcp.exe check
```

بعد این بخش را به `%APPDATA%\Claude\claude_desktop_config.json` اضافه کنید:

```json
{
  "mcpServers": {
    "exchange": {
      "command": "D:\\Projects\\Packages\\exchange-mcp\\.venv\\Scripts\\exchange-mcp.exe",
      "env": {
        "EXCHANGE_EMAIL": "you@company.com",
        "EXCHANGE_EWS_URL": "https://mail.company.com/EWS/Exchange.asmx"
      }
    }
  }
}
```

Claude Desktop را از آیکون کنار ساعت کامل ببندید و دوباره باز کنید؛ بستن پنجره کافی نیست.

## تنظیمات

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `EXCHANGE_EMAIL` | (الزامی) | آدرس اصلی صندوق |
| `EXCHANGE_USERNAME` | همان ایمیل | نام کاربری ورود: `DOMAIN\user` یا `user@domain` |
| `EXCHANGE_EWS_URL` | autodiscover | آدرس EWS (پیشنهاد می‌شود حتماً تنظیم شود) |
| `EXCHANGE_AUTH` | `ntlm` | `ntlm`، `basic`، `digest` یا `sspi` |
| `EXCHANGE_PASSWORD` | Credential Manager | فقط اگر نمی‌خواهید از Credential Manager استفاده کنید |
| `EXCHANGE_USE_SYSTEM_CERTS` | `true` | اعتماد به گواهی‌های مورد اعتماد ویندوز/مک (CA داخلی سازمان) |
| `EXCHANGE_CA_BUNDLE` | ندارد | فایل PEM گواهی CA داخلی، اگر سیستم به آن اعتماد ندارد |
| `EXCHANGE_VERIFY_SSL` | `true` | با `false` بررسی گواهی خاموش می‌شود (فقط آخرین راه) |
| `EXCHANGE_TIMEZONE` | منطقه‌ی زمانی سیستم | مثلاً `Asia/Tehran` |
| `EXCHANGE_ALLOW_SEND` | `false` | ابزارهای ارسال و دعوت‌نامه‌ی جلسه را فعال می‌کند |
| `EXCHANGE_DOWNLOAD_DIR` | `~/Downloads/exchange-mcp` | محل ذخیره‌ی پیوست‌ها |
| `EXCHANGE_LOG_LEVEL` | `WARNING` | سطح لاگ (در Claude Desktop داخل `mcp-server-exchange.log`) |

## امنیت

- ایمیل‌ها فقط بین این کامپیوتر و سرور Exchange جابه‌جا می‌شوند.
- رمز در Windows Credential Manager یا Keychain مک ذخیره می‌شود. در نسخه‌ی افزونه، Claude Desktop خودش آن را امن نگه می‌دارد.
- ارسال به‌طور پیش‌فرض خاموش است و Claude فقط پیش‌نویس می‌سازد. پیش‌نویس‌ها در پوشه‌ی Drafts می‌مانند تا خودتان از Outlook بفرستید.
- Claude Desktop پیش از اجرای هر ابزار اجازه می‌گیرد، مگر اینکه برای آن ابزار اجازه‌ی دائمی داده باشید.

## پیش‌نیازهای سمت Exchange

- EWS برای کاربر فعال باشد (در Exchange داخلی به‌طور پیش‌فرض فعال است): `Get-CASMailbox you | fl EwsEnabled`
- ورود NTLM یا Basic روی مسیر `/EWS` مجاز باشد (پیش‌فرض Exchange همین است).
- جستجوی متن آزاد (`query`) به سالم بودن ایندکس جستجوی Exchange وابسته است. فیلترهای ساخت‌یافته بدون ایندکس هم کار می‌کنند.

## محدودیت‌ها

- پاسخ دادن به دعوت‌نامه‌ی جلسه (قبول، رد یا شاید) هنوز پشتیبانی نمی‌شود. دعوت‌نامه‌ها خوانده می‌شوند و در تقویم به‌صورت «موقت» دیده می‌شوند.
- جستجو در همه‌ی پوشه‌ها (`folder: "all"`) برای هر پوشه یک درخواست جدا می‌فرستد و در صندوق‌های پرپوشه کندتر است.

## توسعه

```bash
pip install -e ".[dev,build]"
pytest                        # تست‌ها روی یک سرور EWS ساختگی اجرا می‌شوند
python packaging/build.py     # فایل اجرایی و .mcpb برای همین سیستم در dist/
```

با push کردن تگ `v*`، GitHub Actions نسخه‌های ویندوز، مک اینتل و مک Apple را می‌سازد، تست‌ها را روی خود فایل اجرایی ساخته‌شده اجرا می‌کند و خروجی‌ها را در Release قرار می‌دهد.
