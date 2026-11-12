import asyncio
import gzip
import json
import re
import ssl
import time
import urllib.request
import uuid
from playwright.async_api import async_playwright, BrowserContext, Page, Response
from config import SESSION_DIR, SCREENSHOTS_DIR, HEADLESS


class UzumBrowserManager:
    def __init__(self):
        self.playwright = None
        self.context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    async def start(self, headless: bool = HEADLESS):
        if self.context is not None:
            try:
                _ = self.context.pages
                return
            except Exception:
                await self.stop()

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
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        self.context = None

        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None

    async def get_safe_page(self) -> Page:
        await self.start(headless=HEADLESS)
        try:
            return await self.context.new_page()
        except Exception:
            await self.stop()
            await self.start(headless=HEADLESS)
            return await self.context.new_page()

    @classmethod
    def _find_product_dict(cls, obj):
        candidates = []

        def _walk(o):
            if isinstance(o, dict):
                if "skuList" in o and isinstance(o["skuList"], list) and len(o["skuList"]) > 0:
                    candidates.append(o)
                for v in o.values():
                    _walk(v)
            elif isinstance(o, list):
                for item in o:
                    _walk(item)

        _walk(obj)
        if not candidates:
            return None
        # Avval narxlari (sellPrice / purchasePrice / fullPrice / priceBlock) mavjud bo'lganini tanlaymiz
        for c in candidates:
            first_sku = c["skuList"][0]
            if isinstance(first_sku, dict) and any(
                k in first_sku for k in ["purchasePrice", "sellPrice", "fullPrice", "priceBlock"]
            ):
                return c
        return candidates[0]

    @classmethod
    def _parse_uzum_payload(cls, data: dict, fallback_id: str = "") -> dict | None:
        if not isinstance(data, dict):
            return None
        product_obj = cls._find_product_dict(data)
        if not isinstance(product_obj, dict):
            return None

        title = product_obj.get("title")
        if not title:
            loc = product_obj.get("localizableTitle")
            if isinstance(loc, dict):
                title = loc.get("titleUz") or loc.get("titleRu")

        sku_list = product_obj.get("skuList")
        if not isinstance(sku_list, list) or not sku_list:
            return None

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
            elif isinstance(first_p, str) and first_p.startswith("http"):
                photo_url = first_p

        # Agar bosh rasm topilmasa, SKU ichidagi rasmni olish
        if not photo_url:
            for s in sku_list:
                if isinstance(s, dict) and isinstance(s.get("photo"), dict):
                    p_link = s["photo"].get("link")
                    if isinstance(p_link, dict):
                        photo_url = p_link.get("high") or p_link.get("low")
                        if photo_url:
                            break
                    elif s["photo"].get("key"):
                        photo_url = f"https://images.uzum.uz/{s['photo']['key']}/original.jpg"
                        break

        if photo_url and "_low.jpg" in photo_url:
            photo_url = photo_url.replace("_low.jpg", "_high.jpg").replace("240", "540")

        char_groups = product_obj.get("characteristics", [])
        variants = []

        for idx, s in enumerate(sku_list):
            if not isinstance(s, dict):
                continue
            s_id = str(s.get("id", ""))
            p_price = int(
                s.get("purchasePrice")
                or s.get("sellPrice")
                or (s.get("priceBlock", {}).get("sellPrice", {}).get("amount") if isinstance(s.get("priceBlock"), dict) else 0)
                or s.get("fullPrice")
                or 0
            )
            f_price = int(
                s.get("fullPrice")
                or (s.get("priceBlock", {}).get("fullPrice", {}).get("amount") if isinstance(s.get("priceBlock"), dict) else 0)
                or p_price
            )
            avail = int(s.get("availableAmount") or 0)

            char_parts = []
            sku_chars = s.get("characteristics", [])
            if isinstance(sku_chars, list):
                for sc in sku_chars:
                    if not isinstance(sc, dict):
                        continue
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
                    elif sc.get("valueTitle") or sc.get("title"):
                        char_parts.append(str(sc.get("valueTitle") or sc.get("title")))

            sku_title = s.get("skuTitle") or s.get("title")
            char_desc = " / ".join(char_parts) if char_parts else (sku_title if sku_title else (f"Variant #{idx + 1}" if len(sku_list) > 1 else "Standart"))

            variants.append({
                "sku_id": s_id,
                "name": char_desc,
                "price": p_price,
                "full_price": f_price,
                "available": avail,
            })

        if not variants:
            return None

        return {
            "id": str(product_obj.get("id") or fallback_id),
            "title": title or f"Uzum Mahsuloti #{fallback_id}",
            "photo_url": photo_url,
            "variants": variants,
        }

    @classmethod
    def fetch_product_quick(cls, product_id_or_url: str) -> dict | None:
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
        base_path = product_url.split("?")[0].rstrip("/")
        m = re.search(r"(\d+)$", base_path)
        if not m:
            m = re.search(r"product/.*?(\d+)", base_path)
        p_id = m.group(1) if m else "0"

        sku_match = re.search(r"skuId=(\d+)", product_url)
        url_sku_id = sku_match.group(1) if sku_match else p_id

        # 1. Avval tezkor API ni sinab ko'ramiz
        quick = await asyncio.to_thread(self.fetch_product_quick, product_url)
        if quick and quick.get("variants") and quick["variants"][0].get("price", 0) > 0 and not quick["title"].startswith("Uzum Tovar #"):
            return quick

        # 2. Playwright orqali sahifani ochish va ichki API + DOM dan o'qish
        async with self._lock:
            page = await self.get_safe_page()
            captured_parsed = None

            async def on_resp(response: Response):
                nonlocal captured_parsed
                if captured_parsed is not None:
                    return
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype:
                    return
                if any(k in response.url for k in ["uzum.uz", "product", "graphql"]):
                    try:
                        raw = await response.json()
                        parsed = self._parse_uzum_payload(raw, p_id)
                        if parsed and parsed.get("variants") and parsed["variants"][0].get("price", 0) > 0:
                            captured_parsed = parsed
                    except Exception:
                        pass

            page.on("response", on_resp)
            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=45000)

                # A) Brauzer kontekstining o'zidan (cookie va tokenlar bilan) API ga fetch yuborish
                try:
                    in_page_json = await page.evaluate(
                        '''async (pid) => {
                            try {
                                const r = await fetch(`https://api.uzum.uz/api/v2/product/${pid}`, {
                                    headers: { "Accept": "application/json", "Accept-Language": "uz-UZ,uz;q=0.9,ru;q=0.8" },
                                    credentials: "include"
                                });
                                if (r.ok) return await r.json();
                            } catch (e) {}
                            return null;
                        }''',
                        p_id
                    )
                    if in_page_json:
                        parsed_in_page = self._parse_uzum_payload(in_page_json, p_id)
                        if parsed_in_page and parsed_in_page.get("variants") and parsed_in_page["variants"][0].get("price", 0) > 0:
                            captured_parsed = parsed_in_page
                except Exception:
                    pass

                # B) Network interceptor javobini va h1 elementini kutish
                try:
                    await page.wait_for_selector("h1", timeout=12000)
                except Exception:
                    pass

                for _ in range(20):
                    if captured_parsed is not None:
                        break
                    await asyncio.sleep(0.5)

                if captured_parsed:
                    # Agar sarlavha umumiy bo'lsa yoki rasm bo'lmasa, DOM dan boyitish
                    if not captured_parsed.get("title") or captured_parsed["title"].startswith("Uzum Mahsuloti"):
                        try:
                            h1 = (await page.locator("h1").first.inner_text()).strip()
                            if h1:
                                captured_parsed["title"] = h1
                        except Exception:
                            pass
                    if not captured_parsed.get("photo_url"):
                        try:
                            img = await page.locator("img[src*='images.uzum.uz']").first.get_attribute("src")
                            if img:
                                captured_parsed["photo_url"] = img
                        except Exception:
                            pass
                    return captured_parsed

                # C) Sahifa ichidagi SSR state (__NUXT__ / __INITIAL_STATE__ / ld+json) va DOM dan o'qish
                dom_scraped = await page.evaluate(
                    '''(args) => {
                        const { pid, skuId } = args;
                        for (const key of ["__INITIAL_STATE__", "__NUXT__", "__NEXT_DATA__"]) {
                            if (window[key]) {
                                return { ssr_state: window[key] };
                            }
                        }
                        const h1 = document.querySelector("h1");
                        const title = h1 ? h1.innerText.trim() : document.title;

                        let photo = null;
                        const ogImg = document.querySelector("meta[property='og:image']");
                        if (ogImg && ogImg.content) photo = ogImg.content;
                        if (!photo) {
                            const imgEl = document.querySelector("img[src*='images.uzum.uz']");
                            if (imgEl) photo = imgEl.src;
                        }

                        let discountPrice = 0;
                        let fullPrice = 0;
                        const priceNodes = Array.from(document.querySelectorAll("[data-test-id*='price'], [class*='price'], span, div"));
                        const foundPrices = [];
                        for (const el of priceNodes) {
                            if (el.children.length === 0 && el.innerText) {
                                const txt = el.innerText.trim();
                                if ((txt.includes("so'm") || txt.includes("сум")) && txt.length < 25) {
                                    const num = parseInt(txt.replace(/[^0-9]/g, ""), 10);
                                    if (num > 1000 && !foundPrices.includes(num)) {
                                        foundPrices.push(num);
                                    }
                                }
                            }
                        }
                        if (foundPrices.length === 1) {
                            discountPrice = foundPrices[0];
                            fullPrice = foundPrices[0];
                        } else if (foundPrices.length >= 2) {
                            discountPrice = Math.min(foundPrices[0], foundPrices[1]);
                            fullPrice = Math.max(foundPrices[0], foundPrices[1]);
                        }

                        return {
                            dom_data: {
                                title: title,
                                photo_url: photo,
                                price: discountPrice,
                                full_price: fullPrice
                            }
                        };
                    }''',
                    {"pid": p_id, "skuId": url_sku_id}
                )

                if dom_scraped and dom_scraped.get("ssr_state"):
                    parsed_ssr = self._parse_uzum_payload(dom_scraped["ssr_state"], p_id)
                    if parsed_ssr and parsed_ssr.get("variants") and parsed_ssr["variants"][0].get("price", 0) > 0:
                        return parsed_ssr

                if dom_scraped and dom_scraped.get("dom_data"):
                    dd = dom_scraped["dom_data"]
                    if dd.get("title") and not dd["title"].startswith("Uzum Tovar"):
                        return {
                            "id": p_id,
                            "title": dd["title"],
                            "photo_url": dd.get("photo_url"),
                            "variants": [{
                                "sku_id": url_sku_id,
                                "name": f"Tanlangan variant (#{url_sku_id})" if url_sku_id != p_id else "Asosiy variant",
                                "price": int(dd.get("price") or 0),
                                "full_price": int(dd.get("full_price") or dd.get("price") or 0),
                                "available": 1,
                            }],
                        }

                return None
            except Exception as e:
                print(f"[fetch_product_with_playwright] Xatolik: {e}")
                return None
            finally:
                await page.close()

    def _extract_sku_from_json(self, data: dict, target_sku_id: str | None = None) -> dict | None:
        parsed = self._parse_uzum_payload(data)
        if not parsed or not parsed.get("variants"):
            return None
        variants = parsed["variants"]
        chosen = None
        if target_sku_id:
            for v in variants:
                if str(v["sku_id"]) == str(target_sku_id):
                    chosen = v
                    break
        if not chosen:
            in_stock = [v for v in variants if v["available"] > 0]
            chosen = min(in_stock, key=lambda x: x["price"]) if in_stock else variants[0]

        if chosen["price"] <= 0:
            return None
        return {
            "title": parsed["title"],
            "price": chosen["price"],
            "available_amount": chosen["available"],
            "sku_id": chosen["sku_id"],
        }

    async def check_price_and_stock(self, product_url: str) -> dict | None:
        sku_match = re.search(r"skuId=(\d+)", product_url)
        target_sku_id = sku_match.group(1) if sku_match else None

        quick = await asyncio.to_thread(self.fetch_product_quick, product_url)
        if quick and quick.get("variants"):
            variants = quick["variants"]
            chosen = None
            if target_sku_id:
                chosen = next((v for v in variants if str(v["sku_id"]) == str(target_sku_id)), None)
            if not chosen:
                in_stock = [v for v in variants if v["available"] > 0]
                chosen = min(in_stock, key=lambda x: x["price"]) if in_stock else variants[0]
            if chosen and chosen["price"] > 0:
                return {
                    "title": quick["title"],
                    "price": chosen["price"],
                    "available_amount": chosen["available"],
                    "sku_id": chosen["sku_id"],
                }

        async with self._lock:
            page = await self.get_safe_page()
            captured_result = None

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
                for _ in range(16):
                    if captured_result is not None:
                        break
                    await asyncio.sleep(0.5)

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
        async with self._lock:
            page = await self.get_safe_page()
            screenshot_path = SCREENSHOTS_DIR / f"order_{product_id}_{int(time.time())}.png"

            try:
                await page.goto(product_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(1.5)

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
                    in_cart_loc = page.locator("a[href*='/cart'], button:has-text('Savatga oʻtish'), button:has-text('Перейти в корзину')").first
                    if await in_cart_loc.count() == 0:
                        await page.screenshot(path=str(screenshot_path), full_page=True)
                        return {
                            "success": False,
                            "error": "Savatga qo'shish tugmasi topilmadi yoki tovar tugagan.",
                            "screenshot": str(screenshot_path),
                        }

                await asyncio.sleep(1.5)
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
                        "error": "Checkout sahifasida yakuniy tasdiqlash tugmasi bosilmadi.",
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
