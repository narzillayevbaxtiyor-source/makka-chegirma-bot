# Makka chegirmalar boti

@Oziqovaqt_bot har kuni Makka vaqti bilan 08:07 va 17:07 da ishga tushadi.
Danube, BinDawood, Panda, Othaim va boshqa do'konlarning yangi aksiyalarini topadi.
DeepSeek narxlarni ajratib, o'zbekchaga o'giradi. Eng katta chegirmalar (25% va undan ko'p)
@tartibliaka kanaliga joylanadi.

## Sozlash (telefondan)
1. Repo → **Settings → Secrets and variables → Actions → New repository secret**
   - `TELEGRAM_BOT_TOKEN` — @BotFather bergan token
   - `DEEPSEEK_API_KEY` — DeepSeek kaliti
2. @Oziqovaqt_bot ni @tartibliaka kanaliga **admin** qilib qo'shing (post joylash huquqi bilan).
3. @Foydaliadmi akkauntidan @Oziqovaqt_bot ga `/start` yozing (hisobotlar shu yerga keladi).
4. **Actions → Chegirma boti → Run workflow** → `dry_run` belgilangan holda ishga tushiring.
   Postlar avval faqat @Foydaliadmi ga keladi. Yoqsa, belgisiz ishga tushiring.

## O'zgartirish — `config.json`
- `min_discount_percent` — eng kam chegirma foizi
- `max_posts_per_run` — bir ishga tushishda nechta post chiqadi
- `extra_pages` — qo'shimcha aksiya sahifalari havolalari
- `stores` — qaysi do'konlar kuzatiladi
