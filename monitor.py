import asyncio
import random
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from config import MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL
from database import (
    get_active_products,
    update_product_metrics,
    acquire_safety_lock,
    mark_status,
)
from browser_manager import UzumBrowserManager


class PriceMonitor:
    def __init__(self, bot: Bot, browser_manager: UzumBrowserManager):
        self.bot = bot
        self.browser = browser_manager
        self.running = False

    async def start_loop(self):
        self.running = True
        print("[Monitor] Narx kuzatuvchisi ishga tushdi (Anti-Ban: 45-90s)...")

        while self.running:
            products = await get_active_products()
            if not products:
                await asyncio.sleep(15)
                continue

            for item in products:
                if not self.running:
                    break

                product_id = item["id"]
                user_id = item["user_id"]
                url = item["url"]
                target_price = item["target_price"]
                mode = item["mode"]

                info = await self.browser.check_price_and_stock(url)
                if info:
                    title = info["title"]
                    current_price = info["price"]
                    available = info["available_amount"]

                    await update_product_metrics(product_id, title, current_price, available)
                    print(
                        f"[Monitor] ID #{product_id} | {title[:30]} | "
                        f"Narx: {current_price:,} so'm (Maqsad: {target_price:,}) | Qoldiq: {available}"
                    )

                    # Narx maqsadli narxga tushgan va omborda mavjud bo'lsa
                    if current_price <= target_price and available > 0:
                        if mode == "MODE_A":
                            # Mode A: Auto-Buy Sniper (Darhol xarid qilish va Safety Lock)
                            locked = await acquire_safety_lock(product_id)
                            if locked:
                                await self.bot.send_message(
                                    user_id,
                                    f"🎯 <b>SNIPER TRIGGER (Mode A)!</b>\n\n"
                                    f"📦 <b>{title}</b>\n"
                                    f"💰 Joriy narx: <b>{current_price:,} so'm</b> (Maqsad: {target_price:,} so'm)\n"
                                    f"📦 Omborda: <b>{available} ta</b>\n\n"
                                    f"⚡ Avtomatik xarid boshlandi...",
                                    parse_mode="HTML",
                                )
                                result = await self.browser.auto_checkout(url, product_id)
                                if result["success"]:
                                    await mark_status(product_id, "COMPLETED")
                                    caption = (
                                        f"✅ <b>XARID MUVAFFAQIYATLI YAKUNLANDI!</b>\n\n"
                                        f"📦 Tovar: <b>{title}</b>\n"
                                        f"💵 Xarid narxi: <b>{current_price:,} so'm</b>\n"
                                        f"🔒 Status: <b>COMPLETED</b> (Safety Lock faollashdi, kuzatuv to'xtatildi)"
                                    )
                                else:
                                    await mark_status(product_id, "FAILED")
                                    caption = (
                                        f"⚠️ <b>Avto-xaridda xatolik yuz berdi!</b>\n\n"
                                        f"📦 Tovar: <b>{title}</b>\n"
                                        f"❌ Sabab: {result['error']}\n"
                                        f"🔒 Qayta xarid bo'lmasligi uchun kuzatuv to'xtatildi."
                                    )

                                if result.get("screenshot"):
                                    photo = FSInputFile(result["screenshot"])
                                    await self.bot.send_photo(user_id, photo=photo, caption=caption, parse_mode="HTML")
                                else:
                                    await self.bot.send_message(user_id, caption, parse_mode="HTML")

                        elif mode == "MODE_B":
                            # Mode B: 1-Click Confirm (Xabar va [Sotib olish] inline tugmasi)
                            await mark_status(product_id, "NOTIFIED")
                            kb = InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text="🛒 Sotib olish (1-Click PVZ)",
                                            callback_data=f"buy_now:{product_id}",
                                        )
                                    ],
                                    [
                                        InlineKeyboardButton(
                                            text="❌ Bekor qilish",
                                            callback_data=f"delete:{product_id}",
                                        )
                                    ],
                                ]
                            )
                            await self.bot.send_message(
                                user_id,
                                f"🔔 <b>NARX TUSHDI (Mode B)!</b>\n\n"
                                f"📦 <b>{title}</b>\n"
                                f"💰 Yangi narx: <b>{current_price:,} so'm</b> (Maqsad: {target_price:,} so'm)\n"
                                f"📦 Omborda qoldiq: <b>{available} ta</b>\n\n"
                                f"Darhol PVZga buyurtma berish uchun quyidagi tugmani bosing:",
                                reply_markup=kb,
                                parse_mode="HTML",
                            )

                # Anti-Ban: Har bir tovar orasida 45-90 soniya random kechikish
                delay = random.randint(MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL)
                print(f"[Anti-Ban] Keyingi tekshiruvgacha {delay} soniya kutilmoqda...")
                await asyncio.sleep(delay)
