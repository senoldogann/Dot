# Dot özerklik mimarisi incelemesi

Tarih: 2026-11-05. Ağaç: `main` (`35eba950` ve çevresi). Bu dosya bir incelemedir; davranış değiştirmez.

Kapsam: Dot’un bugünkü çalışma zamanı, ajan döngüsü, araçlar, MCP ve bağlayıcılar, bellek, güvenlik ve ölçüm. Amaç, güçlü bir masaüstü ajana (eklenti bağlama, kalıcı bellek, işi bitene kadar sürdürme) giden **en küçük** yol. Büyük bir yeniden yazım önermez.

Yöntem: `src/dotagent` altındaki modüller, `src/dotagent/defaults.toml` ve `docs/` sözleşmeleri okundu. Satır numaraları bu ağaca aittir; kayarsa sembol adı esastır. Canlı macOS (PyObjC, Keychain, Chrome, iMessage, launchd) bu ortamda çalıştırılmadı. `policy/engine.py` içindeki her dal satır satır yürütülmedi; karar sırası `decide` / `decide_past_review` gövdesinden özetlendi.

## Hüküm

Dot, ince bir sohbet kabuğu değildir. Tek bir Python daemon’u (`dotx`) SQLite üzerinde koşu tutar, modeli çağırır, her araç çağrısını bir politika kapısından geçirir, tarayıcıyı ve Mac uygulamalarını sürer, MCP sunucularını sürece bağlar, not belleği ve beceri metinleri taşır, takvim ve kampanya ile arka planda uyanır. Güçlü bir ajanda aranan parçaların çoğu **vardır ve kapalıdır ya da bağlayıcı değildir**.

Görünür özerkliği bugün sınırlayan dört şey:

1. **Goal mode varsayılanı kapalı** (`[agent] goal_mode = false`). Bitirme denetçisi, plan kanıtı ve “daha iyi sonuç varsa devam” ancak koşu bu bayrakla açıldıysa çalışır (`run.goal_mode`, `agent/loop.py`).
2. **Plan bir sözleşme değil, bir anımsatmadır.** Açık adımlar `finish` öncesinde yalnızca bir kez hatırlatılır (`plan_reminded`); sonra bitiş sürer.
3. **MCP bağlanır, platform olmaz.** Kurulum, OAuth, `list_tools` ve çağrı vardır. Kaynak (`resources`) ve istem (`prompts`) yoktur. Galeri yalnızca beceri kurar. Eklentinin MCP süreçleri daemon yeniden başlayınca ayağa kalkar. Şeması düzleştirilemeyen tek araç günlükte vardır, ekranda yoktur: `usable_tools` `mcp_tool_left_out` yazar; `dotx doctor` ve arayüz bu satırı göstermez. “Left out” bütün sunucu düşünce görünür.
4. **Bellek nottur, bölüm (episode) değildir.** Kısa vadeli bağlam sıkıştırılır ve bir defter (`RunLedger`) ile sabitlenir. Uzun vadeli bellek etiketli notlardır. Geçmiş koşuların özetleri anlama göre aranmaz. Beceriler `SKILL.md` metnidir; çalışan bir iş akışı motoru yoktur.

Güvenlik katmanı bu dört maddeden daha olgundur. Özerklik, `yolo` açarak veya politika motorunu incelterek değil, mevcut döngünün varsayılanını ve sözleşmesini sıkılaştırarak büyür.

## 1. Mimari genel bakış

```text
dotx run | POST /api/runs | Scheduler._start | kanal | inisiyatif
        → RunManager._drive
        → run_agent (agent/loop.py)     veya  run_in_opencode (OpenCode modeli)
             while True:
               gelen kutu → bağlam sığdır → model → execute_calls → finish?
        → politika: prepare → Gate → ApprovalBroker
        → Store (SQLite) + EventBus
```

Web arayüzü ve Tauri kabuğu aynı daemon’a konuşur. Ajan döngüsü tarayıcıda değildir.

### Çalışma zamanı

`build_runtime` / `open_runtime` (`src/dotagent/runtime.py`) uzun ömürlü `Runtime` kurar: `Store`, `EventBus`, `ApprovalBroker`, `ModelRouter`, `ToolRegistry`, tarayıcı, masaüstü, `McpHub`. Araç grupları `_tool_groups` ile, MCP araçları `build_mcp_tools(mcp)` ile aynı kayıt defterine girer.

Daemon `run_daemon` (`src/dotagent/server/daemon.py`): FastAPI, `RunManager`, `Scheduler`, kanallar, isteğe bağlı izleyiciler, açılışta `manager.recover()`. HTTP başlangıcı `POST /api/runs` (`src/dotagent/server/routes_runs.py`). CLI `src/dotagent/cli_run.py` aynı `RunManager.start` yolunu kullanır.

OpenCode sağlayıcısındaki modeller Dot döngüsünü atlar (`RunManager._drive` içinde `run_in_opencode`). Bu koşular Dot’un plan, goal check ve araç kapısı sözleşmesinin dışındadır. Harness de bunlardan öğrenmemeyi seçer (`harness` içinde `learns_from`).

Süreç dışı gözcü `src/dotagent/watchdog.py`: launchd ile yaklaşık dakikada bir, daemon sağlığı ve takılı koşu (`POST /api/runs/{id}/stuck`). Koşu içi “ilerliyor mu” gözcüsü ayrıdır: `src/dotagent/agent/progress.py`.

### Ajan döngüsü

Ana fonksiyon `run_agent` (`src/dotagent/agent/loop.py`, yaklaşık satır 2147). Adım döngüsü `while True` (yaklaşık 2543).

Bir adımda kabaca şunlar olur:

- İptal, yönlendirme, bekletme, gelen kutu.
- Koşu sırasında değişen bellek notları ve uçbirim art alanı.
- Limit aşımında araç kümesi daralır.
- `plan_each_turn` açıksa (varsayılan **açık**) açık plan notu, sohbet olmayan koşuya eklenir.
- `_fit_context`: eski görüntüler düşer, eski araç sonuçları kısalır, sığmazsa özet (`agent/compact.py`, `agent/continuity.py`).
- İsteğe bağlı adım yönlendirme (küçük model).
- Model çağrısı, kota ve erişim hatalarında bekleme ve yedek model.
- `execute_calls` (`src/dotagent/agent/executor.py`): `prepare` → kapı → zaman aşımı → sonuç. `finish` son çağrı olmak zorundadır. `parallel_safe` araçlar üst üste binebilir.
- Metinle biten yanıt, `finish` araç çağrısına doğru dürtülür.
- Başarılı `finish` öncesi: başarısız bitiş, açık plan adımları (bir kez), kampanya hedefi, `verify_answer_paths`, sonra goal mode ise goal check.
- Goal check bitişi kabul etse bile gelen kutuda soru varsa döngü sürer.

Plan araçları `src/dotagent/tools/meta.py` (`update_plan`, `mark_plan_step`). Birden fazla adımın brifingi ve bağımlılığı uygunsa `src/dotagent/tools/fan_out.py` alt koşular açar (`delegate`, tavan `[agent] fan_out`). Ayrı bir planlayıcı süreci yoktur. Ayrıştırmayı model yapar; izleme olay ve adım listesidir.

### Araçlar

Sözleşme `src/dotagent/tools/base.py`: `ToolArgs`, `ToolResult`, `PreparedCall`, `RegisteredTool`. Kayıt `src/dotagent/tools/registry.py` (`build_registry`; ad çakışması `ConfigError`). Modlar `task`, `chat`, `research`.

Yerel yüzey (tam liste değil): dosya (`tools/files.py`), kabuk (`tools/shell.py`), tarayıcı (`tools/browser.py`), web (`tools/web.py`), bellek, masaüstü, beceri, takvim ve posta, takvimleme, delege, ofis, sayfalar, uçbirim, Apple, ticaret. MCP araçları `mcp__<sunucu>__<araç>` adıyla aynı deftere yazılır (`tools/mcp.py`, `mcp/naming.py`).

`[agent] tool_surface` varsayılanı `"full"` (`defaults.toml`): her adımda bütün araçlar. `"grouped"` çekirdek ve daha önce açılmış grupları tutar, gerisini `use_tools` ile açar. `"auto"` dar pencereli modellerde gruplar. Gruplar `tools/groups.py`.

### Arayüz

Web: `src/dotagent/server/static/`. Derleme adımı yok; düz JavaScript modülleri ve CSS. Görünümler `js/views/` (koşu, takvim, kampanya, kanal, bağlayıcı, eklenti, harness, ayarlar, bellek yolu ayarlar içinde). Goal mode seçicisi `js/runmodes.js` içinde `goalModePicker`; sohbet modunda gizlenir. Üç boyutlu ofis `js/office/` altındadır; özerklik yolu değildir.

Masaüstü: `desktop/` Tauri 2 kabuğu (Rust `src-tauri`). Yetenek daemon’dadır. Telefon düzeni `docs/phone.md` ve aynı statik arayüz.

### Depolama

SQLite `src/dotagent/store/db.py` (`store/models.py`): koşular, mesajlar, olaylar, onaylar, bellek notları ve vektörler, kurallar. Yedek `store/backup.py`. Geri alma günlüğü `src/dotagent/undo/`. Yollar sahibin Dot evine gider (`DOT_HOME` testlerde ayrı tutulur). Gömme modeli değişince vektörler düşer (`memory/service.py`).

Olaylar `src/dotagent/events.py` (`EventBus` → `Store.add_event`, SSE ve CLI aboneleri). Bitiş sınıfları `store/endings.py`. `dotx stats` `store/stats.py` okur.

## 2. Özerklik boşlukları

Karşılaştırma: işi kendisi bölen, adımları bitene kadar süren, arka planda uyanan, iddiayı kanıtla denetleyen, takılınca başka yola geçen bir masaüstü ajan.

### Planlama

Bugün: model `update_plan` yazar; `plan_each_turn = true` (değişince ve kabaca her 20 adımda) planı yeniden koyar. `fan_out` uygun adımları paralel alt koşuya verir. Kampanya (`docs/agents-and-campaigns.md`, `RunManager.start_campaign`) işi oturumlara böler. “Keep going” besteci seçeneği goal mode değildir; kampanyadır (`docs/autonomy.md`).

Boşluk: plan, döngünün dışındaki bir grafik olarak yürütülmez. Açık adım varken `finish` bir kez not düşer ve `plan_reminded = True` olur (`agent/loop.py`, yaklaşık 3101–3108). İkinci `finish` açık adımlarla geçebilir. Goal mode kapalıysa goal check bu adımların kanıtsız “bitti” işaretini de sorgulamaz.

### Çok adımlı yürütme

Döngünün adım tavanı yoktur. Tavanlar: `[agent] run_max_minutes = 120`, `run_max_tokens = 10_000_000`, `stall_limit = 4`. `stall_escalation = false`. İlerleme gözcüsü (`agent/progress.py`) tekrar, döngü, başarısızlık ve sapmayı görür; merdiven varsayılan olarak durdurmaya zorlamaz.

Delegasyon ve fan-out vardır. Alt koşu, üst koşunun onay kipini alır; kendi goal mode’u yoktur (`docs/autonomy.md`).

### Arka plan

`Scheduler` (`src/dotagent/scheduler/runner.py`): `tick` → `_fire_due` → `RunManager.start_firing`. Yansıma, harness adımı, eklenti keşfi, onarım gecesi burada uyanır. Kampanya sonraki oturumu önceki ipliğe ve `carried` koşu kimliklerine bağlar (`agent/manager.py`).

İnisiyatif `src/dotagent/proactive/initiative.py`: Takvimler açmadan çalışmaz. Acil durdurma, başka bir koşu, Mac boşta değil, günlük tavan ve aralık engeller. Açılınca onay kipi `yolo` (`[initiative]`, `defaults.toml`). Kirli yapılacak işlerde gönderim, ödeme ve gönderi yine inceleme modeline gider. Bunu “özerklik” diye varsayılan açmak yanlış kapıdır.

İzleyiciler `[watchers] enabled = true`. Uyanık tutma `src/dotagent/awake.py`.

Sınır (`docs/limits.md` ile uyumlu, bu ortamda ölçülmedi): takvim ancak daemon ayaktayken ve Mac uyanıkken ateşlenir. Kanal kuyruğu, süren koşunun arkasına girer.

### Doğrulama

Goal mode açıkken `GoalChecker` (`src/dotagent/agent/review.py`) `finish` yanıtını hedefin kanıtına göre okur: araç özeti, dosya araçlarının yazdığı yollar, aynı çağrının sonradan düzelmediği hatalar, test ve derleme komutlarının son sonucu, yanıtın adlandırıp hiç dokunulmamış yollar. Hüküm: `achieved`, `continue` (en fazla `goal_patience = 4`), `blocked`, `scheduled`. Planlı adımın kanıtsız “bitti”si `unproven_steps` olur. Denetçi iş eklemez.

Ayrıca: belirli adım aralığında `CheckpointJudge`; TypeSafe `src/dotagent/agent/judge.py` (tıklama, gönderi, kampanya hedef iddiası; nihai hedef kanıtının yerine geçmez); `verify_answer_paths`.

Boşluk: kod işlerinde “taze bir test komutu defterde yoksa bitiş yok” diye ayrı bir motor gerekmez; bu kanıt zaten denetçiye gider. Denetçi goal mode kapalıyken uyur. Denetçi bir modeldir; model susarsa koşu başarısız biter, kullanıcıya düşmez (`docs/autonomy.md`, inceleme modeli için aynı “yedek yok” kuralı).

### Toparlanma

Sağlananlar: daemon `recover()` yarım koşuları ve bayat onayları kapatır; ölü `dotx` süreçleri `watch_commands`; model kotasında bekleme ve yedek model; adım ortasında yönlendirme çağrıyı keser; acil STOP dosyası; `dotx undo` ve rewind; onayda koşu `blocked`. Daemon yeniden başlayınca veya kapanınca kesilen koşu `src/dotagent/agent/resume.py` ile aynı ipliğe döner: `resume_note` hedefi, kesilen adımdaki çağrıları ve planı yeni koşunun ilk mesajına koyar. Bu not `blocked`, başarısız biten veya sahibin “devam” dediği koşuyu kapsamaz (`can_resume` yalnızca `cut_off_by` ile kesileni alır).

`[policy] unanswered = "refuse"`. Kimsenin bakmadığı koşuda (takvim, `yolo`, eval) `ask_user` varsayılan olarak beklemeyi `leave` ile geçer veya reddedilir; `park` ancak ayar değişince (`agent/parking.py`). Takvimli bir soru, sahip dönene kadar saklanmaz.

Sıkıştırma `RunLedger` ile hedefi, son araçları ve kimlikleri nota gömer (`agent/continuity.py`). `tools/recall.py` bu kimliklerden kayıt okur. Bölüm araması yoktur: “geçen salı ne oldu” not veya hatırlanan kimlik kadar vardır.

## 3. Eklenti, MCP, bağlayıcı

Üç ayrı şey aynı “Customize” sayfasında durur. Karıştırmak yol haritasını şişirir.

| Katman | Kod | Modele ne verir | Kurulum |
| --- | --- | --- | --- |
| Beceri eklentisi | `src/dotagent/plugins/` | `skill` aracıyla okunan `SKILL.md` | Galeri veya disk. MCP’si ancak daemon başlarken |
| Bağlayıcı kataloğu | `src/dotagent/connections/` + `mcp/catalogue.py` | Hizmeti kurma adımları | Arayüz ve `[[mcp]]` |
| MCP çalışma zamanı | `src/dotagent/mcp/` | Her araç bir `RegisteredTool` | `open_hub`, OAuth, sırlar |
| Hesap API’si | `src/dotagent/connectors/` + `src/dotagent/oauth/` | Posta ve takvim araçları | Google / Microsoft, Keychain |

### Bugün çalışanlar

- **Galeri** (`plugins/gallery.py`): sabit commit, hash, yalnızca beceri klasörleri. Dokümantasyon (`docs/extending.md`) 22 eklenti ve 201 beceri der. Kanca, komut, alt ajan ve MCP alınmaz (dosya başı, `gallery.py`).
- **Keşif** (`plugins/scout.py`): gece önerir, kurmaz. Lisans, yaş, yıldız, tarama, inceleme modeli. ClawHub okunmaz. MCP sunucuları aranmaz; `docs/extending.md` bunu açık yazar (sunucu bu Mac’te program çalıştırır).
- **PluginShelf** (`plugins/catalog.py`, yaklaşık 210–213): beceriler daemon ayaktayken yenilenir. Eklentinin MCP sunucuları “Dot yeniden başlayana kadar” kalkmaz.
- **Katalog**: `MCP_SERVICES` içinde 142 `McpService` (`mcp/catalogue.py`). Bağlayıcı sayfası `PLUGINS` içinde 156 `PluginListing` (`connections/directory.py`). Liste, hepsinin tek tıkla eklenebildiği anlamına gelmez; katalog başı bilinçli dışlamaları yazar.
- **Bağlantı** (`mcp/hub.py`, `mcp/connection.py`): stdio, streamable HTTP, SSE. `list_tools` sayfalar. `select_tools` (`allow`). `usable_tools` şeması düzleşmeyen aracı tek başına bırakır ve `mcp_tool_left_out` ile günlüğe yazar; sunucunun diğer araçları durur. `allow` listesindeki bir araç veya kullanılabilir aracı kalmayan sunucu bütün sunucuyu düşürür.
- **Kimlik**: uzak `oauth: true` sunucularda OAuth 2.1 + PKCE (`mcp/signin.py`), sahip bağlayıcı sayfasında. Koşu içi etkileşimli giriş yok (`kept_sign_in`). stdio sırları Keychain ve ortam (`mcp/launch.py`). Google ve Microsoft resmi jetonları ayrı paket: `oauth/`.
- **Çağrı**: `McpHub.call` → araç sonucu çitlenir, büyük yanıt çalışma alanına yazılır (`tools/mcp_result.py`, `tools/third_party.py` `mcp-results/`). Politika `classify_mcp_call` (`tools/mcp.py`): okuma ile yazma, yıkıcı, dışarı gönderim. İleti gönderen ad, sunucu “readOnly” dese de gönderim sayılır.
- **Sıcak yenileme**: bağlayıcı eklenince `routes_mcp.py` hub’ı günceller ve `refresh_mcp_tools` kayıt defterini yeniler. Bu, **eklenti dizininden** gelen MCP için geçerli değildir (yukarıdaki shelf).
- **CLI**: `src/dotagent/cli_mcp.py` sunucuları yoklar ve politika ipucunu basar.

`list_resources` ve `list_prompts` bu depoda `src/dotagent/mcp` altında yoktur. Ajana kaynak okuma veya MCP istem şablonu aracı verilmez.

### Eksik olan, kurulumu engelleyenler

1. Galeriden veya ajanın kendi kararıyla keyfi MCP paketi kurulmaz. Katalog `npx` / `uvx` pinler.
2. Eklenti MCP’si beceriyle birlikte sıcak kalkmaz.
3. Şeması yüzünden bırakılan tek araç günlükte vardır, ekranda yoktur. `usable_tools` `mcp_tool_left_out` yazar (`mcp/hub.py`). `dotx doctor` (`doctor.py` `mcp_checks`) sunucunun komutunu ve sırlarını yoklar, bu aracı listelemez; sunucuya da bağlanmaz. Arayüzdeki “Left out” (`routes_mcp.py` içinde `state = "left_out"`, `js/views/connectors.js`) bütün sunucu `hub.connected()` dışında kalınca görünür, tek araç için değil. Bırakılan aracın adı hizmetin araç listesinde de yoktur: liste yalnızca kalan araçları sayar.
4. Kaynak ve istem protokolleri yok. Birçok sunucunun asıl değeri araçtan çok kaynaktadır.
5. Çok sunucu bağlanınca `tool_surface = full` araç listesini her adıma koyar. Gruplama kodu vardır, eşik yoktur.
6. Koşu, OAuth bitiremez. Sunucu imzasızsa koşu onu yok sayar; sahip bağlayıcıya dönmelidir. Bu doğrudur. Eksik olan, koşunun “bu sunucu imzasız” diye tek bir yapılandırılmış durması ve sonraki oturumun imzadan sonra aynı hedefe devam etmesidir (bölüm 2’deki el değiştirme ile birlikte).

## 4. Bellek ve bağlam

### Kısa vade

İplik mesajları SQLite’tadır. `_fit_context` bütçeyi aşınca hızlı model süreklilik notu yazar; olmazsa mekanik özet. `check_continuity` `GOAL`, `DONE`, `STATE`, `DECISIONS`, `PROBLEMS`, `NEXT` bölümlerini arar. `RunLedger` modelin uyduramayacağı kimlikleri nota sabitler. Sistem istemi sohbet boyunca aynı bayt kalır; anımsanan notlar kullanıcı mesajındaki `turn_note` içindedir (`agent/prompt.py`). Amaç istem önbelleğidir (`docs/memory-and-schedules.md`).

### Uzun vade

Paket `src/dotagent/memory/`. `prompt_memories` (`memory/service.py`, yaklaşık 525) koşu başında not seçer. Bütçe: pencerenin `inject_share` kadarı, tavan `inject_tokens` (belgelerde %2 ve 1500). Sıra: sabit `core` ve sahibin notları, sonra ajana ait dersler. Arama BM25 + kosinüs, sıra birleştirmesi (`memory/ranking.py`). Kullanım skoru ve dinlenme (`memory/lifecycle.py`). Dış içerik okurken kaydedilen not kirli işaretlenir; aramak koşuyu yeniden kirletir.

Gece yansıması `src/dotagent/agent/reflection.py`: ders, dinlenme, birleştirme, çöp, günlük, “senin hakkında” bölümü. Öğrenilen metin tek kapıdan geçer: `src/dotagent/core/learned_text.py` (sır, bağlantı, ev yolu, hesap, istem başlığı gibi görünen satır düşer). Sahibin kendi satırları yeniden yazılmaz.

Araçlar: `memory_save`, `memory_search`, `memory_update`, `memory_forget` (`tools/memory.py`). CLI `cli_memory.py`. Arayüz Ayarlar › Bellek.

### Beceriler ve iş akışları

`src/dotagent/skills/`: köklerde ve eklentilerde `SKILL.md`. Modele ad ve kısa açıklama listelenir; gövde `skill` aracıyla okunur (`tools/skill.py`). Öğrenilen oyun kitabı başarılı görevden sonra inceleme modelinin taslağıdır (`agent/playbooks.py`, `learned-<tür>/SKILL.md`). Sahibin öğrettiği `taught-` (`tools/skill_save.py`); kirli sohbette kapalıdır. Dinlenen beceriler sunulmaz.

Bunlar yordam metnidir. Okununca döngü bir kontrol listesini plan adımına bağlamak zorunda değildir.

### Harness

`src/dotagent/harness/`: benimsenen metin yalnızca alışkanlıklar ve araç rehberi (`harness/components.py`). Sistem istemine `text_for_run` ile girer. Adım `harness/step.py`: kötü koşu, aday metin, eval görevlerinde yoklama, bütçe, sahip veya `[harness] adopt`. Varsayılan `[harness] step_time = "off"`. Deneme kolu `harness/trial.py`.

Harness politika, araç şeması veya çekirdek istemi yazmaz. Geceyi açmak özerkliği bir anda büyütmez. Aday metin eval’de kazanınca ve sahip görünce anlamlıdır.

### Güçlendirmek (yeni depo değil)

- Süreklilik notlarını (ve kampanya `carried` özetlerini) bellek gömmesiyle aranabilir yapmak. Ayrı vektör veritabanı gerekmez; not tablosunun yanına bölüm kaydı yeter.
- `skill` okununca, becerideki kontrol listesi varsa mevcut `update_plan` ile yazılsın. Yürütme yine döngü ve politika.
- Çok günlük plan için yeni bir devir yazılmaz. `resume.py` içindeki `resume_note` kesilen koşuya hedefi, son adımdaki çağrıları ve planı zaten geri verir; P0-C bu notu `blocked`, başarısız ve sahibin “devam” dediği hallere, bir de sıkıştırma sonrasına uzatır. Kampanya `carried` yolunu bozmaz.

Varlık grafı (kişi, proje, dosya ilişkisi) ikinci önceliktir. Not etiketleri ve mağaza kimlikleri bugünkü sorguların çoğunu taşır. Graf, bölüm araması yokken erken bir soyutlamadır.

## 5. Araçlar ve bütünleşmeler

| Yetenek | Bugün | Güçlü ajan için duran iş |
| --- | --- | --- |
| Kabuk | `tools/shell.py`, koruma `policy/shell.py`, kum havuzu `policy/sandbox.py` | Koruma bilerek eksiktir (`docs/limits.md`). Özerklik, korumayı gevşetmek değildir |
| Dosya | `tools/files.py`, mühürlü yollar `policy/paths.py` | Yeter. Undo günlüğü OpenCode yazılarını görmez (`docs/autonomy.md`) |
| Tarayıcı | `browser_open`, `browser_snapshot`, `browser_act`, `browser_eval`, `browser_upload`, oturum açma araçları (`tools/browser.py`) | Kanıtlı bitiş için goal mode (P0-A). Yeni tarayıcı yığını yok |
| Masaüstü | `desktop_apps`, `desktop_open_app`, `desktop_snapshot`, `desktop_act`, pano (`tools/desktop.py`) | Parola yöneticisi ve Keychain Access her kipte uzak durur |
| Web | `web_fetch`, `web_search`, `web_crawl`, `web_extract`; gömülü `web-research` MCP (`mcp/builtin.py`) | Kirli içerik işareti doğru. Kaynak MCP ile karışmamalı |
| Posta ve takvim | `tools/mail_calendar.py`, `connectors/google.py`, `connectors/graph.py`, `oauth/` | Gönderim politika sorusudur. Yeni sağlayıcı ancak sahip isteyince |
| Sohbet kanalı | Yalnızca iMessage (`channels/types.py`, `channels/router.py`) | Onay ve soru kanaldan yanıtlanmaz. İkinci kanal, MCP mesajlaşması yetmezse |
| Kaynak kontrolü | `shell`. GitHub katalog bağlayıcısı. `repair/night.py` Dot’un kendi deposuna, izole checkout’ta, Claude Code ile PR açar | Genel “PR aç” aracı yoktur. İlk ihtiyaç, şeması düzleşmeyen aracın günlükteki `mcp_tool_left_out` satırının doctor ve hizmet durumunda da görünmesidir (P0-D), özel bir git çerçevesi değil |
| Ofis | `tools/office.py`, `office.py` | Word ve Excel becerileri `skills/builtin/`. Özerklik kapısı değil |
| Ticaret | `src/dotagent/trading/`, `tools/trading.py` | Ayrı sınırlar (`docs/trading.md`). Bu incelemenin yolu değil |
| Ses | `src/dotagent/voice/` | Kabuk ve metin döngüsüne ek |
| Kasa | `src/dotagent/vault/` | Giriş doldurma her kipte sahibindir (`docs/autonomy.md`) |

`repair/` geceyi: günlük ve koşulardan bir problem defteri, Claude Code, yeşil kontrolde isteğe bağlı birleştirme. İnsan bakışı isteyen yollara ve “NO FIX” sınıfına dokunmaz (`docs/repair.md`). Bunu genel ajan yürütmesi sanmamak gerekir.

## 6. Güvenlik ve insan kapısı

Sözleşme `docs/safety.md`. Kod `src/dotagent/policy/` (ayrı bir `safety` paketi yok). Motor `policy/engine.py` yaklaşık 426 satırdır. Giriş `decide` (yaklaşık 319), ardından `decide_past_review` (yaklaşık 336). Gövde sırası kabaca: otomatik inceleme, kip kapısı, Dot’un kendi dosyaları, sert duruş, kural ve korumalar (kabuk, kasa, ticaret), harcama payı, önayar ve kir. Ayrıntılı dal okuması bu incelemenin yerine geçmez; davranış iddiası için `docs/safety.md` ve `tests/smoke/test_approval_modes.py` esastır.

Kipler `policy/types.py`: `supervised`, `ask` (varsayılan), `auto`, `yolo`. Önayar varsayılanı `aggressive`. `yolo_taint_check = true`.

Kirli koşuda dışarı sır sızdıran adım her kipte, `yolo` dahil, reddedilir (`policy/egress.py`). Pace sert sınırdır (`policy/pace.py`). Kum havuzu yolo’da kalkar (`tools/context.py`). Kabuk koruması ad çözümlemesidir; gizlenmiş komut ve Apple Event kaçağı belgelerde açık sınırdır (`policy/sandbox.py`, `docs/limits.md`).

İnsan kapısı `src/dotagent/agent/approvals.py`: `request` koşuyu `blocked` yapar; `record_automatic` inceleme modeli ve yolo için kayıt düşer (`decided_by`). Park `agent/parking.py` ve `[policy] unanswered`. Sohbet izni `policy/chat_permits.py`. Sahibin kuralları `policy/drafting.py` (izin, ön onay, sor, devret; kural reddetmez).

Tutulması gerekenler:

- Sonuç doğuran iş (ödeme, sahip adına ileti ve posta, herkese açık gönderi, kasa girişi, Dot’un kendi dosyası) `yolo` ile bile kısmen veya tamamen sahibindedir. Ayrıntı `docs/autonomy.md`.
- Galeri ve keşif kendiliğinden kurmaz.
- Öğrenilen metin kapısı harness, sahip bölümü ve beceride ortaktır.
- İnceleme modeli başarısız olunca soru sahibe düşmez; araç çağrısı hata döner ve koşu sürer. Bu, sessiz onay değildir. Özerk koşuda sahip bunu `approval_auto` ve hata sonucundan okur.

Özerklik önerisi bu kapıyı inceltmez. `approvals` varsayılanı `ask` kalır. İnisiyatif `yolo` kalır ve kapalı kalır; açmak ayrı bir sahip kararıdır.

## 7. Güvenilirlik, gözlemlenebilirlik, ölçüm

### Gözlem

Her politika duruşu ve otomatik karar olaydır (`approval_requested`, `approval_decided`, `approval_auto`, `goal_check`, `plan_reminder`, `embedding_failed` ve diğerleri; türler `store/models.py` ve döngüdeki `emit` çağrıları). `dotx doctor` (`src/dotagent/doctor.py`, `cli_doctor.py`). `dotx stats`. Yapılandırılmış günlük `core/logs.py`. Bitiş nedeni `store/endings.py` (takılma ayrı sınıf).

Sahibin “neden kendi başına bitti” sorusunun yanıtı olay günlüğündedir. Arayüz, ayar kapalıyken inceleme modelinin satırlarını sohbetten gizler; kayıt durur (`docs/autonomy.md`).

### Ölçüm

`src/dotagent/evals/`, CLI `src/dotagent/cli_eval.py`.

- `dotx eval check`: her `EvalTask` (`evals/catalog.py`) için statik denetim, `replay_reference` (model yok, gerçek döngü ve araçlar), `do_nothing_passes` (boş durarak geçmemeli). `cmd_eval_check` “No model is called” der.
- `dotx eval run`: gerçek model, süre ve jeton bütçesi, JSON rapor (`evals/scoring.py`, `evals/metrics.py`): kategori geçiş oranı, takılma, sahte başarı, yanıtsız soru, enjeksiyon uyumu, sıkıştırma, araç hatası, goal check’te kanıtsız adım.
- `dotx eval tools`: araç çağrısı vakaları, tam görev değil.

Testler: `tests/unit` (model ve ağ yok), `tests/smoke` (`live`, `browser`, `internet`, `desktop`, `hosted` işaretleri). Kapı `.github/workflows/gate.yml` ve `CLAUDE.md`.

Ölçülmeyenler (bilinçli): tam macOS kum havuzu kaçağı, gizlenmiş kabuk, uyuyan Mac’te takvim, gerçek Chrome ve Keychain, yüzlerce MCP aracı, kampanyanın canlı model ile çok oturumu, `eval check` dışında uzun özerk görev. Eval kaynağı park ve insan kapısını üretimdeki gibi yaşatmaz. Harness denemesi güvenlik regresyonu değildir.

Uzun koşu riski: 120 dakika ve 10 milyon jeton tavanı vardır, `stall_escalation` kapalıdır, `unanswered = refuse` sahibi yokken işi düşürür, olay kuyruğu taşınca abone geride kalabilir (`events.py`; tam eşik bu metinde sayı olarak kilitlenmedi). Mağaza büyümesi elle budanır (`cli_prune.py`).

## 8. Yol haritası

Karmaşıklık: **S** bir iki modül, mevcut sözleşme, birim test. **M** birkaç modül, davranış değişir, `docs/` sayfası ve `dotx eval check`. **L** yeni alt sistem. Takvim tahmini yok. Her dilim kendi `Item N` işi olarak iner (`CLAUDE.md`); bu dosya bir item değildir.

Başarı ölçütü, sahip bir görev yazınca gördüğü davranış ve onu kilitleyen testtir.

### P0 — görünür özerklik, yeni çerçeve yok

**P0-A. Görev ve araştırma koşuları goal mode ile başlasın.** Karmaşıklık **S**.

Dokunulacaklar: `src/dotagent/defaults.toml` `[agent] goal_mode`, besteci varsayılanı (`server/static/js/runmodes.js` ve onu çağıran koşu görünümü), bu varsayılanı kilitleyen birim veya duman testi, `docs/autonomy.md`.

Sohbet goal mode almaz; döngü bunu zaten reddeder. `--no-goal-mode` ve besteciden kapatmak durur. `approvals` `ask` kalır. `goal_patience` 4 kalır.

Başarı: yeni `task` koşusunda `run.goal_mode` doğrudur. Erken `finish`, denetçi `continue` derse işe döner. Sohbet ve açık kapalı bayrak goal check çalıştırmaz. `uv run dotx eval check` geçer.

**P0-B. Açık plan adımı bitişi bir anımsatmayla geçmesin.** Karmaşıklık **S**.

Dokunulacaklar: `agent/loop.py` içindeki `plan_reminded` dalı (yaklaşık 3101–3108), gerekirse `tools/meta.py` / `agent/plan.py` yalnızca mevcut adım kapanmıyorsa. Yeni plan motoru yok.

Döngü, sohbet dışında, `open_steps` boşalmadan `finish` ile çıkmasın. Adım bugünkü plan araçlarıyla kapanır veya gerekçeyle bırakılır. Sonsuz döngü olmasın: anımsatma sayısı `stall_limit` veya küçük bir sabit ile sınırlı kalsın, sonra mevcut ilerleme merdiveni veya goal check devralsın. `plan_reminded` tek atımlık kalmasın.

Başarı: birim testi, açık adım varken koşunun bitmediğini, adımlar kapanınca bittiğini gösterir. Goal mode kapalı bir görevde de açık adım tek başına bitişi geçirmez (denetçi uyusa bile).

**P0-C. Kesilen koşunun devir notu, tıkanan, başarısız olan ve sahibin “devam” dediği koşuya da gelsin.** Karmaşıklık **M** (yeni defter yok; `resume.py` genişler).

`src/dotagent/agent/resume.py` bunu daemon yeniden başlayınca, kapanınca veya `dotx` süreci kesilince zaten yapar. `resume_note` hedefi, kesilen adımdaki çağrıları (`interrupted_calls`) ve planı (`plan_lines`) yeni koşunun ilk mesajına koyar. `can_resume` yalnızca `cut_off_by` ile kesilen koşuyu alır (`daemon_restarted`, `daemon_shutdown`, `process_ended`, `process_died`). `blocked`, başarısız biten ve sahibin “devam” dediği koşu bu kapının dışındadır. Sıkıştırma sonrası kimlikler ayrı durur: `agent/continuity.py` içindeki `RunLedger`.

İş, sıfırdan bir el değiştirme veya ikinci bir depo yazmak değildir. `resume_note` / `Resumption` aynı iplikte şu hallere uzanır: koşu `blocked` veya başarısız bitti, sahip “devam” dedi. Sıkıştırmadan sonra açık plan ve ledger kimlikleri bu notta kalır. Sistem isteminin baytları değişmez. Kampanyanın `carried` yolu bozulmaz. `scheduled`, kampanyanın kendi oturumuna bırakılır; bu dilim ona ikinci bir not eklemez.

Başarı: birim testi bugünkü kesilme notunu olduğu gibi korur. Aynı test, `blocked`, başarısız ve “devam” için notun hedefi, son adımdaki çağrıları ve planı taşıdığını gösterir. Sıkıştırılmış iplikte bu not modelden önce durur.

**P0-D. Günlükte bırakılan MCP aracı, doctor ve hizmet durumunda da görünsün.** Karmaşıklık **S**.

Bugün günlükte var, ekranda yok. `usable_tools` (`mcp/hub.py`) şeması düzleşmeyen aracı tek başına bırakır ve `log.warning("mcp_tool_left_out", ...)` yazar; sunucunun diğer araçları hizmete devam eder. `allow` listesindeki araç veya hiç kullanılamayan sunucu zaten bütün sunucuyu düşürür; o halde arayüz “Left out” der. Tek araç için bu rozet yoktur (`routes_mcp.py`: `left_out`, sunucu `hub.connected()` dışında kalınca). `doctor.py` `mcp_checks` komut ve sırlara bakar, araç adına bakmaz.

Dokunulacaklar: doctor satırı (`doctor.py`, `cli_doctor.py`) ve hizmet durumu (`server/routes_mcp.py`, bağlayıcı görünümü `server/static/js/views/connectors.js`). `usable_tools` yeniden yazılmaz; günlük satırı ekrana taşınır. Modelin sistemine sahte bir araç eklenmez.

Başarı: düzleşmeyen şemalı bir araç kayıt defterinde yoktur, `mcp_tool_left_out` günlüğe yazılmaya devam eder, ve aynı ad ile neden `dotx doctor` çıktısında ve hizmet durumunda durur. Sağlam araçlar çağrılmaya devam eder. Bütün sunucunun “Left out” hali olduğu gibi kalır.

P0 sırası: A ve B birlikte bir koşunun “bitirmeden önce işi sürdür” hali. C, `resume.py` notunu kesilme dışındaki devam hallerine uzatır. D, sahip MCP bağlayınca günlükte kalan aracı doctor ve hizmet durumunda görünür kılar. Dördü de politika kipini değiştirmez.

### P1 — bağlandıktan sonra güç

**P1-A. Bölüm araması.** Süreklilik notu ve koşu sonucu, mevcut gömme yoluyla `memory_search` ile aynı sıraya girer veya yanında `episode_search` olur. Karmaşıklık **M**. Başarı: birim testi, gömme kapalıyken kelime, açıkken yakın not, kirli bölümün enjeksiyon kuralına uyduğunu gösterir. Ayrı vektör hizmeti yok.

**P1-B. Eklenti MCP’si yeniden başlatmadan kalksın.** `PluginShelf` bugün beceriyi yeniler, süreci kaldırmaz. `McpHub.connect` zaten vardır. Karmaşıklık **M** (süreç ömrü, ad çakışması `mcp/naming.py`, başarısız süreç diğer sunucuları düşürmemeli). Başarı: testte bir eklenti `.mcp.json` ile shelf doldurulunca hub’da sunucu vardır; kaldırma süreci keser. Galeri hâlâ MCP kurmaz.

**P1-C. Araç sayısı eşiğinde gruplama.** `tool_surface = full` kalsın; bağlı MCP araç sayısı eşiği geçince o koşu gruplu yüzeye düşsün veya MCP grupları `use_tools` gerisinde kalsın. Eşik yapılandırmada. Karmaşıklık **M**. Başarı: eşiğin altında tam liste, üstünde MCP grubu model isteyene kadar şemada yok. `dotx eval check` araç kümesi daraldığı için kırılırsa görevler çekirdek araçla geçer; kırılan görev düzeltilir, eşik sessizce kapatılmaz.

**P1-D. Goal mode açıkken takılma merdiveni.** `stall_escalation` genel varsayılanı kapalı kalabilir; goal mode koşusunda merdiven açılsın. Karmaşıklık **S**. Başarı: aynı hata `stall_limit` kadar tekrarlanınca koşu durur veya kullanıcıya sorar; tek seferlik hata durdurmaz. `docs/autonomy.md` veya `docs/limits.md`.

**P1-E. Beceri kontrol listesi plana yazılsın.** `skill` aracı, metinde Dot’un tanıdığı bir liste görürse `update_plan` ile aynı depoya adım yazar. Ajan yine araç çağırır. Karmaşıklık **M**. Başarı: bir beceri okununca planda adımlar vardır ve P0-B onları sözleşmeye bağlar. Liste yoksa plan değişmez.

**P1-F. MCP kaynağı, salt okunur.** `list_resources` ve tek bir `mcp_read_resource`. Yazma yok. Sonuç `mcp_result` gibi kirli çitlenir. Karmaşıklık **M**. Başarı: kaynak okumak `mcp_read` sayılır ve kirletir; sunucu kaynak sunmuyorsa araç açık hata döner. İstem şablonları bu dilimde yoktur.

### P2 — sahip isteyince

- Varlık grafı. Bölüm araması (P1-A) yetmezse. **L**.
- MCP istem şablonları. Kaynak okuma oturunca. **M**.
- MCP keşfi. Scout’un beceri tarama çubuğu yetmez; sunucu kod çalıştırır. Otomatik kurulum yok. **L** ve güvenlik incelemesi (`REVIEW.md`, “Always needs a human” yollarına değer).
- İkinci mesaj kanalı. iMessage kuyruk ve “onay kanaldan yanıtlanmaz” sınırı dururken Slack kopyası açmak MCP mesaj aracından sonra gelir. **L**.
- Harness `step_time` ve `adopt = auto`. Eval kazancı görülmeden açılmaz.
- Kod bitişinde zorunlu test komutu. Önce P0-A; denetçi bu kanıtı zaten okur. Hâlâ bitiş kanıtsız olursa goal check istemine bir cümle. Yeni doğrulayıcı süreci **yazılmaz**.

## 9. Okyanusu kaynatmamak

En küçük yol üç davranış değişikliği ve bir görünürlük düzeltmesidir. Yeni ajan çerçevesi, yeni veritabanı, yeni tarayıcı, yeni kanal, yeni planlayıcı hizmeti yoktur.

1. Görev goal mode ile başlar (P0-A). Bitirme denetçisi uyanır. Test, derleme ve dokunulmamış yol kanıtı bugün kodda durur; kapalı bayrağın arkasındadır.
2. Açık plan adımı iş bitmiş sayılmaz (P0-B). Model plan yazdığı anda sözleşme başlar.
3. Sahibin “devam” dediği, tıkanan veya başarısız biten koşu, kesilen koşunun gördüğü notu görür (P0-C). Not `resume.py` içindedir; sıfırdan yazılmaz. Sıkıştırmadan sonra da açık plan bu notta kalır.
4. Şeması yüzünden bırakılan MCP aracı günlükte vardır, ekranda yoktur (P0-D). Aynı ad doctor çıktısına ve hizmet durumuna gelir. Bağlayıcı sayısı 142 şablon ve 156 karttır. “Left out” bütün sunucu içindir; tek araç ona karışmaz.

Bunlar oturunca ilk güçlendirme bölüm araması (P1-A) ve eklenti MCP’sinin sıcak kalkmasıdır (P1-B). İkisi de mevcut hub ve bellek gömmesini kullanır.

Yapılmayacaklar:

- Döngüyü (`run_agent`) başka bir çalışma zamanına taşımak.
- Belleği dış bir vektör veritabanına taşımak.
- `approvals` varsayılanını `yolo` veya `auto` yapmak. İnisiyatifi özerklik diye açmak (`[initiative] approvals = "yolo"`).
- Galeriye MCP kurulumu eklemek veya scout’un sunucu kurmasını sağlamak.
- Harness’ın çekirdek istemi veya politikayı yeniden yazması.
- Araç yüzeyi, kum havuzu ve kabuk korumasını “daha çok iş yapsın” diye gevşetmek. Sınırlar `docs/limits.md` ve `docs/safety.md` içindedir.
- Ofis, ticaret, ses veya üç boyutlu ofis arayüzünü bu yola bağlamak.

İlk uygulama dilimi, bir sahip görevinde ölçülür: goal mode anahtarına basmadan verilen bir `task`, erken `finish` ile kapanmaz; açık plan adımı varken kapanmaz; şeması düzleşmeyen MCP aracı doctor’da ve hizmet durumunda adıyla durur (günlük satırı durur). Bu üçü geçmeden P1’e geçilmez.

## Ek: giriş noktaları

| Konu | Yer |
| --- | --- |
| Çalışma zamanı | `src/dotagent/runtime.py` (`build_runtime`, `_open_services`) |
| Döngü | `src/dotagent/agent/loop.py` (`run_agent`) |
| Koşu ömrü | `src/dotagent/agent/manager.py` (`RunManager`) |
| Araç yürütme | `src/dotagent/agent/executor.py` (`execute_calls`) |
| Goal check | `src/dotagent/agent/review.py` |
| İlerleme | `src/dotagent/agent/progress.py` |
| Süreklilik | `src/dotagent/agent/continuity.py`, `agent/compact.py` |
| Kesilen koşunun devri | `src/dotagent/agent/resume.py` (`resume_note`, `can_resume`) |
| Onay | `src/dotagent/agent/approvals.py`, `agent/parking.py` |
| Politika | `src/dotagent/policy/engine.py` (`decide`) |
| Araç kaydı | `src/dotagent/tools/registry.py`, `tools/base.py` |
| MCP | `src/dotagent/mcp/hub.py`, `connection.py`, `signin.py`, `catalogue.py` |
| Eklenti | `src/dotagent/plugins/catalog.py`, `gallery.py`, `scout.py` |
| Bağlayıcı sayfası | `src/dotagent/connections/directory.py` |
| Hesap API’si | `src/dotagent/connectors/`, `src/dotagent/oauth/` |
| Bellek | `src/dotagent/memory/service.py` (`prompt_memories`) |
| Beceriler | `src/dotagent/skills/`, `agent/playbooks.py`, `tools/skill.py` |
| Harness | `src/dotagent/harness/step.py` |
| Takvim | `src/dotagent/scheduler/runner.py` |
| Kanal | `src/dotagent/channels/` (iMessage) |
| Ölçüm | `src/dotagent/evals/`, `cli_eval.py` |
| Arayüz | `src/dotagent/server/static/js/views/`, `desktop/` |
| Varsayılanlar | `src/dotagent/defaults.toml` |
| Sözleşmeler | `docs/autonomy.md`, `docs/safety.md`, `docs/extending.md`, `docs/memory-and-schedules.md`, `docs/harness.md`, `docs/limits.md` |

## Bilinçli olarak kilitlenmeyenler

- `policy/engine.py` dallarının tamamı yürütülmedi. Sıra `decide` ve `decide_past_review` üzerindedir.
- Olay kuyruğu taşma eşiğinin sayısal değeri burada sabitlenmedi (`events.py`).
- Galeri eklenti sayısı `docs/extending.md` ile yazıldı (22 / 201). Katalog sayısı kodda sayıldı (142 hizmet, 156 kart).
- `mark_plan_step` durum kümesinin her değeri yeniden tasarlanmadı. P0-B mevcut kapama araçlarını kullanır; eksik bir “bırakıldı” hali ancak test bunu gerektirirse eklenir.
- Canlı sağlayıcı, tarayıcı ve Mac uygulaması bu incelemede çalıştırılmadı.
