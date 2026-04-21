import flet as ft
import aiohttp
import asyncio

API_BASE_URL = "http://127.0.0.1:8000"

def format_crypto(price):
    if price is None or price == 0: 
        return "$0.00"
    if price < 1.0:
        # 4 decimale za Dogecoin, XRP i slične
        return f"${price:,.4f}"
    # 2 decimale za Bitcoin, Solanu itd.
    return f"${price:,.2f}"

async def main(page: ft.Page):
    page.title = "Crypto Pulse AI"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 400  
    page.window.height = 800 
    
    # Glavni kontejner za stranice
    main_content = ft.Column(expand=True, scroll=ft.ScrollMode.ADAPTIVE)
    selected_asset_type = {"value": "crypto"}
    dashboard_context = {"asset_type": "crypto", "instrument_name": "", "ticker": ""}

    # --- POMOĆNA FUNKCIJA: ZAPRATI (TRACKER) ---
    async def zaprati_valutu(asset, ai_result):
        try:
            # Vadimo podatke iz AI odgovora uz osigurače (ako nešto fali, stavljamo 0)
            forecast = ai_result.get('forecast', {})
            current_price = float(asset.get('current_price', 0.0))

            # Sanity check: forecast mora biti u opsegu 50%-200% trenutne cene
            def _sane(val, fallback):
                v = float(val or 0)
                if current_price > 0 and (v < current_price * 0.5 or v > current_price * 2.0):
                    return fallback
                return v

            expected_7d = _sane(forecast.get('expected', 0), current_price * 1.03)
            bull_val    = _sane(forecast.get('bull', 0),     current_price * 1.10)
            bear_val    = _sane(forecast.get('bear', 0),     current_price * 0.92)

            payload = {
                "asset_id": asset.get('id', asset.get('name', 'unknown').lower()),
                "asset_name": asset.get('name', 'Nepoznato'),
                "ticker": (asset.get('symbol') or asset.get('ticker') or 'UNK').upper(),
                "asset_type": asset.get('asset_type', 'crypto'),
                "current_price": current_price,
                "expected_price_7d": expected_7d,
                "bull_case": bull_val,
                "bear_case": bear_val,
                "model_confidence": float(forecast.get('confidence', 0))
            }
            
            # Šaljemo podatke na backend (Tracker ruta)
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{API_BASE_URL}/api/tracker", json=payload) as resp:
                    if resp.status == 200:
                        snack = ft.SnackBar(ft.Text(f"✅ {asset.get('name')} je uspešno dodat u Watcher!"), bgcolor=ft.Colors.GREEN_700)
                        page.overlay.append(snack)
                        snack.open = True
                    else:
                        error_text = await resp.text()
                        snack = ft.SnackBar(ft.Text(f"❌ Greška baze: {error_text}"), bgcolor=ft.Colors.RED_700)
                        page.overlay.append(snack)
                        snack.open = True
                    
                    page.update()
        except Exception as e:
            snack = ft.SnackBar(ft.Text(f"❌ Mrežna greška: {str(e)}"), bgcolor=ft.Colors.RED_700)
            page.overlay.append(snack)
            snack.open = True
            page.update()

    def formatiraj_cenu(cena):
        """Ako je cena manja od 1, prikaži 4 decimale, inače 2."""
        if cena < 1:
            return f"${cena:,.4f}"
        return f"${cena:,.2f}"
    
    # --- STRANICA: DETALJI I AI ANALIZA ---
    # --- STRANICA: DETALJI VALUTE (MODULARNI PREMIUM DIZAJN) ---
    async def prikazi_detalje(asset):
        main_content.controls.clear()

        dashboard_context["asset_type"] = asset.get("asset_type", selected_asset_type["value"])
        dashboard_context["instrument_name"] = asset.get("name", "")
        dashboard_context["ticker"] = asset.get("ticker", "")
        
        ticker = asset.get('ticker', 'UNK').upper()
        trenutna_cena = asset.get('current_price', 0.0)
        promena = asset.get('change_24h', 0.0)
        
        boja_promene = ft.Colors.GREEN_400 if promena >= 0 else ft.Colors.RED_400
        bg_boja_promene = ft.Colors.with_opacity(0.1, boja_promene)
        ikona_promene = "▲" if promena >= 0 else "▼"

        # --- ZAGLAVLJE ---
        header_row = ft.Row([
            ft.IconButton(ft.Icons.ARROW_BACK, icon_color=ft.Colors.GREY_300, on_click=lambda e: asyncio.create_task(ucitaj_dashboard())),
            ft.Row([
                ft.Image(src=asset.get('icon', ''), width=28, height=28) if asset.get('icon') else ft.Icon(ft.Icons.MONETIZATION_ON),
                ft.Text(asset.get('name', 'Nepoznato'), size=20, weight="bold", color=ft.Colors.WHITE),
                ft.Container(
                    content=ft.Text(ticker, size=10, color=ft.Colors.GREY_400, weight="bold"),
                    bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE), padding=ft.padding.symmetric(horizontal=6, vertical=2), border_radius=4
                )
            ], spacing=10)
        ], alignment=ft.MainAxisAlignment.START)

        price_row = ft.Row([
            ft.Text(f"${trenutna_cena:,.2f}", size=34, weight="bold", color=ft.Colors.WHITE),
            ft.Container(
                content=ft.Text(f"{ikona_promene} {abs(promena):.2f}%", size=12, color=boja_promene, weight="bold"),
                bgcolor=bg_boja_promene, padding=ft.padding.symmetric(horizontal=8, vertical=4), border_radius=8
            )
        ], alignment=ft.MainAxisAlignment.START, spacing=15)

        # Loading indikator
        ai_content = ft.Column([
            ft.Container(height=40),
            ft.Row([ft.ProgressRing(color=ft.Colors.PURPLE_400)], alignment=ft.MainAxisAlignment.CENTER),
            ft.Text("Kvantna AI analiza u toku...", color=ft.Colors.PURPLE_300, size=12, text_align=ft.TextAlign.CENTER)
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        main_content.controls = [
            header_row,
            ft.Container(height=10),
            price_row,
            ft.Container(height=20),
            ai_content
        ]
        page.update()

        # --- POVLAČENJE AI ANALIZE ---
        try:
            async with aiohttp.ClientSession() as session:
                # 1. KORAK: Prvo povlačimo istoriju cena (potrebno za AI)
                asset_id = asset.get('id', asset.get('name', '').lower())
                istorija_cena = []
                
                asset_type = asset.get("asset_type", "crypto")
                async with session.get(f"{API_BASE_URL}/api/asset/{asset_id}/history?days=30&asset_type={asset_type}") as hist_res:
                    if hist_res.status == 200:
                        istorija_cena = await hist_res.json()

                # 2. KORAK: Pakujemo sve potrebne podatke koje backend zahteva
                payload = {
                    "asset_name": asset.get('name', 'Nepoznato'),
                    "ticker": ticker,
                    "asset_type": asset_type,
                    "current_price": trenutna_cena,
                    "change_24h": promena,
                    "history": istorija_cena
                }

                print(f"""\n{'='*50}
[DETALJI] Šaljem na /api/ai/analysis:
  asset_name : {payload['asset_name']}
  ticker     : {payload['ticker']}
  price      : ${payload['current_price']}
  change_24h : {payload['change_24h']:.2f}%
  history    : {len(istorija_cena)} tačaka ({istorija_cena[0]['date'] if istorija_cena else 'nema'} → {istorija_cena[-1]['date'] if istorija_cena else 'nema'})
{'='*50}""")

                # 3. KORAK: Šaljemo kompletan paket na analizu
                async with session.post(f"{API_BASE_URL}/api/ai/analysis", json=payload) as response:
                    if response.status == 200:
                        analiza = await response.json()
                        
                        # ZAJEDNIČKI STIL ZA SVE KARTICE
                        card_style = {
                            "padding": 20, 
                            "border_radius": 15, 
                            "bgcolor": ft.Colors.with_opacity(0.02, ft.Colors.WHITE), 
                            "border": ft.border.all(1, ft.Colors.with_opacity(0.05, ft.Colors.WHITE))
                        }
                        
                        # 0. NEWS & SENTIMENT KARTICA
                        news_data = analiza.get('news_sentiment', {})
                        news_score = news_data.get('score', 'NEUTRAL').upper()
                        news_color = ft.Colors.GREEN_400 if "BULL" in news_score else (ft.Colors.RED_400 if "BEAR" in news_score else ft.Colors.AMBER_400)
                        
                        news_card = ft.Container(
                            content=ft.Column([
                                ft.Row([ft.Icon(ft.Icons.NEWSPAPER, size=16, color=ft.Colors.WHITE70), ft.Text("News & Sentiment", size=14, weight="bold", color=ft.Colors.WHITE)], spacing=8),
                                ft.Divider(color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
                                ft.Row([ft.Text("Overall Media Sentiment", size=12, color=ft.Colors.GREY_400), ft.Text(news_score, size=14, weight="bold", color=news_color)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                ft.Text(f"\"{news_data.get('headline', 'Nema značajnih vesti u ovom trenutku.')}\"", size=13, color=ft.Colors.WHITE70, italic=True)
                            ], spacing=10),
                            **card_style
                        )

                        # 1. MARKET STANCE KARTICA
                        stance_val = analiza.get('stance', 'NEUTRAL').upper()
                        stance_color = ft.Colors.GREEN_400 if "BULL" in stance_val else (ft.Colors.RED_400 if "BEAR" in stance_val else ft.Colors.GREY_400)
                        
                        stance_card = ft.Container(
                            content=ft.Column([
                                ft.Row([ft.Text("Market Stance", size=14, color=ft.Colors.GREY_400), ft.Text(stance_val, size=14, weight="bold", color=stance_color)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                ft.Divider(color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
                                ft.Text(analiza.get('summary', ''), size=13, color=ft.Colors.WHITE70)
                            ], spacing=10),
                            **card_style
                        )

                        # 2. KEY CATALYSTS KARTICA
                        katalizatori_ui = []
                        for cat in analiza.get('catalysts', []):
                            katalizatori_ui.append(
                                ft.Container(
                                    content=ft.Row([
                                        ft.Icon(ft.Icons.CIRCLE, size=8, color=ft.Colors.PURPLE_400),
                                        ft.Text(cat, size=13, color=ft.Colors.GREY_300, expand=True)
                                    ], vertical_alignment=ft.CrossAxisAlignment.START),
                                    padding=ft.padding.only(bottom=8)
                                )
                            )

                        catalysts_card = ft.Container(
                            content=ft.Column([
                                ft.Row([ft.Icon(ft.Icons.LIGHTBULB_OUTLINE, size=16, color=ft.Colors.AMBER_400), ft.Text("Key Catalysts", size=14, weight="bold", color=ft.Colors.WHITE)], spacing=8),
                                ft.Divider(color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
                                ft.Column(katalizatori_ui, spacing=0)
                            ], spacing=10),
                            **card_style
                        )

                        # 3. RECOMMENDATION & RISK KARTICA
                        rec_val = analiza.get('recommendation', 'HOLD').upper()
                        rec_color = ft.Colors.GREEN_400 if "BUY" in rec_val else (ft.Colors.RED_400 if "SELL" in rec_val else ft.Colors.AMBER_400)
                        risk_val = analiza.get('risk_level', 'MEDIUM').title()

                        rec_card = ft.Container(
                            content=ft.Row([
                                ft.Column([ft.Text("Recommendation", size=12, color=ft.Colors.GREY_400), ft.Container(content=ft.Text(rec_val, size=14, weight="bold", color=rec_color), bgcolor=ft.Colors.with_opacity(0.1, rec_color), padding=ft.padding.symmetric(horizontal=10, vertical=4), border_radius=6)], expand=True),
                                ft.Container(width=1, height=40, bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)), # Vertikalna linija
                                ft.Column([ft.Text("Risk Level", size=12, color=ft.Colors.GREY_400), ft.Text(risk_val, size=14, weight="bold", color=ft.Colors.WHITE)], expand=True, horizontal_alignment=ft.CrossAxisAlignment.END)
                            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                            **card_style
                        )

                        # --- 4. FORECAST KARTICA SA POZIVOM GLAVNE FUNKCIJE ---
                        forecast = analiza.get('forecast', {})

                        # Sanity check: forecast mora biti u opsegu 50%-200% trenutne cene
                        def _sane_forecast(val, fallback):
                            v = float(val or 0)
                            if trenutna_cena > 0 and (v < trenutna_cena * 0.5 or v > trenutna_cena * 2.0):
                                return fallback
                            return v if v > 0 else fallback

                        exp_price  = _sane_forecast(forecast.get('expected', 0), round(trenutna_cena * 1.03, 2))
                        bull_price = _sane_forecast(forecast.get('bull', 0),     round(trenutna_cena * 1.10, 2))
                        bear_price = _sane_forecast(forecast.get('bear', 0),     round(trenutna_cena * 0.92, 2))

                        # Upisujemo sanirane vrijednosti nazad u analiza dict
                        analiza.setdefault('forecast', {})
                        analiza['forecast']['expected'] = exp_price
                        analiza['forecast']['bull']     = bull_price
                        analiza['forecast']['bear']     = bear_price

                        # Dugme koje sada koristi tvoju "zaprati_valutu" funkciju sa početka fajla
                        btn_zaprati = ft.ElevatedButton(
                            content=ft.Row(
                                [
                                    ft.Icon(ft.Icons.ADD, color=ft.Colors.WHITE),
                                    ft.Text("Zaprati u Trackeru", weight="bold", color=ft.Colors.WHITE),
                                ],
                                alignment=ft.MainAxisAlignment.CENTER,
                                tight=True,
                            ),
                            bgcolor=ft.Colors.BLUE_600,
                            # OVDE POZIVAMO TVOJU FUNKCIJU
                            on_click=lambda _: asyncio.create_task(zaprati_valutu(asset, analiza)),
                            width=300,
                            height=45,
                        )
                        
                        forecast_card = ft.Container(
                            content=ft.Column([
                                ft.Row([
                                    ft.Icon(ft.Icons.TRENDING_UP, size=16, color=ft.Colors.BLUE_400), 
                                    ft.Text("Price Target (7D)", size=14, weight="bold", color=ft.Colors.WHITE)
                                ], spacing=8),
                                ft.Divider(color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
                                ft.Column([
                                    ft.Text("Expected Price", size=12, color=ft.Colors.GREY_400),
                                    ft.Text(f"${exp_price:,.2f}", size=32, weight="bold", color=ft.Colors.WHITE)
                                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                ft.Container(height=10),    
                                ft.Row([
                                    ft.Container(
                                        content=ft.Column([ft.Text("BULL CASE", size=10, color=ft.Colors.GREEN_400, weight="bold"), ft.Text(f"${bull_price:,.2f}", size=16, weight="bold", color=ft.Colors.WHITE)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                        bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.GREEN_400), border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.GREEN_400)), border_radius=10, padding=10, expand=True
                                    ),
                                    ft.Container(
                                        content=ft.Column([ft.Text("BEAR CASE", size=10, color=ft.Colors.RED_400, weight="bold"), ft.Text(f"${bear_price:,.2f}", size=16, weight="bold", color=ft.Colors.WHITE)], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                        bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.RED_400), border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.RED_400)), border_radius=10, padding=10, expand=True
                                    )
                                ], spacing=15),
                                ft.Container(height=15),
                                ft.Row([btn_zaprati], alignment=ft.MainAxisAlignment.CENTER)
                            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                            **card_style
                        )

                        # --- SKLAPANJE SVIH KARTICA ---
                        ai_content.controls.clear()
                        ai_content.controls.extend([
                            news_card,
                            ft.Container(height=5),
                            stance_card,
                            ft.Container(height=5),
                            catalysts_card,
                            ft.Container(height=5),
                            rec_card,
                            ft.Container(height=5),
                            forecast_card,
                            ft.Container(height=30)
                        ])
                        
                    else:
                        error_data = await response.json()
                        ai_content.controls = [ft.Text(f"Greška: {error_data.get('detail', 'Nepoznata')}", color=ft.Colors.RED)]
                        
        except Exception as e:
            ai_content.controls = [ft.Text(f"Dogodila se neočekivana greška: {e}", color=ft.Colors.RED)]

        page.update()

    def create_mobile_stat_card(title, value, subtitle="", icon_name=None, color=ft.Colors.WHITE):
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon_name, size=16, color=color), 
                    ft.Text(title, size=12, color=ft.Colors.GREY_400)
                ]),
                ft.Text(value, size=20, weight="bold", color=ft.Colors.WHITE),
                ft.Text(subtitle, size=10, color=color)
            ], spacing=5),
            padding=15,
            border_radius=15,
            width=170, # <--- BLAGO PROŠIRENO ZA 2x2 GRID
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.WHITE),
            border=ft.border.all(1, ft.Colors.with_opacity(0.08, ft.Colors.WHITE)),
        )

    def normalize_asset_type(raw_value: str) -> str:
        if not raw_value:
            return "crypto"
        normalized = str(raw_value).strip().lower()
        aliases = {
            "crypto": "crypto",
            "cryptocurrency": "crypto",
            "cryptocurrencies": "crypto",
            "stock": "stock",
            "stocks": "stock",
            "index": "index",
            "indices": "index",
            "commodity": "commodity",
            "commodities": "commodity",
        }
        return aliases.get(normalized, "crypto")

    def resolve_icon(icon_name: str):
        if not icon_name:
            return ft.Icons.INSIGHTS
        return getattr(ft.Icons, icon_name, ft.Icons.INSIGHTS)

    def resolve_color(color_name: str):
        if not color_name:
            return ft.Colors.WHITE
        return getattr(ft.Colors, color_name, ft.Colors.WHITE)

    def format_stat_value(value, format_name: str) -> str:
        try:
            val = float(value)
        except Exception:
            val = 0.0

        if format_name == "currency_trillion":
            return f"${val/1e12:.2f}T"
        if format_name == "currency_billion":
            return f"${val/1e9:.1f}B"
        if format_name == "currency":
            return f"${val:,.2f}"
        if format_name == "percent":
            return f"{val:.1f}%"
        if format_name == "percent_signed":
            return f"{val:+.2f}%"
        if format_name == "integer":
            return f"{int(round(val))}"
        return str(value)


    # Loading guard — sprečava race condition pri brzoj promeni dropdowna
    _dashboard_loading = {"active": False}

    _TYPE_TO_IDX = {"crypto": 0, "stock": 1, "index": 2, "commodity": 3}
    _IDX_TO_TYPE = {0: "crypto", 1: "stock", 2: "index", 3: "commodity"}

    # --- STRANICA: DASHBOARD (PREMIUM MOBILNI DIZAJN) ---
    async def ucitaj_dashboard():
        # Guard: ako je fetch već u toku, ignorišemo novi poziv
        if _dashboard_loading["active"]:
            return
        _dashboard_loading["active"] = True

        async def on_type_select(e):
            raw_value = getattr(e, "data", None) or getattr(e.control, "value", None)
            selected_asset_type["value"] = normalize_asset_type(raw_value)
            await ucitaj_dashboard()

        asset_type_dropdown = ft.Dropdown(
            value=selected_asset_type["value"],
            width=160,
            options=[
                ft.dropdown.Option("crypto", "Crypto"),
                ft.dropdown.Option("stock", "Stocks"),
                ft.dropdown.Option("index", "Indices"),
                ft.dropdown.Option("commodity", "Commodities"),
            ],
            on_select=on_type_select,
        )

        try:
            main_content.controls.clear()

            header = ft.Row([
                ft.Column([
                    ft.Text("Market Pulse", size=24, weight="bold", color=ft.Colors.WHITE),
                    ft.Text("Real-time AI Analytics", size=12, color=ft.Colors.BLUE_400)
                ], spacing=2),
                ft.Row([
                    asset_type_dropdown,
                    ft.IconButton(ft.Icons.SEARCH, icon_color=ft.Colors.GREY_400),
                ], spacing=8)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

            # --- GRID KARTICE (2x2 Prikaz) ---
            cards_row = ft.Row(
                controls=[ft.Container(content=ft.ProgressRing(), padding=20)],
                wrap=True,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=15,
                run_spacing=15
            )

            # --- LISTA VALUTA (Moderan prikaz) ---
            market_list = ft.Column(spacing=10)
            asset_section_title = ft.Text("Top Assets", size=16, weight="bold", color=ft.Colors.GREY_300)

            # Sklapanje glavnog interfejsa
            main_content.controls = [
                header,
                ft.Container(height=15),
                cards_row,
                ft.Container(height=20),
                asset_section_title,
                market_list
            ]
            page.update()

            # --- ASINHRONO POVLAČENJE PODATAKA ---
            async with aiohttp.ClientSession() as session:
                # 1. Globalni podaci (za 4 kartice)
                asset_type = selected_asset_type["value"]
                dashboard_context["asset_type"] = asset_type

                async with session.get(f"{API_BASE_URL}/api/market/global?asset_type={asset_type}") as r1:
                    if r1.status == 200:
                        d = await r1.json()
                        cards = d.get("cards", [])

                        if cards:
                            cards_row.controls = [
                                create_mobile_stat_card(
                                    c.get("title", "Metric"),
                                    format_stat_value(c.get("value", 0), c.get("format", "plain")),
                                    c.get("subtitle", ""),
                                    resolve_icon(c.get("icon")),
                                    resolve_color(c.get("color")),
                                )
                                for c in cards[:4]
                            ]
                        else:
                            # Legacy fallback ako backend vrati stari format
                            mc = d.get('total_market_cap') or 0
                            vol = d.get('total_volume_24h') or 0
                            fg = d.get('fear_greed_index') or 50
                            fg_label = d.get('fear_greed_label') or "Neutral"
                            btc_dom = d.get('btc_dominance') or 50.5

                            fg_color = ft.Colors.GREEN_400 if fg > 50 else (ft.Colors.RED_400 if fg < 40 else ft.Colors.AMBER_400)
                            cards_row.controls = [
                                create_mobile_stat_card("Market Cap", f"${mc/1e12:.2f}T", "Global", ft.Icons.PIE_CHART, ft.Colors.BLUE_400),
                                create_mobile_stat_card("Volume 24h", f"${vol/1e9:.1f}B", "Total", ft.Icons.BAR_CHART, ft.Colors.PURPLE_400),
                                create_mobile_stat_card("BTC Dom", f"{btc_dom:.1f}%", "Market Share", ft.Icons.CURRENCY_BITCOIN, ft.Colors.ORANGE_400),
                                create_mobile_stat_card("Fear & Greed", f"{fg}", fg_label, ft.Icons.LOCAL_FIRE_DEPARTMENT, fg_color)
                            ]
                    page.update()

                # 2. Lista instrumenata (Premium list item dizajn)
                pretty_type = {
                    "crypto": "Top Crypto",
                    "stock": "Top Stocks",
                    "index": "Top Indices",
                    "commodity": "Top Commodities",
                }.get(asset_type, "Top Assets")
                asset_section_title.value = pretty_type
                page.update()

                market_list.controls.clear()
                page.update()

                async with session.get(f"{API_BASE_URL}/api/market/top?limit=10&asset_type={asset_type}") as r2:
                    if r2.status == 200:
                        assets = await r2.json()

                        if assets:
                            dashboard_context["instrument_name"] = assets[0].get("name", "")
                            dashboard_context["ticker"] = assets[0].get("ticker", "")

                        async def open_asset_details(selected_asset):
                            dashboard_context["asset_type"] = selected_asset.get("asset_type", asset_type)
                            dashboard_context["instrument_name"] = selected_asset.get("name", "")
                            dashboard_context["ticker"] = selected_asset.get("ticker", "")
                            await prikazi_detalje(selected_asset)

                        for a in assets:
                            image_url = a.get('icon', "")
                            tren_cena = a.get('current_price') or 0.0
                            promena = a.get('change_24h') or 0.0

                            color_change = ft.Colors.GREEN_400 if promena > 0 else ft.Colors.RED_400
                            arrow_icon = "▲" if promena > 0 else "▼"

                            asset_item = ft.Container(
                                content=ft.Row([
                                    # Leva strana: Logo i Ime
                                    ft.Row([
                                        ft.Image(src=image_url, width=36, height=36) if image_url and "http" in image_url else ft.CircleAvatar(content=ft.Text(a.get('ticker', 'X')[0])),
                                        ft.Column([
                                            ft.Text(a.get('name', 'Nepoznato'), weight="bold", size=15),
                                            ft.Text(a.get('ticker', 'UNK').upper(), color=ft.Colors.GREY_500, size=12)
                                        ], spacing=2)
                                    ], spacing=15),

                                    # Desna strana: Cena i Promena
                                    ft.Column([
                                        ft.Text(f"${tren_cena:,.2f}", size=15, weight="bold"),
                                        ft.Text(f"{arrow_icon} {abs(promena):.2f}%", color=color_change, size=12, weight="bold")
                                    ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END)
                                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                padding=15,
                                border_radius=15,
                                bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.WHITE),
                                on_click=lambda e, val=a: page.run_task(open_asset_details, val)
                            )
                            market_list.controls.append(asset_item)
                        if not assets:
                            market_list.controls.append(ft.Text("Nema instrumenata za izabrani tip.", color=ft.Colors.GREY_400))
                    else:
                        market_list.controls.append(ft.Text(f"🔴 Greška pri učitavanju liste ({r2.status})", color=ft.Colors.RED))

        except Exception as e:
            print(f"Greška na Dashboardu: {e}")
        finally:
            _dashboard_loading["active"] = False

        page.update()

    # --- POMOĆNA FUNKCIJA ZA BRISANJE ---
    async def obrisi_iz_trackera(sid):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(f"{API_BASE_URL}/api/tracker/{sid}") as resp:
                    if resp.status == 200:
                        # Prikazujemo potvrdu korisniku
                        snack = ft.SnackBar(ft.Text("Uspešno obrisano iz Watchera."), bgcolor=ft.Colors.BLUE_GREY_800)
                        page.overlay.append(snack)
                        snack.open = True
                        # KLJUČNO: Ponovo osvežavamo prikaz
                        await ucitaj_tracker()
        except Exception as e:
            print(f"Greška pri brisanju: {e}")
        page.update()

    # --- STRANICA: TRACKER (PREMIUM MOBILNI DIZAJN) ---
    async def ucitaj_tracker():
        main_content.controls.clear()
        
        # --- ZAGLAVLJE ---
        header = ft.Row([
            ft.Column([
                ft.Row([ft.Icon(ft.Icons.TRACK_CHANGES, color=ft.Colors.BLUE_400, size=24), ft.Text("Forecast Tracker", size=24, weight="bold", color=ft.Colors.WHITE)]),
                ft.Text("Uporedi AI prognoze sa stvarnim cenama", size=12, color=ft.Colors.GREY_400)
            ], spacing=2),
            # Bedž za broj praćenih valuta
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.CIRCLE, size=8, color=ft.Colors.BLUE_400), ft.Text("Active", size=12, weight="bold", color=ft.Colors.WHITE)], spacing=4),
                bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.BLUE_400), padding=ft.padding.symmetric(horizontal=10, vertical=5), border_radius=15
            )
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        
        tracker_list = ft.Column(spacing=20)
        
        # --- FUNKCIJA ZA BRISANJE IZ TRACKERA ---
        async def obrisi_iz_trackera(snapshot_id):
            if not snapshot_id:
                print("Greška: Nema ID-ja za brisanje!")
                return
                
            try:
                async with aiohttp.ClientSession() as session:
                    # Sada gađamo tačnu rutu sa ID brojem iz tvoje baze
                    async with session.delete(f"{API_BASE_URL}/api/tracker/{snapshot_id}") as response:
                        if response.status in [200, 204]:
                            print(f"Uspješno obrisano iz baze (ID: {snapshot_id})")
                            asyncio.create_task(ucitaj_tracker()) # Osvežava listu na ekranu
                        else:
                            print(f"Greška pri brisanju API-ja, status: {response.status}")
            except Exception as e:
                print(f"Greška u mreži pri brisanju: {e}")

        # Loading prikaz
        tracker_list.controls.append(
                ft.Container(
                    content=ft.Row([ft.ProgressRing()], alignment=ft.MainAxisAlignment.CENTER), 
                    padding=40
                )
            )

        main_content.controls = [
            header,
            ft.Container(height=15),
            tracker_list
        ]
        page.update()

        # --- ASINHRONO POVLAČENJE PODATAKA ---
        try:
            async with aiohttp.ClientSession() as session:
                # NAPOMENA: Prilagodi URL tvom stvarnom endpointu za tracker ako se razlikuje!
                async with session.get(f"{API_BASE_URL}/api/tracker") as response:
                    tracker_list.controls.clear()
                    
                    if response.status == 200:
                        tracked_assets = await response.json()
                        
                        if not tracked_assets:
                            tracker_list.controls.append(ft.Text("Trenutno ne pratiš nijednu valutu. Dodaj ih sa detaljne stranice!", color=ft.Colors.GREY_400, text_align=ft.TextAlign.CENTER))
                        
                        # Fetchujemo 7d historiju za sve praćene assete paralelno
                        async def fetch_history_for(item):
                            asset_id = item.get('asset_id', '')
                            asset_type = item.get('asset_type', 'crypto')
                            try:
                                async with session.get(f"{API_BASE_URL}/api/asset/{asset_id}/history?days=7&asset_type={asset_type}") as hr:
                                    if hr.status == 200:
                                        return await hr.json()
                            except Exception:
                                pass
                            return []

                        histories = await asyncio.gather(*[fetch_history_for(it) for it in tracked_assets])

                        for item, hist_data in zip(tracked_assets, histories):
                            ticker = item.get('ticker', 'UNK').upper()
                            name = item.get('asset_name', 'Nepoznato')
                            
                            # ИСПРАВЉЕНО: Користимо тачна имена поља из твог api.py
                            entry_price = item.get('current_price', 0.0)
                            target_price = item.get('expected_price_7d', 0.0)
                            confidence = item.get('model_confidence', 0)
                            
                            # Извлачимо bull и bear случајеве
                            bull_val = item.get('bull_case', 0.0)
                            bear_val = item.get('bear_case', 0.0)
                            
                            # Izračunavanje razlike
                            exp_delta = ((target_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                            delta_color = ft.Colors.GREEN_400 if exp_delta >= 0 else ft.Colors.RED_400
                            delta_str = f"+{exp_delta:.2f}%" if exp_delta >= 0 else f"{exp_delta:.2f}%"

                            # Datum praćenja i preostali dani
                            tracked_at_str = item.get('tracked_at', '')
                            days_remaining = 7
                            try:
                                from datetime import datetime as _dt
                                tracked_dt = _dt.strptime(tracked_at_str, "%Y-%m-%d %H:%M")
                                days_elapsed = (_dt.utcnow() - tracked_dt).days
                                days_remaining = max(0, 7 - days_elapsed)
                            except Exception:
                                pass
                            days_color = ft.Colors.GREEN_400 if days_remaining > 2 else (ft.Colors.AMBER_400 if days_remaining > 0 else ft.Colors.RED_400)
                            days_label = f"{days_remaining}d left" if days_remaining > 0 else "Isteklo"

                            # 1. ZAGLAVLJE KARTICE (Logo + Kanta za brisanje)

                            # Izvlačimo tačan ID iz baze (obično je pod ključem 'id' ili 'snapshot_id')
                            item_id = item.get('id') or item.get('snapshot_id')

                            card_header = ft.Row([
                                ft.Row([
                                    ft.CircleAvatar(content=ft.Text(ticker[0]), radius=16, bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
                                    ft.Column([
                                        ft.Text(name, size=14, weight="bold"),
                                        ft.Row([
                                            ft.Text(ticker, size=10, color=ft.Colors.GREY_400),
                                            ft.Container(
                                                content=ft.Text(days_label, size=9, weight="bold", color=days_color),
                                                bgcolor=ft.Colors.with_opacity(0.1, days_color),
                                                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                                border_radius=4
                                            ),
                                            ft.Text(f"od {tracked_at_str[:10]}", size=9, color=ft.Colors.GREY_600),
                                        ], spacing=6)
                                    ], spacing=2)
                                ]),
                                ft.IconButton(
                                    ft.Icons.DELETE_OUTLINE,
                                    icon_color=ft.Colors.GREY_500,
                                    tooltip="Ukloni iz Trackera",
                                    on_click=lambda e, sid=item_id: asyncio.create_task(obrisi_iz_trackera(sid))
                                )
                            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                            
                            # 2. SNAPSHOT BOX (Sivi box unutar kartice)
                            snapshot_box = ft.Container(
                                content=ft.Column([
                                    ft.Row([ft.Icon(ft.Icons.RADAR, size=14, color=ft.Colors.GREY_400), ft.Text("7D FORECAST SNAPSHOT", size=10, weight="bold", color=ft.Colors.GREY_400)], spacing=5),
                                    ft.Divider(height=10, color=ft.Colors.with_opacity(0.1, ft.Colors.WHITE)),
                                    ft.Row([ft.Text("Entry Price", size=12, color=ft.Colors.GREY_400), ft.Text(f"${entry_price:,.2f}", size=14, weight="bold")], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                    ft.Row([ft.Text("Target", size=12, color=ft.Colors.GREY_400), ft.Text(f"${target_price:,.2f}", size=14, weight="bold", color=ft.Colors.BLUE_400)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                    ft.Row([ft.Text("Expected Δ", size=12, color=ft.Colors.GREY_400), ft.Text(delta_str, size=14, weight="bold", color=delta_color)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                    ft.Container(height=5),
                                    # BULL / BEAR Mini Box са 2 децимале
                                    ft.Row([
                                        ft.Container(
                                            content=ft.Column([
                                                ft.Text("BULL", size=9, color=ft.Colors.GREEN_400), 
                                                ft.Text(f"${bull_val:,.2f}", size=12, weight="bold") # Промењено на .2f
                                            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER), 
                                            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.GREEN_400), 
                                            border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.GREEN_400)), 
                                            border_radius=8, padding=5, expand=True
                                        ),
                                        ft.Container(
                                            content=ft.Column([
                                                ft.Text("BEAR", size=9, color=ft.Colors.RED_400), 
                                                ft.Text(f"${bear_val:,.2f}", size=12, weight="bold") # Промењено на .2f
                                            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER), 
                                            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.RED_400), 
                                            border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.RED_400)), 
                                            border_radius=8, padding=5, expand=True
                                        )
                                    ], spacing=10),
                                    ft.Container(height=5),
                                    # Confidence Bar
                                    ft.Row([ft.Text("Confidence", size=10, color=ft.Colors.GREY_400), ft.Text(f"{confidence}%", size=10, weight="bold")], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                    ft.ProgressBar(value=confidence/100, color=ft.Colors.BLUE_400, bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.BLUE_400))
                                ]),
                                padding=15, border_radius=12, bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.WHITE)
                            )
                            
                            # 3. DNEVNE CENE iz stvarnih podataka historije
                            days_row = ft.Row(scroll=ft.ScrollMode.ADAPTIVE, spacing=10)
                            
                            if hist_data and len(hist_data) > 0:
                                # Uzimamo zadnjih 7 dana iz historije
                                recent = hist_data[-7:]
                                for i, day_point in enumerate(recent):
                                    day_price = day_point.get('price', 0.0)
                                    if day_price and entry_price > 0:
                                        day_change = round((day_price - entry_price) / entry_price * 100, 2)
                                    else:
                                        day_change = 0.0
                                    d_color = ft.Colors.GREEN_400 if day_change >= 0 else ft.Colors.RED_400
                                    day_label = day_point.get('date', f'D{i+1}')  # "03 Apr" format
                                    days_row.controls.append(
                                        ft.Container(
                                            content=ft.Column([
                                                ft.Text(day_label, size=9, color=ft.Colors.GREY_400, weight="bold"),
                                                ft.Text(f"${day_price:,.2f}" if day_price < 10000 else f"${day_price:,.0f}", size=12, weight="bold", color=d_color),
                                                ft.Text(f"{'+' if day_change >= 0 else ''}{day_change:.1f}%", size=10, color=d_color)
                                            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
                                            width=82, padding=8, border_radius=10,
                                            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.WHITE),
                                            border=ft.border.all(1, ft.Colors.with_opacity(0.08, d_color))
                                        )
                                    )
                            else:
                                # Fallback: prazne kutije
                                for i in range(1, 8):
                                    days_row.controls.append(
                                        ft.Container(
                                            content=ft.Row([ft.Text("-", size=20, color=ft.Colors.GREY_600)], alignment=ft.MainAxisAlignment.CENTER), 
                                            width=80, height=80, 
                                            bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.WHITE), 
                                            border_radius=10, 
                                            border=ft.border.all(1, ft.Colors.with_opacity(0.05, ft.Colors.WHITE))
                                        )
                                    )

                            # SKLAPANJE CELE KARTICE ZA JEDNU VALUTU
                            asset_card = ft.Container(
                                content=ft.Column([
                                    card_header,
                                    snapshot_box,
                                    ft.Container(height=5),
                                    days_row
                                ]),
                                padding=20, border_radius=15, bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.WHITE), border=ft.border.all(1, ft.Colors.with_opacity(0.05, ft.Colors.WHITE))
                            )
                            
                            tracker_list.controls.append(asset_card)
                            
                    else:
                        tracker_list.controls.append(ft.Text("Greška pri učitavanju praćenih valuta.", color=ft.Colors.RED))
        except Exception as e:
            tracker_list.controls.append(ft.Text(f"Dogodila se greška: {e}", color=ft.Colors.RED))

        page.update()



    # --- STRANICA: AI CHAT ---
    # --- STRANICA: AI CHAT (Verzija 1.0 - Stable) ---
    async def ucitaj_chat():
        main_content.controls.clear()
        
        # Koristimo najosnovnije parametre za Switch
        portfolio_switch = ft.Switch(
            label="Analiziraj moj portfolio", 
            value=False, 
            active_color="blue"
        )
        
        chat_messages = ft.ListView(
            expand=True, 
            spacing=10, 
            auto_scroll=True
        )

        # Inicijalna poruka asistentu
        istorija_poruka = [
            {"role": "system", "content": "Ti si stručni kripto analitičar. Odgovaraj na srpskom jeziku."}
        ]

        async def posalji_poruku(e):
            if not user_input.value: return
            
            pitanje = user_input.value
            user_input.value = ""
            kontekst_ukljucen = portfolio_switch.value
            
            # Korisnička poruka - jednostavan kontejner bez max_width
            chat_messages.controls.append(
                ft.Row([
                    ft.Container(
                        content=ft.Text(pitanje, color="white"),
                        bgcolor="blue800",
                        padding=10,
                        border_radius=10,
                    )
                ], alignment=ft.MainAxisAlignment.END)
            )
            
            loading = ft.Text("⏳ AI razmišlja...", italic=True, size=12, color=ft.Colors.GREY_400)
            chat_messages.controls.append(loading)
            page.update()

            istorija_poruka.append({"role": "user", "content": pitanje})

            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "messages": istorija_poruka,
                        "use_portfolio_context": kontekst_ukljucen
                    }
                    async with session.post(f"{API_BASE_URL}/api/chat", json=payload) as resp:
                        if loading in chat_messages.controls:
                            chat_messages.controls.remove(loading)
                        
                        if resp.status == 200:
                            res_data = await resp.json()
                            odgovor = res_data.get("response", "")
                            istorija_poruka.append({"role": "assistant", "content": odgovor})
                            
                            # AI poruka - bazična siva boja (grey900) umesto SURFACE_VARIANT
                            chat_messages.controls.append(
                                ft.Row([
                                    ft.Icon(ft.Icons.AUTO_AWESOME, color="amber", size=20),
                                    ft.Container(
                                        content=ft.Text(odgovor, selectable=True),
                                        bgcolor="grey900",
                                        padding=10,
                                        border_radius=10,
                                        expand=True
                                    )
                                ], vertical_alignment=ft.CrossAxisAlignment.START)
                            )
                        else:
                            chat_messages.controls.append(ft.Text("Greška na serveru.", color="red"))
            except Exception as ex:
                if loading in chat_messages.controls:
                    chat_messages.controls.remove(loading)
                chat_messages.controls.append(ft.Text(f"Greška: {str(ex)}", color="red"))
            
            page.update()

        user_input = ft.TextField(hint_text="Pitaj me...", expand=True, on_submit=posalji_poruku)
        send_btn = ft.IconButton(icon=ft.Icons.SEND, on_click=posalji_poruku)

        # 3. Elementi za unos (Donji deo ekrana)
        user_input = ft.TextField(
            hint_text="Pitaj me o kriptu...", 
            expand=True, 
            border_radius=25,
            content_padding=15,
            on_submit=posalji_poruku
        )
        
        send_btn = ft.FloatingActionButton(
            icon=ft.Icons.SEND_ROUNDED, 
            on_click=posalji_poruku,
            bgcolor=ft.Colors.BLUE_400,
            mini=True
        )

        # 4. Finalni Layout stranice
        main_content.controls = [
            ft.Row([
                ft.Text("AI Kripto Asistent", size=24, weight="bold"),
                ft.Icon(ft.Icons.FORUM_OUTLINED, color=ft.Colors.BLUE_200)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            
            # Switch postavljen lijevo
            ft.Row([portfolio_switch], alignment=ft.MainAxisAlignment.START),
            
            ft.Divider(height=1, thickness=1),
            
            # Glavni prozor za poruke
            ft.Container(
                content=chat_messages, 
                expand=True, 
                border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=10,
                padding=10,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.BLACK)
            ),
            
            ft.Container(height=10),
            
            # Red za unos poruke
            ft.Row([user_input, send_btn], spacing=10)
        ]
        page.update()

    # --- NAVIGACIJA ---
    async def change_page(e):
        idx = e.control.selected_index
        if idx == 0: 
            await ucitaj_dashboard()
        elif idx == 1: 
            await ucitaj_tracker()
        elif idx == 2: 
            # OVDE SADA POZIVAMO CHAT
            await ucitaj_chat()
        elif idx == 3:
            await ucitaj_analize()

    # --- STRANICA: FINANCIAL AGENT IZVEŠTAJI ---

    async def prikazi_detalje_analize(report):
        """Prikazuje pun tekst izveštaja."""
        main_content.controls.clear()

        asset_type = report.get("asset_type", "stock")
        type_colors = {
            "crypto": ft.Colors.ORANGE_400,
            "stock": ft.Colors.BLUE_400,
            "index": ft.Colors.PURPLE_400,
            "commodity": ft.Colors.AMBER_400,
        }
        badge_color = type_colors.get(asset_type, ft.Colors.GREY_400)

        header = ft.Row([
            ft.IconButton(
                ft.Icons.ARROW_BACK, icon_color=ft.Colors.GREY_300,
                on_click=lambda e: asyncio.create_task(ucitaj_analize())
            ),
            ft.Column([
                ft.Text(report.get("instrument_name", "Izveštaj"), size=18, weight="bold"),
                ft.Row([
                    ft.Container(
                        content=ft.Text(asset_type.upper(), size=10, weight="bold", color=badge_color),
                        bgcolor=ft.Colors.with_opacity(0.12, badge_color),
                        padding=ft.padding.symmetric(horizontal=8, vertical=3), border_radius=6
                    ),
                    ft.Text(report.get("created_at", ""), size=11, color=ft.Colors.GREY_400),
                ], spacing=8)
            ], spacing=4, expand=True),
        ], alignment=ft.MainAxisAlignment.START, spacing=8)

        price_row = ft.Row([
            ft.Text("Cena u vreme analize:", size=12, color=ft.Colors.GREY_400),
            ft.Text(f"${report.get('current_price', 0):,.4f}", size=13, weight="bold"),
            ft.Text(f"| AI: {report.get('ai_provider', '')}", size=11, color=ft.Colors.GREY_500),
        ], spacing=8)

        report_text = report.get("report_text", report.get("report", "Nema teksta izveštaja."))

        async def obrisi_i_nazad(e):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.delete(f"{API_BASE_URL}/api/analysis/{report.get('id')}") as resp:
                        if resp.status == 200:
                            await ucitaj_analize()
            except Exception as ex:
                print(f"Greška pri brisanju analize: {ex}")

        btn_obrisi = ft.OutlinedButton(
            content=ft.Row([
                ft.Icon(ft.Icons.DELETE_OUTLINE, size=16, color=ft.Colors.RED_400),
                ft.Text("Obriši izveštaj", color=ft.Colors.RED_400, size=13),
            ], tight=True),
            on_click=obrisi_i_nazad,
        )

        main_content.controls = [
            header,
            price_row,
            ft.Divider(),
            ft.Container(
                content=ft.Markdown(
                    report_text,
                    selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                    code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
                    expand=True,
                ),
                padding=10,
                bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.WHITE),
                border_radius=10,
                border=ft.border.all(1, ft.Colors.with_opacity(0.07, ft.Colors.WHITE)),
            ),
            ft.Container(height=10),
            ft.Row([btn_obrisi], alignment=ft.MainAxisAlignment.CENTER),
            ft.Container(height=30),
        ]
        page.update()

    async def ucitaj_analize():
        main_content.controls.clear()

        # --- VARIJABLE ZA DIJALOG "NOVA ANALIZA" ---
        dlg_loading = ft.Ref[ft.ProgressRing]()
        dlg_btn     = ft.Ref[ft.ElevatedButton]()

        dlg_instrument = ft.TextField(
            label="Instrument (npr. gold, NVDA, bitcoin, ^GSPC)",
            autofocus=True, border_radius=10,
        )
        dlg_type = ft.Dropdown(
            label="Tip instrumenta",
            value="crypto",
            options=[
                ft.dropdown.Option("crypto",    "Kriptovaluta"),
                ft.dropdown.Option("stock",     "Akcija (Stock)"),
                ft.dropdown.Option("index",     "Berzanski indeks"),
                ft.dropdown.Option("commodity", "Roba / Futures"),
            ],
        )
        dlg_ticker = ft.TextField(
            label="Ticker / ID (npr. bitcoin, NVDA, GC=F, ^GSPC)",
            border_radius=10,
        )
        dlg_status = ft.Text("", size=12, color=ft.Colors.AMBER_400)

        async def pokreni_analizu(e):
            instr  = (dlg_instrument.value or "").strip()
            ticker = (dlg_ticker.value or instr).strip()
            atype  = dlg_type.value or "crypto"

            if not instr:
                dlg_status.value = "⚠️ Unesi naziv instrumenta!"
                page.update()
                return

            dlg_status.value = "⏳ Analiza u toku (može potrajati 20-40s)..."
            if dlg_btn.current:
                dlg_btn.current.disabled = True
            page.update()

            try:
                async with aiohttp.ClientSession() as session:
                    payload = {"instrument_name": instr, "asset_type": atype, "ticker": ticker, "save": True}
                    async with session.post(
                        f"{API_BASE_URL}/api/analysis/run",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=120)
                    ) as resp:
                        if resp.status == 200:
                            dlg_status.value = "✅ Analiza sačuvana!"
                            page.update()
                            await asyncio.sleep(0.8)
                            nova_analiza_dlg.open = False
                            page.update()
                            await ucitaj_analize()
                            return
                        else:
                            err = await resp.text()
                            dlg_status.value = f"❌ Greška: {err[:120]}"
            except Exception as ex:
                dlg_status.value = f"❌ Mrežna greška: {ex}"

            if dlg_btn.current:
                dlg_btn.current.disabled = False
            page.update()

        nova_analiza_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("🧠 Nova Financial Agent Analiza"),
            content=ft.Column([
                dlg_type,
                ft.Container(height=8),
                dlg_instrument,
                ft.Container(height=8),
                dlg_ticker,
                ft.Container(height=8),
                dlg_status,
            ], tight=True, width=320),
            actions=[
                ft.TextButton("Otkaži", on_click=lambda e: setattr(nova_analiza_dlg, "open", False) or page.update()),
                ft.ElevatedButton(
                    "Pokreni analizu",
                    ref=dlg_btn,
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    bgcolor=ft.Colors.BLUE_700,
                    color=ft.Colors.WHITE,
                    on_click=lambda e: asyncio.create_task(pokreni_analizu(e)),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.overlay.append(nova_analiza_dlg)

        def otvori_dijalog(e):
            pref_type = dashboard_context.get("asset_type") or selected_asset_type["value"] or "crypto"
            dlg_type.value = pref_type
            dlg_instrument.value = dashboard_context.get("instrument_name", "")
            dlg_ticker.value = dashboard_context.get("ticker", "")
            dlg_status.value = ""
            nova_analiza_dlg.open = True
            page.update()

        # --- HEADER ---
        header = ft.Row([
            ft.Column([
                ft.Row([ft.Icon(ft.Icons.ANALYTICS_OUTLINED, color=ft.Colors.BLUE_400, size=22),
                        ft.Text("Financial Reports", size=22, weight="bold")], spacing=8),
                ft.Text("AI izveštaji sa tehničkim indikatorima", size=12, color=ft.Colors.GREY_400),
            ], spacing=2, expand=True),
            ft.FloatingActionButton(
                icon=ft.Icons.ADD,
                mini=True,
                bgcolor=ft.Colors.BLUE_700,
                on_click=otvori_dijalog,
                tooltip="Nova analiza",
            )
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        izvestaji_lista = ft.Column(spacing=12)
        izvestaji_lista.controls.append(
            ft.Row([ft.ProgressRing()], alignment=ft.MainAxisAlignment.CENTER)
        )

        main_content.controls = [
            header,
            ft.Container(height=15),
            izvestaji_lista,
        ]
        page.update()

        # --- UČITAVANJE IZVEŠTAJA IZ API-JA ---
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_BASE_URL}/api/analysis") as resp:
                    izvestaji_lista.controls.clear()
                    if resp.status == 200:
                        reports = await resp.json()
                        if not reports:
                            izvestaji_lista.controls.append(
                                ft.Container(
                                    content=ft.Column([
                                        ft.Icon(ft.Icons.ANALYTICS_OUTLINED, size=48, color=ft.Colors.GREY_700),
                                        ft.Text("Nema sačuvanih izveštaja.", color=ft.Colors.GREY_500,
                                                text_align=ft.TextAlign.CENTER),
                                        ft.Text("Klikni + da pokreneš prvu analizu.", size=12,
                                                color=ft.Colors.GREY_600, text_align=ft.TextAlign.CENTER),
                                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
                                    padding=40,
                                )
                            )
                        else:
                            type_colors = {
                                "crypto": ft.Colors.ORANGE_400,
                                "stock": ft.Colors.BLUE_400,
                                "index": ft.Colors.PURPLE_400,
                                "commodity": ft.Colors.AMBER_400,
                            }
                            for r in reports:
                                atype  = r.get("asset_type", "stock")
                                bcolor = type_colors.get(atype, ft.Colors.GREY_400)
                                card = ft.Container(
                                    content=ft.Column([
                                        ft.Row([
                                            ft.Column([
                                                ft.Text(r.get("instrument_name", "-"),
                                                        size=15, weight="bold"),
                                                ft.Row([
                                                    ft.Container(
                                                        content=ft.Text(atype.upper(), size=9,
                                                                        weight="bold", color=bcolor),
                                                        bgcolor=ft.Colors.with_opacity(0.12, bcolor),
                                                        padding=ft.padding.symmetric(horizontal=7, vertical=2),
                                                        border_radius=5,
                                                    ),
                                                    ft.Text(r.get("ticker", ""), size=11, color=ft.Colors.GREY_400),
                                                ], spacing=6),
                                            ], spacing=3, expand=True),
                                            ft.Column([
                                                ft.Text(f"${r.get('current_price', 0):,.4f}",
                                                        size=14, weight="bold"),
                                                ft.Text(r.get("created_at", ""), size=10,
                                                        color=ft.Colors.GREY_500),
                                            ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END),
                                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                        ft.Divider(height=6, color=ft.Colors.with_opacity(0.07, ft.Colors.WHITE)),
                                        ft.Text(
                                            r.get("preview", ""),
                                            size=12, color=ft.Colors.GREY_400, max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ], spacing=8),
                                    padding=16,
                                    border_radius=14,
                                    bgcolor=ft.Colors.with_opacity(0.03, ft.Colors.WHITE),
                                    border=ft.border.all(1, ft.Colors.with_opacity(0.07, ft.Colors.WHITE)),
                                    on_click=lambda e, rep=r: asyncio.create_task(
                                        _load_and_show_report(rep["id"])
                                    ),
                                )
                                izvestaji_lista.controls.append(card)
                    else:
                        izvestaji_lista.controls.append(
                            ft.Text("Greška pri učitavanju.", color=ft.Colors.RED)
                        )
        except Exception as ex:
            izvestaji_lista.controls.clear()
            izvestaji_lista.controls.append(ft.Text(f"Greška: {ex}", color=ft.Colors.RED))
        page.update()

    async def _load_and_show_report(report_id: int):
        """Preuzima kompletan izveštaj (sa punim tekstom) i otvara ga."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_BASE_URL}/api/analysis/{report_id}") as resp:
                    if resp.status == 200:
                        report = await resp.json()
                        await prikazi_detalje_analize(report)
        except Exception as ex:
            print(f"Greška pri učitavanju detalja analize: {ex}")

    page.navigation_bar = ft.NavigationBar(
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.HOME, label="Home"),
            ft.NavigationBarDestination(icon=ft.Icons.TRACK_CHANGES, label="Tracker"),
            ft.NavigationBarDestination(icon=ft.Icons.CHAT, label="Chat"),
            ft.NavigationBarDestination(icon=ft.Icons.ANALYTICS_OUTLINED, label="Reports"),
        ],
        on_change=change_page
    )

    page.add(main_content)
    await ucitaj_dashboard()

ft.run(main)