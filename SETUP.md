# Haftalık Piyasa Radarı — otomasyon kurulumu

Bu repo, `index.html`'i her Pazartesi otomatik olarak güncelleyen bir GitHub
Actions workflow'u içerir: `.github/workflows/weekly-radar.yml`.

## Yapman gereken tek şey

Reponun **Settings → Secrets and variables → Actions → New repository secret**
kısmından şu secret'ı ekle:

- **Name:** `ANTHROPIC_API_KEY`
- **Value:** kendi Anthropic API key'in (console.anthropic.com üzerinden alınır)

Bu key'i asla bir chat/sohbet ortamına yapıştırma — sadece GitHub'ın secret
formuna gir, orada şifrelenmiş saklanır ve workflow log'larında görünmez.

Başka bir şey yapmana gerek yok. Push işlemini workflow, GitHub'ın kendi
otomatik `GITHUB_TOKEN`'ıyla yapıyor (repo ayarlarında zaten mevcut,
`permissions: contents: write` workflow dosyasında tanımlı).

## Nasıl çalışır

1. Her Pazartesi 09:00 (İstanbul saati) tetiklenir; **Actions → Haftalık
   Piyasa Radarı → Run workflow** ile istediğin zaman elle de çalıştırabilirsin.
2. `scripts/generate_radar.py`:
   - Mevcut `index.html`'den sayı numarasını ve dönem tarihlerini okur.
   - Mevcut sayıyı `archive/edition-N.html` olarak arşivler (GitHub Pages'te
     kendi kalıcı linkine sahip olur: `https://nbeyter.github.io/radar/archive/edition-N.html`).
   - Anthropic API'yi web araması (`web_search` tool) ile çağırıp o haftanın
     Türkiye + global perakende elektrik piyasası gelişmelerini araştırır.
   - Aynı tasarım şablonuyla (`scripts/template.html`) yeni `index.html`'i üretir.
3. Workflow değişiklikleri `radar-bot` adına commit'leyip push eder.

## Bir şeyler ters giderse

Araştırma adımı başarısız olursa (API hatası, JSON parse hatası, vb.) script
kasıtlı olarak **hata ile durur** ve `index.html`'e dokunmaz — böylece bozuk
veya boş bir sayı hiç yayınlanmaz. Bu durumda GitHub, workflow'un başarısız
olduğuna dair repo'yu izleyenlere otomatik bir e-posta gönderir (Actions
sekmesinde de kırmızı ile görünür). Hatayı görmek için: **Actions** sekmesi →
ilgili run → **generate** job log'u.

## Tasarımla ilgili not

`scripts/template.html`, bu konuşmada üst yönetim geri bildirimiyle
sadeleştirilen en güncel tasarımı yansıtır (masthead'deki istatistik
kutucukları satırı kaldırılmıştı). İleride tasarımda değişiklik istersen
`scripts/template.html`'i düzenlemen yeterli — script her hafta o dosyayı
kullanır.

## Güvenlik notu

Bu otomasyonu kurarken repoya tek seferlik push için bir GitHub Personal
Access Token kullanıldı. Kurulum tamamlandıktan sonra o token'ı
**Settings → Developer settings → Personal access tokens** üzerinden
**revoke/regenerate** etmen önerilir — workflow artık ona ihtiyaç duymuyor,
her şeyi otomatik `GITHUB_TOKEN` ile yapıyor.
