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
    waiting_for_sku = State()
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
        "🚀 <b>Uzum Market Price Tracker & Auto-Buy Sniper</b>\n\n"
        "📌 <b>Qanday ishlaydi:</b>\n"
        "1. Uzum ilovasi yoki saytidan mahsulot havolasini yuboring.\n"
        "2. Bot rasm, aksiya narxi va barcha variantlarni (metr, rang, razmer) chiqaradi.\n"
        "3. Kerakli variantni tanlang va maqsadli narxni belgilang.\n"
        "4. Narx tushishi bilan bot avtomatik yoki 1-bosishda PVZga buyurtma beradi!\n\n"
        "📋 <b>Buyruqlar:</b>\n"
        "/list — Kuzatilayotgan tovarlar",
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
        mode_label = "⚡ Auto-Buy (Mode A)" if item["mode"] == "MODE_A" else "🔔 1-Click (Mode B)"
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
        msg_text = (
            f"🆔 <b>#{item['id']} | {item['title']}</b>\n"
            f"🎯 Maqsadli narx: <b>{item['target_price']:,} so'm</b>\n"
            f"💰 Oxirgi narx: <b>{last_p}</b> (Qoldiq: {item['available_amount']} ta)\n"
            f"⚙️ Rejim: <b>{mode_label}</b>\n"
            f"📌 Status: <b>{item['status']}</b>\n"
            f"🔗 <a href='{item['url']}'>Uzum sahifasi</a>"
        )
        await message.answer(msg_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(F.text.contains("uzum.uz"))
async def handle_uzum_link(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return

    url_match = re.search(r"(https?://[^\s]*uzum\.uz/[^\s]+)", message.text)
    if not url_match:
        await message.answer("⚠️ To'g'ri Uzum.uz mahsulot havolasini yuboring.")
        return

    full_url = url_match.group(1)
    status_msg = await message.answer("🔍 Mahsulot ma'lumotlari va rasmlari yuklanmoqda...")

    # Tezkor API orqali tovar ma'lumotlarini olish
    p_info = await asyncio.to_thread(browser_manager.fetch_product_quick, full_url)
    await status_msg.delete()

    if not p_info or not p_info.get("variants"):
        await message.answer("⚠️ Mahsulot ma'lumotlarini yuklab bo'lmadi. Havolani tekshiring.")
        return

    title = p_info["title"]
    photo_url = p_info["photo_url"]
    variants = p_info["variants"]

    # Havola ichida skuId bormi?
    sku_match = re.search(r"skuId=(\d+)", full_url)
    target_sku_id = sku_match.group(1) if sku_match else None

    # Agar skuId havola ichida allaqachon bo'lsa, to'g'ridan-to'g'ri o'shani tanlash
    selected_variant = None
    if target_sku_id:
        for v in variants:
            if v["sku_id"] == target_sku_id:
                selected_variant = v
                break

    await state.update_data(
        url=full_url,
        title=title,
        photo_url=photo_url,
        all_variants=variants
    )

    if selected_variant:
        # Variant aniq ma'lum
        await state.update_data(selected_sku=selected_variant)
        await ask_price_prompt(message, state, selected_variant, title, photo_url)
    elif len(variants) == 1:
        # Faqat 1 ta variant bor
        selected_variant = variants[0]
        await state.update_data(selected_sku=selected_variant)
        await ask_price_prompt(message, state, selected_variant, title, photo_url)
    else:
        # Bir nechta variantlar bor — Inline tugmalar orqali tanlash
        await state.set_state(AddProductFSM.waiting_for_sku)
        kb_rows = []
        for v in variants[:12]: # Maksimal 12 tagacha tugma
            v_name = v["name"]
            v_price = f"{v['price']:,} so'm"
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"🔘 {v_name} — {v_price}",
                    callback_data=f"pick_sku:{v['sku_id']}"
                )
            ])
        
        reply_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        caption = (
            f"📦 <b>{title}</b>\n\n"
            f"❗️ <b>Ushbu tovarda bir nechta variantlar mavjud.</b>\n"
            f"Qaysi birini kuzatmoqchisiz? Quyidagi tugmalardan birini tanlang:"
        )
        if photo_url:
            await message.answer_photo(photo=photo_url, caption=caption, reply_markup=reply_kb, parse_mode="HTML")
        else:
            await message.answer(caption, reply_markup=reply_kb, parse_mode="HTML")


@dp.callback_query(AddProductFSM.waiting_for_sku, F.data.startswith("pick_sku:"))
async def handle_sku_picked(call: CallbackQuery, state: FSMContext):
    sku_id = call.data.split(":")[1]
    data = await state.get_data()
    variants = data.get("all_variants", [])
    title = data.get("title")
    photo_url = data.get("photo_url")

    selected_variant = next((v for v in variants if v["sku_id"] == sku_id), None)
    if not selected_variant:
        await call.answer("Variant topilmadi!", show_alert=True)
        return

    await state.update_data(selected_sku=selected_variant)
    await call.message.delete()
    await ask_price_prompt(call.message, state, selected_variant, title, photo_url)
    await call.answer()


async def ask_price_prompt(message: Message, state: FSMContext, variant: dict, title: str, photo_url: str):
    await state.set_state(AddProductFSM.waiting_for_price)

    p_price = variant["price"]
    f_price = variant["full_price"]
    avail = variant["available"]
    v_name = variant["name"]

    # Chegirma foizini hisoblash
    discount_str = ""
    if f_price > p_price:
        pct = int(round((1 - p_price / f_price) * 100))
        discount_str = f" <i>(-{pct}%)</i>"

    caption = (
        f"📦 <b>{title}</b>\n"
        f"🏷 <b>Tanlangan variant:</b> <code>{v_name}</code>\n\n"
        f"💵 Asl narxi: <s>{f_price:,} so'm</s>\n"
        f"🔥 <b>Hozirgi aksiya narxi:</b> <b>{p_price:,} so'm</b>{discount_str}\n"
        f"📊 Ombordagi qoldiq: <b>{avail} ta</b>\n\n"
        f"💰 <b>Ushbu tovar qancha narxga tushsa avtomatik xarid qilinsin?</b>\n"
        f"<i>Maqsadli narxni faqat raqamlarda kiriting (masalan: <code>{int(p_price * 0.9)}</code>):</i>"
    )

    if photo_url:
        await message.answer_photo(photo=photo_url, caption=caption, parse_mode="HTML")
    else:
        await message.answer(caption, parse_mode="HTML")


@dp.message(AddProductFSM.waiting_for_price)
async def handle_target_price(message: Message, state: FSMContext):
    digits = re.sub(r"[^\d]", "", message.text or "")
    if not digits or int(digits) <= 0:
        await message.answer("⚠️ Iltimos, narxni faqat raqamlarda kiriting (masalan: <code>65000</code>):", parse_mode="HTML")
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
        f"🎯 Maqsadli narx: <b>{target_price:,} so'm</b> qilib belgilandi.\n\n"
        f"Endi buyurtma rejimini tanlang:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(AddProductFSM.waiting_for_mode, F.data.startswith("select_mode:"))
async def handle_mode_selection(call: CallbackQuery, state: FSMContext):
    mode = call.data.split(":")[1]
    data = await state.get_data()
    url = data["url"]
    title = data["title"]
    photo_url = data.get("photo_url")
    target_price = data["target_price"]
    sku = data.get("selected_sku", {})
    sku_id = sku.get("sku_id")
    variant_name = sku.get("name", "")

    product_id = await add_product(
        user_id=call.from_user.id,
        url=url,
        sku_id=sku_id,
        title=f"{title} ({variant_name})" if variant_name else title,
        photo_url=photo_url,
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
        f"📦 Tovar: <b>{title}</b>\n"
        f"🏷 Variant: <code>{variant_name}</code>\n"
        f"🎯 Maqsadli narx: <b>{target_price:,} so'm</b>\n"
        f"⚙️ Rejim: {mode_desc}\n"
        f"⏱ Kuzatish oralig'i: <b>45–90 soniya (Anti-Ban)</b>",
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
        f"Iltimos, kuting, buyurtma tasdiqlanib skrinshot yuboriladi...",
        parse_mode="HTML",
    )

    checkout_url = product["url"]
    if product.get("sku_id") and "skuId=" not in checkout_url:
        sep = "&" if "?" in checkout_url else "?"
        checkout_url = f"{checkout_url}{sep}skuId={product['sku_id']}"

    result = await browser_manager.auto_checkout(checkout_url, product_id)
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
