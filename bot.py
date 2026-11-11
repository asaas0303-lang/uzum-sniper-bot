import asyncio
import re
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import BOT_TOKEN, ADMIN_ID
from database import (
    init_db,
    add_product,
    get_user_products,
    get_product_by_id,
    acquire_safety_lock,
    mark_status,
    delete_product,
)
from browser_manager import UzumBrowserManager
from monitor import PriceMonitor


class AddProductFSM(StatesGroup):
    waiting_for_price = State()
    waiting_for_mode = State()


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
browser_manager = UzumBrowserManager()
monitor = PriceMonitor(bot, browser_manager)


def is_authorized(user_id: int) -> bool:
    if ADMIN_ID == 0:
        return True
    return user_id == ADMIN_ID


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        await message.answer("⛔ Sizda ushbu shaxsiy botdan foydalanish huquqi yo'q.")
        return

    await state.clear()
    await message.answer(
        "🚀 <b>Uzum Market Price Tracker & Auto-Buy Sniper</b> botiga xush kelibsiz!\n\n"
        "📌 <b>Qo'llanma:</b>\n"
        "1. Menga Uzum mahsuloti havolasini (<code>https://uzum.uz/...</code>) yuboring.\n"
        "2. Kutilayotgan maqsadli narxni (<code>target_price</code>) kiriting.\n"
        "3. Rejimni tanlang:\n"
        "   • <b>Mode A (Auto-Buy Sniper)</b> — Narx tushishi bilan avtomatik PVZga xarid qiladi.\n"
        "   • <b>Mode B (1-Click Confirm)</b> — Narx tushganda xabar va <b>[Sotib olish]</b> tugmasini yuboradi.\n\n"
        "📋 <b>Buyruqlar:</b>\n"
        "/list — Kuzatilayotgan tovarlar ro'yxati\n"
        "/login — Serverda brauzer sessiyasi holatini tekshirish",
        parse_mode="HTML",
    )


@dp.message(Command("list"))
async def cmd_list(message: Message):
    if not is_authorized(message.from_user.id):
        return

    items = await get_user_products(message.from_user.id)
    if not items:
        await message.answer("📭 Hozircha kuzatilayotgan tovarlar yo'q. Uzum havolasini yuboring!")
        return

    for item in items:
        last_p = f"{item['last_price']:,} so'm" if item["last_price"] else "Hali tekshirilmadi"
        mode_label = "⚡ Mode A (Auto-Buy)" if item["mode"] == "MODE_A" else "🔔 Mode B (1-Click)"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛒 Hozir sotib olish",
                        callback_data=f"buy_now:{item['id']}",
                    ),
                    InlineKeyboardButton(
                        text="🗑 O'chirish",
                        callback_data=f"delete:{item['id']}",
                    ),
                ]
            ]
        )
        await message.answer(
            f"🆔 <b>#{item['id']} | {item['title']}</b>\n"
            f"🎯 Maqsadli narx: <b>{item['target_price']:,} so'm</b>\n"
            f"💰 Oxirgi narx: <b>{last_p}</b> (Qoldiq: {item['available_amount']} ta)\n"
            f"⚙️ Rejim: <b>{mode_label}</b>\n"
            f"📌 Status: <b>{item['status']}</b>\n"
            f"🔗 <a href='{item['url']}'>Mahsulot havolasi</a>",
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@dp.message(F.text.contains("uzum.uz"))
async def handle_uzum_link(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return

    url_match = re.search(r"(https?://[^\s]*uzum\.uz/[^\s]+)", message.text)
    if not url_match:
        await message.answer("⚠️ To'g'ri Uzum.uz mahsulot havolasini yuboring.")
        return

    clean_url = url_match.group(1)
    await state.update_data(url=clean_url)
    await state.set_state(AddProductFSM.waiting_for_price)
    await message.answer(
        "💰 <b>Maqsadli narxni (target_price) kiriting (so'mda):</b>\n"
        "Masalan: <code>150000</code>",
        parse_mode="HTML",
    )


@dp.message(AddProductFSM.waiting_for_price)
async def handle_target_price(message: Message, state: FSMContext):
    digits = re.sub(r"[^\d]", "", message.text or "")
    if not digits or int(digits) <= 0:
        await message.answer("⚠️ Iltimos, narxni faqat raqamlarda kiriting (masalan: <code>125000</code>):", parse_mode="HTML")
        return

    target_price = int(digits)
    await state.update_data(target_price=target_price)
    await state.set_state(AddProductFSM.waiting_for_mode)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Mode A: Auto-Buy Sniper (Avto-Xarid)",
                    callback_data="select_mode:MODE_A",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Mode B: 1-Click Confirm (Tasdiqlash bilan)",
                    callback_data="select_mode:MODE_B",
                )
            ],
        ]
    )
    await message.answer(
        f"🎯 Maqsadli narx: <b>{target_price:,} so'm</b>\n\n"
        f"Endi xarid rejimini tanlang:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(AddProductFSM.waiting_for_mode, F.data.startswith("select_mode:"))
async def handle_mode_selection(call: CallbackQuery, state: FSMContext):
    mode = call.data.split(":")[1]
    data = await state.get_data()
    url = data["url"]
    target_price = data["target_price"]

    product_id = await add_product(
        user_id=call.from_user.id,
        url=url,
        target_price=target_price,
        mode=mode,
    )
    await state.clear()

    mode_desc = (
        "⚡ <b>Mode A (Auto-Buy Sniper)</b> — Narx tushishi bilan avtomatik PVZga buyurtma beriladi."
        if mode == "MODE_A"
        else "🔔 <b>Mode B (1-Click Confirm)</b> — Narx tushishi bilan sizga [Sotib olish] tugmasi yuboriladi."
    )

    await call.message.edit_text(
        f"✅ <b>Tovar kuzatuvga qo'shildi! (ID: #{product_id})</b>\n\n"
        f"🔗 Havola: {url}\n"
        f"🎯 Maqsadli narx: <b>{target_price:,} so'm</b>\n"
        f"⚙️ Rejim: {mode_desc}\n"
        f"⏱ Tekshirish intervali: <b>45–90 soniya (Anti-Ban)</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await call.answer()


@dp.callback_query(F.data.startswith("buy_now:"))
async def handle_one_click_buy(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])
    product = await get_product_by_id(product_id)

    if not product:
        await call.answer("Tovar topilmadi!", show_alert=True)
        return

    if product["status"] == "COMPLETED":
        await call.answer("🔒 Bu tovar allaqachon xarid qilingan (Safety Lock)!", show_alert=True)
        return

    locked = await acquire_safety_lock(product_id)
    if not locked:
        await call.answer("⏳ Xarid jarayoni allaqachon ketmoqda yoki yakunlangan!", show_alert=True)
        return

    await call.answer("⚡ Auto-Checkout boshlandi!")
    await call.message.edit_text(
        f"⚡ <b>1-Click Auto-Checkout bajarilmoqda...</b>\n"
        f"📦 Tovar: <b>{product['title']}</b>\n"
        f"Iltimos, 10-15 soniya kuting, yakuniy skrinshot yuboriladi.",
        parse_mode="HTML",
    )

    result = await browser_manager.auto_checkout(product["url"], product_id)
    if result["success"]:
        await mark_status(product_id, "COMPLETED")
        caption = (
            f"✅ <b>1-CLICK XARID MUVAFFAQIYATLI YAKUNLANDI!</b>\n\n"
            f"📦 Tovar: <b>{product['title']}</b>\n"
            f"🔒 Status: <b>COMPLETED</b> (Safety Lock faollashdi)"
        )
    else:
        await mark_status(product_id, "FAILED")
        caption = (
            f"⚠️ <b>Xaridda xatolik:</b> {result['error']}\n"
            f"Skrinshotni tekshiring."
        )

    if result.get("screenshot"):
        photo = FSInputFile(result["screenshot"])
        await call.message.answer_photo(photo=photo, caption=caption, parse_mode="HTML")
    else:
        await call.message.answer(caption, parse_mode="HTML")


@dp.callback_query(F.data.startswith("delete:"))
async def handle_delete_product(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])
    deleted = await delete_product(product_id, call.from_user.id)
    if deleted:
        await call.message.edit_text(f"🗑 Tovar #{product_id} kuzatuvdan o'chirildi.")
    else:
        await call.answer("Tovar topilmadi.", show_alert=True)


async def main():
    await init_db()
    asyncio.create_task(monitor.start_loop())
    print("[Bot] Telegram bot ishga tushdi...")
    try:
        await dp.start_polling(bot)
    finally:
        await browser_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
