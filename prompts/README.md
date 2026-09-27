# Pozivi

Vsi pozivi, ki jih sistem pošlje jezikovnim modelom, po korakih analize.
Oznake v zavitih oklepajih (npr. `{claim}`, `{prev_pass}`) koda ob klicu
nadomesti z dejanskimi podatki. Pri slovenski analizi je vsakemu pozivu dodano
še navodilo, naj bo besedilo v izhodu slovensko.

| mapa | korak |
|---|---|
| `1_izluscanje/` | izluščanje argumentov: sistemski poziv, pravila o udeležencih, navodilo, namig iz naslova posnetka |
| `2_zmote/` | zaznava logičnih zmot |
| `3_preverjanje_dejstev/` | izbira trditev, razgradnja, zbiralci (splet, Grok, Perplexity), razsodba in merila razsodbe |
| `4_zavrnitve/` | preslikava odgovorov in izmikanj |
| `5_sinteza/` | sinteza (razprava in en govorec) |
