# Analiza razprav

Sistem za samodejno analizo argumentacije video razprav z velikimi jezikovnimi modeli. Izdelan je bil v okviru diplomske naloge na Fakulteti za računalništvo in informatiko Univerze v Ljubljani.

Sistem sprejme povezavo do posnetka na YouTubu ali naloženo datoteko in izdela prepis z ločevanjem govorcev. Iz prepisa nato izlušči argumente s premisami, zazna logične zmote, preveri dejstvene trditve v zunanjih virih, poveže odgovore z napadenimi argumenti in napiše povzetek. Izid je prikazan v spletnem vmesniku, kjer ga je mogoče ročno urediti in izvoziti v PDF. Podprta sta nastop enega govorca in razprava dveh govorcev, vmesnik in poročilo pa sta na voljo v slovenščini in angleščini.

## Zgradba

| Del | Datoteka | Naloga |
|---|---|---|
| vmesnik REST | `api.py` | sprejema zahteve, zažene opravilo v ločeni niti, vrača stanje in izide |
| prenos | `youtube_downloader.py` | metapodatki in prenos zvoka z orodjem yt-dlp |
| prepis | `transcribe.py` | prepis z ločevanjem govorcev (`gpt-4o-transcribe-diarize`) |
| analiza | `debate_analyzer.py` | koraki 1, 2, 4 in 5: izluščanje argumentov, zmote, odgovori in izmikanja, sinteza |
| preverjanje dejstev | `fact_checker.py` | korak 3: izbira trditev, iskalni moduli, seznam virov, razsodba |
| sheme | `llm_schemas.py` | preverjanje oblike odgovorov modelov in zaprte množice vrednosti |
| hramba | `database.py` | baza SQLite: uporabniki, seje, analize |
| izvoz | `pdf_export.py` | poročilo v obliki PDF |
| nastavitve | `config.yaml`, `config_loader.py` | modeli, iskalni moduli, omejitve |
| pozivi | `prompts/` | vsi pozivi, razvrščeni po korakih (glej `prompts/README.md`) |
| odjemalec | `frontend/` | spletni vmesnik v ogrodju React (Vite) |
| testi | `tests/test_core.py` | enotski testi |

## Zahteve

- Python 3.10 ali novejši (razvito na 3.10.11)
- Node.js 18 ali novejši in npm
- ffmpeg, dosegljiv v sistemski poti (`PATH`)
- ključi API za OpenAI in Anthropic, neobvezno še za Perplexity, xAI in Google Fact Check

## Namestitev

Zaledni sistem (Windows):

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Na Linuxu ali macOS je ukaz za aktivacijo `source venv/bin/activate`.

Odjemalec:

```bash
cd frontend
npm install
```

## Ključi API

Datoteko `.env.example` skopiraj v `.env` in vpiši ključe.

| Spremenljivka | Obvezna | Uporaba |
|---|---|---|
| `OPENAI_API_KEY` | da | prepis, spletno iskanje, izbira in razgradnja trditev, angleške ključne besede |
| `ANTHROPIC_API_KEY` | da | koraki analize in razsodba |
| `PERPLEXITY_API_KEY` | ne | iskalni modul Perplexity |
| `XAI_API_KEY` | ne | iskalni modul Grok |
| `GOOGLE_FACTCHECK_API_KEY` | ne | iskalni modul Google Fact Check |
| `OPENALEX_EMAIL` | ne | e-naslov v zahtevkih za OpenAlex in Crossref |
| `ADMIN_SECRET` | ne | skrivnost v glavi `x-admin-secret` za skrbniške končne točke |
| `CORS_ORIGINS` | ne | dovoljeni izvori odjemalca, privzeto `http://localhost:3000,http://localhost:5173` |
| `RATE_LIMIT_MAX` | ne | največ analiz na uporabnika v 24 urah, privzeto 3, vrednost 0 omejitev izklopi |

Brez ključa Perplexity in Grok ne stečeta, Google Fact Check pa ne vrne zadetkov. Preverjanje dejstev se nadaljuje z ostalimi iskalnimi moduli.

## Zagon

Na Windows `run_server.bat` v ločenih oknih zažene zaledni sistem na vratih 8000 in odjemalca na vratih 5173. Če je nameščen ngrok, zažene še tunel do odjemalca.

Ročni zagon:

```bash
# zaledni sistem
uvicorn api:app --host 0.0.0.0 --port 8000

# odjemalec (v drugem terminalu)
cd frontend
npm run dev
```

Aplikacija je nato dosegljiva na `http://localhost:5173`. Odjemalec zahteve na `/api` preusmeri na zaledni sistem na vratih 8000.

Za zagon brez razvojnega strežnika Vite odjemalca zgradi z `npm run build` in vsebino mape `frontend/dist` skopiraj v mapo `static/` v korenu projekta. Zaledni sistem jo nato streže sam.

## Uporabniki in krediti

Ob prvi uporabi se je treba registrirati v vmesniku. Uporabnik z oznako 1, torej prvi registrirani, ob vsakem zagonu strežnika dobi skrbniške pravice in 100 kreditov. Vsak nov uporabnik ima en kredit, ena analiza pa porabi en kredit. Kredite dodeli skrbnik prek končne točke `POST /admin/credits`.

## Nastavitve

Pomembnejše nastavitve v `config.yaml`:

| Ključ | Pomen |
|---|---|
| `pipeline.max_recording_minutes` | najdaljši posnetek oziroma odsek, ki ga sistem sprejme |
| `analysis.pass_models` | model za posamezni korak analize |
| `fact_checking.engines` | vklop in izklop posameznih iskalnih modulov |
| `fact_checking.parallel_workers` | število trditev, ki se preverjajo hkrati |
| `fact_checking.judge_model` | model za razsodbo |
| `fact_checking.cache.enabled` | predpomnilnik odgovorov na disku (pri meritvah izklopljen) |

Način (en govorec ali razprava) in jezik analize uporabnik izbere v vmesniku ob oddaji posnetka.

## Podatki

- `data/debates.db`: baza SQLite z uporabniki, sejami in analizami
- `transcripts/`: prepisi končanih analiz, iz katerih bere ponovna analiza
- `jobs/<oznaka opravila>/`: delovne mape opravil; zvok in vmesne datoteke se izbrišejo eno uro po koncu opravila
- `.cache/fact_check/`: predpomnilnik preverjanja dejstev

## Testi

```bash
pip install pytest
pytest -q
```

## Licenca

Izvorna koda je ponujena pod licenco GNU General Public License, različica 3 ali novejša.
