import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page, Response
from config import SESSION_DIR, SCREENSHOTS_DIR, HEADLESS


class UzumBrowserManager:
    @staticmethod
    def fetch_product_quick(product_id_or_url: str) -> dict | None:
        import urllib.request
        import json
        import re

        m = re.search(r"product/.*?(\d+)", str(product_id_or_url))
        if not m:
            m = re.search(r"product/(\d+)", str(product_id_or_url))
        p_id = m.group(1) if m else str(product_id_or_url)

        api_url = f"https://api.uzum.uz/api/v2/product/{p_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "uz-UZ,uz;q=0.9,ru;q=0.8"
        }
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    payload = data.get("payload", {}).get("data", data)
                    
                    # Bosh rasm
                    photos = payload.get("photos", [])
                    photo_url = None
                    if photos:
                        p_obj = photos[0].get("photo", {})
                        photo_url = p_obj.get("high") or p_obj.get("low")

                    title = payload.get("title", "Uzum Mahsuloti")
                    sku_list = payload.get("skuList", [])
                    
                    variants = []
                    for s in sku_list:
                        s_id = str(s.get("id"))
                        p_price = int(s.get("purchasePrice") or 0)
                        f_price = int(s.get("fullPrice") or p_price)
                        avail = int(s.get("availableAmount") or 0)
                        
                        # Variant tavsifi (metr, rang, razmer)
                        chars = s.get("characteristics", [])
                        char_desc = " / ".join([c.get("valueTitle", "") for c in chars if c.get("valueTitle")])
                        if not char_desc:
                            char_desc = f"Variant #{s_id[-4:]}"

                        variants.append({
                            "sku_id": s_id,
                            "name": char_desc,
                            "price": p_price,
                            "full_price": f_price,
                            "available": avail
                        })

                    return {
                        "id": p_id,
                        "title": title,
                        "photo_url": photo_url,
                        "variants": variants
                    }
        except Exception as e:
            print(f"[fetch_product_quick] API xatosi: {e}")
        return None

    def __init__(self):
        self.playwright = None
        self.context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def start(self, headless: bool = HEADLESS):
        if self.context is not None:
            return
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=headless,
            viewport={"width": 1366, "height": 820},
            locale="uz-UZ",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

    async def stop(self):
        if self.context:
            await self.context.close()
            self.context = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    def _extract_sku_from_json(self, data: dict, target_sku_id: str | None = None) -> dict | None:
        '''
        Uzum API v2 yoki GraphQL JSON javobidan title, purchasePrice va availableAmount ni ajratib oladi.
        '''
        payload = data.get("payload", {})
        product_obj = payload.get("data", data.get("data", data))
        if not isinstance(product_obj, dict):
            return None

        sku_list = product_obj.get("skuList")
        if not isinstance(sku_list, list) or not sku_list:
            return None

        title = product_obj.get("title", "Uzum Mahsuloti")
        chosen_sku = None

        if target_sku_id:
            for sku in sku_list:
                if str(sku.get("id")) == str(target_sku_id):
                    chosen_sku = sku
                    break

        if not chosen_sku:
            # Omborda qoldig'i bor eng arzon SKU ni tanlash
            in_stock_skus = [s for s in sku_list if int(s.get("availableAmount", 0)) > 0]
            if in_stock_skus:
                chosen_sku = min(
                    in_stock_skus,
                    key=lambda s: int(s.get("purchasePrice") or s.get("fullPrice") or 999999999)
                )
            else:
                chosen_sku = sku_list[0]

        price = int(chosen_sku.get("purchasePrice") or chosen_sku.get("fullPrice") or 0)
        available = int(chosen_sku.get("availableAmount") or 0)

        if price <= 0:
            return None

        return {
            "title": title,
            "price": price,
            "available_amount": available,
            "sku_id": chosen_sku.get("id"),
        }

    async def check_price_and_stock(self, product_url: str) -> dict | None:
        '''
        Sahifani ochib page.on("response") orqali Uzum ichki JSON API javobidan
        aniq narx va ombordagi qoldiqni (availableAmount) o'qiydi.
        '''
        async with self._lock:
            await self.start(headless=HEADLESS)
            page = await self.context.new_page()
            captured_result = None

            # URL ichida ?skuId= bo'lsa ajratib olamiz
            sku_match = re.search(r"skuId=(\d+)", product_url)
            target_sku_id = sku_match.group(1) if sku_match else None

            async def on_response(response: Response):
                nonlocal captured_result
                if captured_result is not None:
                    return
                ctype = response.headers.get("content-type", "")
                if "application/json" not in ctype:
                    return
                url = response.url
                if any(k in url for k in ["api.uzum.uz", "/product/", "graphql", "v2/product"]):
                    try:
                        raw = await response.json()
                        parsed = self._extract_sku_from_json(raw, target_sku_id)
                        if parsed:
                            captured_result = parsed
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=45000)
                # JSON API javobi kelishini maksimal 8 soniya kutamiz
                for _ in range(16):
                    if captured_result is not None:
                        break
                    await asyncio.sleep(0.5)

                # Fallback: Agar kesh tufayli XHR tutib bo'lmasa, DOM/window state dan o'qiymiz
                if captured_result is None:
                    captured_result = await self._fallback_dom_extract(page)

                return captured_result
            except Exception as e:
                print(f"[check_price_and_stock] Xatolik: {e}")
                return None
            finally:
                await page.close()

    async def _fallback_dom_extract(self, page: Page) -> dict | None:
        try:
            title_el = page.locator("h1").first
            title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else "Uzum Mahsuloti"

            price_selectors = [
                "[data-test-id='text__product-price']",
                ".currency",
                "[class*='price']",
            ]
            price_val = 0
            for sel in price_selectors:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    txt = await loc.inner_text()
                    digits = re.sub(r"[^\d]", "", txt)
                    if digits:
                        price_val = int(digits)
                        break

            # Savatga qo'shish tugmasi faol bo'lsa qoldiq > 0 deb olamiz
            add_btn = page.locator(
                "button[data-test-id='button__add-cart'], "
                "button:has-text('Savatga'), "
                "button:has-text('В корзину')"
            ).first
            available = 1 if await add_btn.count() > 0 and await add_btn.is_enabled() else 0

            if price_val > 0:
                return {
                    "title": title,
                    "price": price_val,
                    "available_amount": available,
                    "sku_id": None,
                }
        except Exception:
            pass
        return None

    async def auto_checkout(self, product_url: str, product_id: int) -> dict:
        '''
        Mode A va Mode B uchun Auto-Checkout:
        1. Mahsulot sahifasini ochadi va savatga qo'shadi.
        2. Savat va Rasmiylashtirish (Checkout) sahifasiga o'tadi.
        3. PVZ (Topshirish punkti) va 'Topshirish punktida to'lash' (Оплата при получении) usulini tanlaydi.
        4. Buyurtmani tasdiqlaydi va yakuniy skrinshotni saqlaydi.
        '''
        async with self._lock:
            await self.start(headless=HEADLESS)
            page = await self.context.new_page()
            screenshot_path = SCREENSHOTS_DIR / f"order_{product_id}_{int(time.time())}.png"

            try:
                await page.goto(product_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(1.5)

                # 1. Savatga qo'shish tugmasini bosish
                add_to_cart_selectors = [
                    "button[data-test-id='button__add-cart']",
                    "button[data-test-id='button__add-to-cart']",
                    "button:has-text('Savatga qoʻshish')",
                    "button:has-text('Savatga')",
                    "button:has-text('В корзину')",
                ]
                added = False
                for sel in add_to_cart_selectors:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click()
                        added = True
                        break

                if not added:
                    # Agar allaqachon savatda bo'lsa ("Savatda" / "O'tish" tugmasi chiqib tursa)
                    in_cart_loc = page.locator("a[href*='/cart'], button:has-text('Savatga oʻtish'), button:has-text('Перейти в корзину')").first
                    if await in_cart_loc.count() == 0:
                        await page.screenshot(path=str(screenshot_path), full_page=True)
                        return {
                            "success": False,
                            "error": "Savatga qo'shish tugmasi topilmadi yoki tovar tugagan.",
                            "screenshot": str(screenshot_path),
                        }

                await asyncio.sleep(1.5)

                # 2. Savatga o'tish va Rasmiylashtirish (Checkout) bosqichi
                await page.goto("https://uzum.uz/uz/cart", wait_until="networkidle", timeout=45000)
                await asyncio.sleep(2.0)

                checkout_btn_selectors = [
                    "button[data-test-id='button__checkout']",
                    "button[data-test-id='cart__checkout-btn']",
                    "button:has-text('Rasmiylashtirishga oʻtish')",
                    "button:has-text('Rasmiylashtirish')",
                    "button:has-text('Перейти к оформлению')",
                    "button:has-text('К оформлению')",
                ]
                for sel in checkout_btn_selectors:
                    btn = page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        break

                await asyncio.sleep(3.0)

                # 3. "Topshirish punktida to'lash" (Оплата при получении) usulini tanlash
                pay_on_delivery_selectors = [
                    "[data-test-id*='payment-upon-receipt']",
                    "[data-test-id*='cash']",
                    "label:has-text('Topshirish punktida')",
                    "div:has-text('Topshirish punktida toʻlash')",
                    "span:has-text('Topshirish punktida')",
                    "label:has-text('При получении')",
                    "span:has-text('Оплата при получении')",
                ]
                for sel in pay_on_delivery_selectors:
                    pay_el = page.locator(sel).first
                    if await pay_el.count() > 0 and await pay_el.is_visible():
                        try:
                            await pay_el.click()
                            await asyncio.sleep(1.0)
                            break
                        except Exception:
                            continue

                # 4. Yakuniy "Buyurtma berish" / "To'lov qilish" tugmasini bosish
                place_order_selectors = [
                    "button[data-test-id='button__make-order']",
                    "button[data-test-id='checkout__submit']",
                    "button:has-text('Buyurtmani rasmiylashtirish')",
                    "button:has-text('Buyurtma berish')",
                    "button:has-text('Оформить заказ')",
                    "button:has-text('Заказать')",
                ]
                order_clicked = False
                for sel in place_order_selectors:
                    order_btn = page.locator(sel).first
                    if await order_btn.count() > 0 and await order_btn.is_visible() and await order_btn.is_enabled():
                        await order_btn.click()
                        order_clicked = True
                        break

                await asyncio.sleep(4.0)
                await page.screenshot(path=str(screenshot_path), full_page=True)

                if not order_clicked:
                    return {
                        "success": False,
                        "error": "Checkout sahifasida yakuniy tasdiqlash tugmasi bosilmadi (PVZ yoki Login holatini skrinshotdan tekshiring).",
                        "screenshot": str(screenshot_path),
                    }

                return {
                    "success": True,
                    "error": None,
                    "screenshot": str(screenshot_path),
                }

            except Exception as e:
                try:
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                except Exception:
                    pass
                return {
                    "success": False,
                    "error": str(e),
                    "screenshot": str(screenshot_path) if screenshot_path.exists() else None,
                }
            finally:
                await page.close()


async def interactive_login():
    '''
    Birinchi marta ishga tushirganda (python browser_manager.py --login)
    foydalanuvchi Uzum profiliga kirishi va PVZ tanlashi uchun oyna ochadi.
    '''
    print("=" * 65)
    print("[UZUM SESSION SETUP] Brauzer ochilmoqda (headless=False)...")
    print("1. Telefon raqamingiz va SMS kod orqali Uzum profilingizga kiring.")
    print("2. Asosiy manzil sifatida o'zingizga yaqin Topshirish punktini (PVZ) tanlang.")
    print("3. Tayyor bo'lgach, shu terminalda ENTER tugmasini bosing.")
    print("=" * 65)

    manager = UzumBrowserManager()
    await manager.start(headless=False)
    page = await manager.context.new_page()
    await page.goto("https://uzum.uz", wait_until="domcontentloaded")

    await asyncio.to_thread(input, "\n>>> Login va PVZ tanlab bo'lgach ENTER bosing... ")
    await manager.stop()
    print("[+] Sessiya './uzum_session' papkasiga muvaffaqiyatli saqlandi!")


if __name__ == "__main__":
    asyncio.run(interactive_login())
