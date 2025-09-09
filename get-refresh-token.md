# Home Assistant – ideiglenes megoldás a Kia/Hyundai EU hibára (refresh_token)

Az alábbi lépések a hivatalos beszélgetésben leírt ideiglenes kerülőutat követik: <https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/1277>.
Ez NEM végleges megoldás. Mielőtt bármit módosítasz, készíts biztonsági mentést a Home Assistant `config/` tartalmáról.

---

## 1) refresh_token megszerzése (ideiglenes segédscript)

### Grafikus környezet (böngészőt nyit):
  -  Használd a repo-ban található [get_token.py](https://gist.github.com/fuatakgun/fa4ef1e1d48b8dca2d22133d4d028dc9) scriptet (Selenium + Requests)

### Környezet előkészítése
Linux/macOS:
```bash
python3 -m venv .venv
. .venv/bin/activate
pip3 install selenium requests
```
Windows (PowerShell):
```powershell
py -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install selenium requests
```

### Script futtatása
Linux/macOS:
```bash
python3 get_token.py
```
Windows:
```powershell
py get_token.py
```
Kövesd az utasításokat, jelentkezz be a Kia/Hyundai fiókoddal. A folyamat végén **másold ki a refresh_token értéket** és tedd el biztonságos helyre.

---

## 2) Módosított KiaUvoApiEU.py használata (átmeneti)
Az issue ideiglenes megoldása szerint a [KiaUvoApiEU.py](https://gist.github.com/marvinwankersteen/af92c571881ac76579a037fac4f3a63a#file-kiauvoapieu-py) módosított változata szükséges.

- Töltsd le az issue-ban hivatkozott módosított fájlt (pl. gist link), majd másold be a Home Assistant környezetbe.
- Példa elhelyezési útvonal (HA Core/HASS OS):
  - Ha egyedi komponensként használod: `/config/custom_components/kia_uvo/KiaUvoApiEU.py`
  - Ha a Python site-packages-be kell másolni (konténeren belül):

Linux/macOS példa konténeres másolásra:
```bash
docker ps | grep homeassistant
# Jegyezd meg a konténer nevét, pl. "homeassistant"
# Nyiss shellt a konténerben
docker exec -it homeassistant /bin/bash
# (A pontos path Python verziótól függhet)
ls -la /usr/local/lib/python*/site-packages/hyundai_kia_connect_api/
# Másolás a hostról a konténerbe (példa):
docker cp ./KiaUvoApiEU.py homeassistant:/usr/local/lib/python3.11/site-packages/hyundai_kia_connect_api/KiaUvoApiEU.py
```

Megjegyzés: HACS frissítés felülírhatja a fájlt. Frissítés után szükség lehet az ismételt cserére.

---

## 3) Home Assistant konfiguráció – refresh_token beállítása
A refresh_token-t a jelenlegi ideiglenes workaround szerint az integráció bejegyzésében a `password` mezőbe kell helyettesíteni.

- Fájl: `/config/.storage/core.config_entries`
- Nyisd meg (VSCode Add-on, vagy konténeren belül `nano`/`vi`).
- Keresd meg a `hyundai_kia_connect_api` / `kia_uvo` integrációhoz tartozó bejegyzést.
- A `data.password` értékét cseréld a korábban kinyert `refresh_token`-re.

Példa részlet:
```json
"data": {
  "username": "valaki@example.com",
  "password": "<IDE_ÍRD_A_REFRESH_TOKEN-T>"
}
```

Opcionális: Eltávolíthatod és újra felveheted az integrációt (nem újratelepítés), és a „jelszó” mezőbe a refresh_token-t írod.

---

## 4) Home Assistant újraindítása
- UI: Settings → System → Restart
- Vagy CLI-ből a konténer újraindításával.

---

## Biztonság
- A `refresh_token` érzékeny titok. **Soha ne** oszd meg, ne töltsd fel publikusan.
- Ha gyanús aktivitást tapasztalsz, **változtasd meg** a Kia/Hyundai fiók jelszavadat.
- Frissítések/új kiadások érkezhetnek – ez az ideiglenes megoldás később érvényét veszítheti.
