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
    def _parse_uzum_payload(data: dict, fallback_id: str = "") -> dict | None:
        payload = data.get("payload", {})
        product_obj = payload.get("data", data.get("data", data))
        if not isinstance(product_obj, dict):
            return None

        title = product_obj.get("title")
        sku_list = product_obj.get("skuList")
        if not title or not isinstance(sku_list, list) or not sku_list:
            return None

        # 1. Bosh rasmni har qanday chuqurlikdan (800/720/540/high/low) topish
        photo_url = None
        photos = product_obj.get("photos", [])
        if isinstance(photos, list) and photos:
            first_p = photos[0]
            if isinstance(first_p, dict):
                p_inner = first_p.get("photo", first_p)
                if isinstance(p_inner, dict):
                    if isinstance(p_inner.get("high"), str):
                        photo_url = p_inner["high"]
                    else:
                        for res_key in ("800", "720", "540", "480", "240"):
                            res_obj = p_inner.get(res_key)
                            if isinstance(res_obj, dict) and res_obj.get("high"):
                                photo_url = res_obj["high"]
                                break
                        if not photo_url:
                            for v in p_inner.values():
                                if isinstance(v, dict) and (v.get("high") or v.get("low")):
                                    photo_url = v.get("high") or v.get("low")
                                    break

        # 2. Xarakteristikalar (Rang, Razmer, Metr) xaritasini tuzish
        char_groups = product_obj.get("characteristics", [])
        variants = []

        for idx, s in enumerate(sku_list):
            s_id = str(s.get("id", ""))
            p_price = int(s.get("purchasePrice") or s.get("fullPrice") or 0)
            f_price = int(s.get("fullPrice") or p_price)
            avail = int(s.get("availableAmount") or 0)

            char_parts = []
            sku_chars = s.get("characteristics", [])
            if isinstance(sku_chars, list):
                for sc in sku_chars:
                    if not isinstance(sc, dict):
                        continue
                    # Format A: charId va valueIndex indekslari orqali
                    c_idx = sc.get("charId")
                    v_idx = sc.get("valueIndex")
                    if c_idx is not None and v_idx is not None and isinstance(char_groups, list):
                        if 0 <= int(c_idx) < len(char_groups):
                            grp = char_groups[int(c_idx)]
                            g_title = grp.get("title", "")
                            vals = grp.get("values", [])
                            if isinstance(vals, list) and 0 <= int(v_idx) < len(vals):
                                v_title = vals[int(v_idx)].get("title", "")
                                if v_title:
                                    char_parts.append(f"{g_title}: {v_title}" if g_title else v_title)
                    # Format B: to'g'ridan-to'g'ri valueTitle bo'lsa
                    elif sc.get("valueTitle") or sc.get("title"):
                        char_parts.append(str(sc.get("valueTitle") or sc.get("title")))

            char_desc = " / ".join(char_parts) if char_parts else (f"Variant #{idx + 1}" if len(sku_list) > 1 else "Standart")

            variants.append({
                "sku_id": s_id,
                "name": char_desc,
                "price": p_price,
                "full_price": f_price,
                "available": avail,
            })

        return {
            "id": str(product_obj.get("id") or fallback_id),
            "title": title,
            "photo_url": photo_url,
            "variants": variants,
        }

    @classmethod
    def fetch_product_quick(cls, product_id_or_url: str) -> dict | None:
        import urllib.request
        import ssl
        import gzip
        import uuid
        import json
        import re

        raw_url = str(product_id_or_url).strip()
        base_path = raw_url.split("?")[0].rstrip("/")
        m = re.search(r"(\d+)$", base_path)
        if not m:
            m = re.search(r"product/.*?(\d+)", base_path)
        p_id = m.group(1) if m else base_path

        sku_match = re.search(r"skuId=(\d+)", raw_url)
        url_sku_id = sku_match.group(1) if sku_match else "0"

        ctx = ssl._create_unverified_context()
        api_urls = [
            f"https://api.uzum.uz/api/v2/product/{p_id}",
            f"https://api.uzum.uz/api/v2/product/{p_id}?skuId={url_sku_id}",
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "uz-UZ,uz;q=0.9,ru-RU;q=0.8,ru;q=0.7",
            "Origin": "https://uzum.uz",
            "Referer": "https://uzum.uz/",
            "x-iid": str(uuid.uuid4()),
        }

        for api_url in api_urls:
            try:
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
                    raw_bytes = response.read()
                    if response.headers.get("Content-Encoding") == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
                        raw_bytes = gzip.decompress(raw_bytes)
                    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
                    parsed = cls._parse_uzum_payload(data, p_id)
                    if parsed and parsed.get("variants"):
                        return parsed
            except Exception as e:
                print(f"[fetch_product_quick] API urinish xatosi ({api_url}): {e}")

        # 2-QATLAM: HTML sahifadan OpenGraph (og:title, og:image) o'qish
        try:
            page_req = urllib.request.Request(raw_url, headers=headers)
            with urllib.request.urlopen(page_req, timeout=10, context=ctx) as resp:
                html_bytes = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or html_bytes[:2] == b"\x1f\x8b":
                    html_bytes = gzip.decompress(html_bytes)
                html = html_bytes.decode("utf-8", errors="ignore")

                og_title = re.search(r'property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html)
                og_image = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)
                price_m = re.search(r'"purchasePrice"\s*:\s*(\d+)', html)
                full_m = re.search(r'"fullPrice"\s*:\s*(\d+)', html)

                title = og_title.group(1) if og_title else f"Uzum Tovar #{p_id}"
                photo = og_image.group(1) if og_image else None
                p_val = int(price_m.group(1)) if price_m else 0
                f_val = int(full_m.group(1)) if full_m else p_val

                if p_val > 0 or og_title:
                    return {
                        "id": p_id,
                        "title": title,
                        "photo_url": photo,
                        "variants": [{
                            "sku_id": url_sku_id if url_sku_id != "0" else p_id,
                            "name": f"SKU #{url_sku_id}" if url_sku_id != "0" else "Asosiy variant",
                            "price": p_val,
                            "full_price": f_val,
                            "available": 1,
                        }],
                    }
        except Exception as e:
            print(f"[fetch_product_quick] HTML fallback xatosi: {e}")

        return None

    async def fetch_product_with_playwright(self, product_url: str) -> dict | None:
        # 1. Avval tezkor API ni sinab ko'ramiz
        quick = await asyncio.to_thread(self.fetch_product_quick, product_url)
        if quick and quick.get("variants") and quick["variants"][0].get("price", 0) > 0:
            return quick

        # 2. Agar API bloklansa, Playwright brauzeri orqali JSON va DOM dan olamiz
        async with self._lock:
            await self.start(headless=HEADLESS)
            page = await self.context.new_page()
            captured_parsed = None

            base_path = product_url.split("?")[0].rstrip("/")
            m = re.search(r"(\d+)$", base_path)
            p_id = m.group(1) if m else "0"
            sku_match = re.search(r"skuId=(\d+)", product_url)
            url_sku_id = sku_match.group(1) if sku_match else p_id

            async def on_resp(response: Response):
                nonlocal captured_parsed
                if captured_parsed is not None:
                    return
                if "application/json" not in response.headers.get("content-type", ""):
                    return
                if any(k in response.url for k in ["api.uzum.uz", "/product/", "v2/product"]):
                    try:
                        raw = await response.json()
                        parsed = self._parse_uzum_payload(raw, p_id)
                        if parsed and parsed.get("variants"):
                            captured_parsed = parsed
                    except Exception:
                        pass

            page.on("response", on_resp)
            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=45000)
                for _ in range(16):
                    if captured_parsed is not None:
                        break
                    await asyncio.sleep(0.5)

                if captured_parsed:
                    return captured_parsed

                # 3. Yakuniy DOM Fallback (hech qachon xatolik bermasligi uchun)
                dom_info = await self._fallback_dom_extract(page)
                og_img = None
                try:
                    og_el = page.locator("meta[property='og:image']").first
                    if await og_el.count() > 0:
                        og_img = await og_el.get_attribute("content")
                except Exception:
                    pass

                price_val = dom_info["price"] if dom_info else 0
                title_val = dom_info["title"] if dom_info else (quick["title"] if quick else f"Uzum Tovar #{p_id}")

                return {
                    "id": p_id,
                    "title": title_val,
                    "photo_url": og_img or (quick["photo_url"] if quick else None),
                    "variants": [{
                        "sku_id": url_sku_id,
                        "name": f"SKU #{url_sku_id}" if url_sku_id != p_id else "Tanlangan variant",
                        "price": price_val,
                        "full_price": price_val,
                        "available": dom_info["available_amount"] if dom_info else 1,
                    }],
                }
            except Exception as e:
                print(f"[fetch_product_with_playwright] Xatolik: {e}")
                # Eng oxirgi zaxira: havolani baribir qabul qilish
                return {
                    "id": p_id,
                    "title": f"Uzum Mahsuloti #{p_id}",
                    "photo_url": None,
                    "variants": [{
                        "sku_id": url_sku_id,
                        "name": f"SKU #{url_sku_id}",
                        "price": 0,
                        "full_price": 0,
                        "available": 1,
                    }],
                }
            finally:
                await page.close()

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
