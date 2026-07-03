# Trading Stratejileri — Araştırma ve Yol Haritası

> Amaç: Botumuzun (`Strategy.on_candle(df, position) -> Signal`) içine takılabilecek,
> gerçek bir kenara (edge) dayanan stratejileri ve **kârlılığı artıran asıl kaldıraçları**
> derlemek. Bu bir kâr garantisi değildir; piyasa ücret + kayma (slippage) + funding
> maliyeti çıkarıldıktan sonra çoğu bot zarar eder. Edge çoğunlukla indikatörde değil,
> **risk yönetimi + rejim seçimi + disiplinli uygulamadadır.**

---

## 0) Önce gerçek: kâr nereden gelir?

Bir stratejinin beklenen getirisi kabaca:

```
Beklenti = (KazanmaOranı × OrtKazanç) − (KaybetmeOranı × OrtKayıp) − Maliyetler
Maliyetler = komisyon (taker ~%0.04) + funding + slippage
```

Sonuç: %55 isabetle bile, ortalama kazancın ortalama kaybından büyük olması (R>1) ve
**aşırı işlem yapmaman** gerekir. Günde 50 işlem yapan bir bot, komisyonla kendini
öldürür. Bu yüzden "daha çok sinyal" değil, **daha kaliteli ve daha az sinyal** hedefliyoruz.

---

## 1) Teknik vs Temel analiz — kriptoda kısa vade için

| Katman | Kısa-vade daytrading'e katkısı | Kaynak |
|---|---|---|
| **Teknik** (EMA, RSI, ATR, Bollinger, VWAP, ADX) | Giriş/çıkış zamanlaması, trend/range ayrımı. **Çekirdek.** | Mum verisi (var) |
| **Kripto-native** (funding rate, open interest, likidasyon) | Kalabalık pozisyon/sıkışma tespiti — hisse/forex'te olmayan edge. **Filtre olarak çok değerli.** | Binance API (var!) |
| **Makro/sentiment** (Fear&Greed, BTC dominansı, haber) | "Veto" filtresi: aşırı açgözlülükte long sinyalini zayıflat. | Ücretsiz API'ler |
| **On-chain** (borsa giriş/çıkış, whale akışı) | Saatlik+ vadede yön; daytrading'de ikincil. | Glassnode/CryptoQuant (ücretli) |

**Sonuç:** Çekirdek teknik + funding/OI filtresi + bir makro veto = bizim için en iyi
maliyet/fayda. On-chain'i şimdilik atlıyoruz (pahalı, kısa vadede zayıf).

---

## 2) Aday stratejiler (hepsi `Strategy` arayüzüne takılır)

### A. Trend-takip: EMA + ADX + MACD onayı  *(şu anki ema_rsi'nin güçlendirilmişi)*
- **Giriş (LONG):** EMA9 > EMA21 (yukarı kesişim) **VE** ADX > 25 (gerçek trend var)
  **VE** MACD histogramı pozitif.
- **Çıkış:** ters kesişim veya ATR-trailing stop.
- **En iyi rejim:** güçlü trend (BTC ralli, alt-season). **Zayıf:** yatay/choppy piyasa.
- **Neden ADX?** Tek başına EMA kesişimi yatay piyasada sürekli yanlış sinyal üretir;
  ADX bunu eler. **En büyük tek iyileştirme bu.**

### B. Ortalamaya dönüş (mean reversion): RSI + Bollinger + VWAP
- **Giriş (LONG):** fiyat alt Bollinger bandına değdi **VE** RSI < 25 **VE** fiyat VWAP altında.
- **Çıkış:** orta band (SMA20) veya VWAP'a dönüş; sıkı stop.
- **En iyi rejim:** yatay/range, yüksek volatil coinler. **Zayıf:** güçlü trend (banda
  yapışıp gider — "düşen bıçağı yakalama" riski).

### C. Kırılım (breakout): Donchian + hacim/OI onayı
- **Giriş:** fiyat son N mumun en yükseğini kırdı **VE** hacim ortalamanın üstünde
  **VE** open interest artıyor (yeni para giriyor, short-cover değil).
- **Çıkış:** ATR-trailing; başarısız kırılımda (geri dönüş) hızlı kes.
- **En iyi rejim:** sıkışma sonrası patlama. **Zayıf:** sahte kırılım (fakeout) çok.

### D. Kripto-native confluence filtresi *(strateji değil, üstüne giydirilen filtre)*
- **Funding rate:** çok pozitif → longlar shortlara ödüyor, piyasa long-ağırlıklı →
  **long sinyallerini zayıflat / shorta meyilli ol.** Çok negatif → tersi (short-squeeze).
- **Open interest:** fiyat↑ + OI↑ = sağlam trend (giriş onayı). Fiyat↑ + OI↓ = zayıf
  (short kapanışı), girişe güvenme.
- **Likidasyon kümeleri:** büyük likidasyonlar genelde lokal dip/tepe işaretler.

### E. ⭐ Rejim-anahtarlı meta-strateji *(asıl kârlılık kaldıracı)*
Tek strateji her piyasada çalışmaz. ADX ile rejimi ölç, uygun motoru seç:
- **ADX > 25 (trend):** A stratejisini çalıştır.
- **ADX < 20 (range):** B stratejisini çalıştır.
- **Arada / düşük güven:** işlem yapma (en kârlı işlem bazen yapılmayandır).
- Üstüne D filtresini ve makro vetoyu uygula.

Bu, araştırmadaki en tutarlı bulgu: **"adaptive multi-strategy, regime-gated"** yaklaşım
sahte sinyalleri ciddi azaltıyor.

---

## 3) Kârlılığı artıran ASIL kaldıraçlar (indikatörden önce gelir)

1. **ATR-bazlı dinamik stop/TP** (sabit % yerine): stop = giriş − 1.5×ATR. Volatiliteye
   uyum sağlar; gürültüye erken takılmaz.
2. **Asimetrik R/R:** sadece TP ≥ 1.5× stop mesafesi olan girişleri al. Düşük R'li
   sinyalleri ele.
3. **Rejim filtresi (yukarıda E):** choppy piyasada işlem yapmamak, tek başına en büyük
   iyileştirme.
4. **Çoklu zaman dilimi (MTF) hizası:** 5m girişi sadece 1h trend yönündeyse al.
5. **Funding/OI farkındalığı:** kalabalık tarafa girme; funding maliyetini hesaba kat.
6. **Aşırı işlemden kaçın:** cooldown + günde maksimum işlem sınırı (komisyon koruması).
7. **Walk-forward / out-of-sample test:** parametreyi geçmişe "fit" etme (overfit). Veriyi
   böl: bir kısımda optimize et, görmediği kısımda doğrula.
8. **Strateji portföyü:** korelasyonsuz 2-3 strateji birlikte → daha düz equity eğrisi.
9. **Maker emir tercihi:** mümkünse limit (maker, ~%0.02) ile gir, taker maliyetini düşür.
10. **Kill-switch + risk limitleri** (bizde var): büyük zararı baştan engeller.

> Not: 1, 2, 3, 6, 10 bizim `RiskManager`'a doğrudan eklenebilir; çoğu indikatörden daha
> çok kâr getirir.

---

## 4) Dış API'ler — neyi bağlamaya değer?

| API | Ne sağlar | Bize değeri | Öneri |
|---|---|---|---|
| **Binance** (var) | Futures emir + **funding rate + OI + likidasyon** | Hem execution hem kripto-native veri tek yerde | ✅ Yeterli; D katmanını buradan besle |
| **TradingView** | Gelişmiş grafik + **Pine Script alarm → webhook** | Stratejiyi Pine'da tasarlayıp webhook ile bota işlet; görselleştirme | ⭐ Güçlü opsiyon (aşağıda) |
| **Coinbase** | Spot fiyat/hacim, ABD-regüle | Futures için gereksiz; çapraz-borsa/yedek veri veya arbitraj için | ➖ Şimdilik gerek yok |
| **Alternative.me** | **Fear & Greed Index** (ücretsiz) | Makro veto filtresi (madde C/makro) | ✅ Ucuz, ekle |
| **CoinGlass** | Funding/OI/likidasyon aggregate (freemium) | Binance tek borsa; çoklu-borsa görünümü | ◻ İsteğe bağlı |
| **Glassnode/CryptoQuant** | On-chain | Kısa vadede zayıf, pahalı | ➖ Sonraya bırak |

### TradingView webhook modeli (ilgini çekerse)
TradingView'de Pine Script ile strateji yazıp **alarm** kurarsın; alarm bir **webhook**
ile bizim bota POST atar; bot emri Binance'de açar. Avantaj: TradingView'in zengin
indikatör/görsel ekosistemi + sen stratejiyi görsel test edersin. Bunun için bota küçük
bir `/webhook` endpoint'i eklemek yeterli (FastAPI'de zaten var, kolayca eklenir).

---

## 5) Önerilen yol haritası (bizim bota uygulama sırası)

1. **RiskManager'ı güçlendir:** ATR-bazlı stop/TP + min R/R filtresi + günlük max işlem.
   *(En yüksek getirili adım, indikatör değiştirmeden.)*
2. **ema_rsi → trend stratejisi A:** ADX + MACD onayı ekle (sahte kesişimleri ele).
3. **Mean-reversion stratejisi B** ekle (yeni modül, REGISTRY'e kaydet).
4. **Rejim-anahtarlı meta-strateji E:** ADX'e göre A/B seç; choppy'de dur.
5. **Funding/OI filtresi D:** Binance'den çek, giriş onayı/vetosu olarak kullan.
6. **Fear & Greed makro veto** (alternative.me).
7. **Walk-forward backtest:** mevcut backtester'ı eğitim/test bölmesiyle genişlet.
8. *(opsiyonel)* **TradingView webhook** endpoint'i.

Her adım testnet + dry-run'da backtest ile doğrulanır; ancak istikrarlı sonra canlıya.

---

### Kaynaklar
- QuantVPS — Top Trading Bot Strategies: https://www.quantvps.com/blog/trading-bot-strategies
- Bitsgap — Best Crypto Trading Bots 2026: https://bitsgap.com/blog/best-crypto-trading-bots-in-2026-strategies-types-and-how-to-choose
- Gate Wiki — OI & Funding Rate sinyalleri: https://web3.gate.com/crypto-wiki/article/how-do-futures-open-interest-and-funding-rates-signal-crypto-derivatives-market-trends-in-2026-20260202
- Artemis — BTC Regime-Gated strateji: https://research.artemis.ai/p/btc-regime-gated-alt-factor-strategy
- QuantJourney — Funding Rates: https://quantjourney.substack.com/p/funding-rates-in-crypto-the-hidden
- CoinXsight — TA indikatör rehberi: https://coinxsight.com/blog/strategy/crypto-technical-analysis-indicators
